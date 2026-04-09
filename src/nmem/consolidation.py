"""
Background consolidation engine — the reflection engine.

Three trigger modes:
  1. Micro-cycles  — reactive, triggered by high-importance journal entries
  2. Full cycles   — every N hours (default 6), runs all consolidation steps
  3. Nightly synthesis — daily, extracts cross-cutting patterns from journal

Consolidation steps:
  1. Decay expired journal entries (delete low-importance, promote high-importance)
  2. Promote high-importance / high-access journal entries to LTM
  3. Deduplicate similar LTM entries (union-find clustering + LLM merge)
  4. Decay confidence on stale LTM entries
  5. Run custom hooks (application-specific steps)
  6. Decay curiosity signal scores
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Callable, Awaitable

from sqlalchemy import select, func, text as sa_text

from nmem.db.models import (
    JournalEntryModel,
    LTMModel,
    SharedKnowledgeModel,
    CuriositySignalModel,
)
from nmem.search import cosine_similarity
from nmem.types import ConsolidationStats

if TYPE_CHECKING:
    from nmem.db.session import DatabaseManager
    from nmem.config import NmemConfig
    from nmem.providers.embedding.base import EmbeddingProvider
    from nmem.providers.llm.base import LLMProvider

logger = logging.getLogger(__name__)

# Category inference from title (used during journal→LTM promotion)
_CATEGORY_KEYWORDS = {
    "procedure": ["step", "process", "workflow", "how to", "guide"],
    "lesson": ["learned", "lesson", "mistake", "avoid", "never"],
    "pattern": ["pattern", "trend", "recurring", "always", "consistently"],
    "policy": ["policy", "rule", "must", "required", "mandate"],
    "contact": ["contact", "email", "phone", "reached out", "spoke with"],
    "troubleshooting": ["error", "fix", "bug", "issue", "resolved", "debug"],
}


def _infer_category(title: str) -> str:
    """Infer LTM category from journal entry title."""
    title_lower = title.lower()
    for category, keywords in _CATEGORY_KEYWORDS.items():
        if any(kw in title_lower for kw in keywords):
            return category
    return "fact"


class Consolidator:
    """Background memory consolidation engine."""

    def __init__(
        self,
        db: DatabaseManager,
        config: NmemConfig,
        embedding: EmbeddingProvider,
        llm: LLMProvider,
    ):
        self._db = db
        self._config = config
        self._embedding = embedding
        self._llm = llm

        # Signal for reactive micro-cycles
        self._signal = asyncio.Event()
        self._signal_reason: str = ""

        # Custom consolidation hooks
        self._full_cycle_hooks: list[tuple[str, Callable[[], Awaitable[None]]]] = []
        self._nightly_hooks: list[tuple[str, Callable[[], Awaitable[None]]]] = []

        # Background task reference
        self._task: asyncio.Task | None = None
        self._running = False

        # Timing
        self._last_full_cycle: datetime | None = None
        self._last_micro: float = 0.0
        self._last_synthesis_date: object = None  # date object

    def signal(self, reason: str = "high_importance_entry") -> None:
        """Signal the consolidator to wake for a micro-cycle."""
        self._signal_reason = reason
        self._signal.set()

    def register_full_cycle_step(
        self, name: str, fn: Callable[[], Awaitable[None]]
    ) -> None:
        """Register a custom step for full consolidation cycles."""
        self._full_cycle_hooks.append((name, fn))

    def register_nightly_step(
        self, name: str, fn: Callable[[], Awaitable[None]]
    ) -> None:
        """Register a custom step for nightly synthesis."""
        self._nightly_hooks.append((name, fn))

    def start(self) -> asyncio.Task:
        """Start the background consolidation loop."""
        if self._task and not self._task.done():
            return self._task
        self._running = True
        self._task = asyncio.create_task(self._loop(), name="nmem-consolidator")
        logger.info(
            "Consolidator started (interval=%dh, synthesis=%02d:00 UTC)",
            self._config.consolidation.interval_hours,
            self._config.consolidation.nightly_synthesis_hour_utc,
        )
        return self._task

    def stop(self) -> None:
        """Stop the background consolidation loop."""
        self._running = False
        self._signal.set()
        if self._task:
            self._task.cancel()
            self._task = None
        logger.info("Consolidator stopped")

    # ── Full Cycle ───────────────────────────────────────────────────────

    async def run_full_cycle(self) -> ConsolidationStats:
        """Run a full consolidation cycle."""
        start = time.monotonic()
        stats = ConsolidationStats()
        logger.info("Starting full consolidation cycle")

        # Step 1: Decay expired journal entries
        deleted, promoted = await self._decay_expired_entries()
        stats.expired_deleted = deleted
        stats.expired_promoted = promoted

        # Step 2: Promote high-importance journal entries to LTM
        stats.promoted_to_ltm = await self._promote_important_entries()

        # Step 3: Deduplicate similar LTM entries
        stats.duplicates_merged = await self._dedup_similar_memories()

        # Step 4: Confidence decay on stale LTM
        stats.confidence_decayed = await self._update_confidence_scores()

        # Step 5: Custom hooks
        for name, fn in self._full_cycle_hooks:
            try:
                await fn()
            except Exception as e:
                logger.warning("Custom consolidation step '%s' failed: %s", name, e)

        # Step 6: Curiosity signal decay
        stats.curiosity_decayed = await self._decay_curiosity_signals()

        stats.duration_seconds = time.monotonic() - start
        self._last_full_cycle = datetime.utcnow()
        logger.info(
            "Full consolidation completed in %.1fs: expired_del=%d, expired_promo=%d, "
            "promoted=%d, deduped=%d, conf_decayed=%d, curiosity_decayed=%d",
            stats.duration_seconds, stats.expired_deleted, stats.expired_promoted,
            stats.promoted_to_ltm, stats.duplicates_merged,
            stats.confidence_decayed, stats.curiosity_decayed,
        )
        return stats

    # ── Micro Cycle ──────────────────────────────────────────────────────

    async def run_micro_cycle(self, reason: str = "") -> ConsolidationStats:
        """Run a fast micro-cycle (promotion pass only)."""
        cooldown = self._config.consolidation.micro_cycle_cooldown_minutes * 60
        now = time.monotonic()
        if now - self._last_micro < cooldown:
            logger.debug("Micro-cycle debounced (cooldown %ds)", cooldown)
            return ConsolidationStats()

        self._last_micro = now
        stats = ConsolidationStats()
        stats.promoted_to_ltm = await self._promote_important_entries()
        if stats.promoted_to_ltm:
            logger.info("Micro-cycle: promoted %d entries (trigger: %s)",
                        stats.promoted_to_ltm, reason)
        return stats

    # ── Nightly Synthesis ────────────────────────────────────────────────

    async def run_nightly_synthesis(self) -> ConsolidationStats:
        """Analyze the day's journal entries for cross-agent patterns.

        Uses LLM to extract 2-3 actionable patterns, saves them as
        shared knowledge, then retroactively boosts contributing entries.
        """
        stats = ConsolidationStats()
        since = datetime.utcnow() - timedelta(hours=24)
        min_entries = self._config.consolidation.nightly_synthesis_min_entries

        # Gather entries grouped by agent and type
        async with self._db.session() as session:
            result = await session.execute(
                select(
                    JournalEntryModel.agent_id,
                    JournalEntryModel.entry_type,
                    func.count().label("count"),
                )
                .where(JournalEntryModel.created_at >= since)
                .group_by(JournalEntryModel.agent_id, JournalEntryModel.entry_type)
            )
            grouped = result.all()
            total_entries = sum(r[2] for r in grouped)

            if total_entries < min_entries:
                logger.debug("Nightly synthesis skipped: only %d entries (need %d)",
                             total_entries, min_entries)
                return stats

            # Get top high-importance entries for context
            result = await session.execute(
                select(
                    JournalEntryModel.agent_id,
                    JournalEntryModel.entry_type,
                    JournalEntryModel.title,
                    JournalEntryModel.importance,
                )
                .where(JournalEntryModel.created_at >= since)
                .where(JournalEntryModel.importance >= 6)
                .order_by(JournalEntryModel.importance.desc())
                .limit(20)
            )
            top_entries = result.all()

        # Build synthesis prompt
        summary_lines = [f"Total entries: {total_entries}"]
        for agent_id, entry_type, count in grouped:
            summary_lines.append(f"  {agent_id}/{entry_type}: {count}")

        entry_lines = [
            f"  [{imp}] {aid}/{etype}: {title}"
            for aid, etype, title, imp in top_entries
        ]

        context = (
            "ACTIVITY SUMMARY (last 24h):\n" + "\n".join(summary_lines) +
            "\n\nTOP ENTRIES:\n" + "\n".join(entry_lines)
        )

        system_prompt = (
            "You are analyzing today's operational activity across a multi-agent "
            "system. Identify 2-3 actionable patterns or trends.\n\n"
            "For each pattern:\n"
            "- What you observe (be specific, cite numbers)\n"
            "- Why it matters\n"
            "- What should change\n\n"
            'Respond as JSON: {"patterns": [{"observation": "...", '
            '"significance": "...", "recommendation": "..."}]}'
        )

        try:
            result = await self._llm.complete_json(
                system_prompt, context,
                max_tokens=self._config.llm.synthesis_max_tokens,
                temperature=0.3, timeout=30.0,
            )

            if not result or not isinstance(result, dict) or not result.get("patterns"):
                logger.debug("Synthesis produced no patterns")
                return stats

            # Save patterns as shared knowledge
            from nmem.search import populate_tsvector

            patterns = result["patterns"][:3]
            for i, pattern in enumerate(patterns):
                obs = pattern.get("observation", "")
                sig = pattern.get("significance", "")
                rec = pattern.get("recommendation", "")
                if not obs:
                    continue

                content = f"Pattern: {obs}\nSignificance: {sig}\nRecommendation: {rec}"
                key = f"daily_synthesis_{datetime.utcnow().strftime('%Y%m%d')}_{i + 1}"
                emb = await asyncio.to_thread(
                    self._embedding.embed, f"{obs} {sig}"[:500]
                )

                async with self._db.session() as session:
                    record = SharedKnowledgeModel(
                        key=key, content=content, category="daily_synthesis",
                        created_by="consolidator", last_updated_by="consolidator",
                        importance=7, embedding=emb,
                    )
                    session.add(record)
                    await session.flush()
                    await populate_tsvector(
                        self._db, "nmem_shared_knowledge", record.id,
                        f"{key} {content[:2000]}",
                    )

                stats.patterns_synthesized += 1

            # Retroactive significance: boost journal entries that contributed
            await self._retroactive_boost(patterns, since)

            logger.info("Nightly synthesis: %d patterns from %d entries",
                        stats.patterns_synthesized, total_entries)

        except Exception as e:
            logger.error("Nightly synthesis failed: %s", e)

        # Custom nightly hooks
        for name, fn in self._nightly_hooks:
            try:
                await fn()
            except Exception as e:
                logger.warning("Custom nightly step '%s' failed: %s", name, e)

        self._last_synthesis_date = datetime.utcnow().date()
        return stats

    # ── Consolidation Steps ──────────────────────────────────────────────

    async def _decay_expired_entries(self) -> tuple[int, int]:
        """Delete expired journal entries with low importance. Promote high-importance ones."""
        promote_threshold = self._config.journal.auto_promote_importance
        access_threshold = self._config.journal.auto_promote_access_count
        now = datetime.utcnow()
        deleted = 0
        promoted = 0

        async with self._db.session() as session:
            result = await session.execute(
                select(JournalEntryModel).where(
                    JournalEntryModel.expires_at.isnot(None),
                    JournalEntryModel.expires_at < now,
                    JournalEntryModel.promoted_to_ltm == False,
                )
            )
            expired = result.scalars().all()

            for entry in expired:
                should_promote = (
                    entry.importance >= promote_threshold
                    or (entry.access_count or 0) >= access_threshold
                )
                if should_promote:
                    await self._promote_entry(entry)
                    promoted += 1
                else:
                    await session.delete(entry)
                    deleted += 1

        return deleted, promoted

    async def _promote_important_entries(self) -> int:
        """Promote journal entries that have earned long-term status."""
        promote_threshold = self._config.journal.auto_promote_importance
        access_threshold = self._config.journal.auto_promote_access_count
        promoted = 0

        async with self._db.session() as session:
            result = await session.execute(
                select(JournalEntryModel).where(
                    JournalEntryModel.promoted_to_ltm == False,
                    (
                        (JournalEntryModel.importance >= promote_threshold)
                        | (JournalEntryModel.access_count >= access_threshold)
                    ),
                ).limit(20)
            )
            entries = result.scalars().all()

        for entry in entries:
            await self._promote_entry(entry)
            promoted += 1

        return promoted

    async def _promote_entry(self, entry: JournalEntryModel) -> None:
        """Promote a single journal entry to LTM."""
        from nmem.search import populate_tsvector

        category = _infer_category(entry.title)
        key = entry.title[:200].replace(" ", "_").lower()
        content = f"[{entry.entry_type}] {entry.content}"

        emb = await asyncio.to_thread(
            self._embedding.embed, f"{key} {content[:500]}"
        )

        async with self._db.session() as session:
            # Check if key already exists for this agent
            existing = await session.execute(
                select(LTMModel).where(
                    LTMModel.agent_id == entry.agent_id,
                    LTMModel.key == key,
                )
            )
            row = existing.scalar_one_or_none()

            if row:
                row.content = content
                row.importance = max(row.importance, entry.importance)
                row.embedding = emb
                row.source = "promotion"
                row.source_journal_id = entry.id
                row.version += 1
                await session.flush()
                ltm_id = row.id
            else:
                ltm = LTMModel(
                    agent_id=entry.agent_id,
                    category=category,
                    key=key,
                    content=content,
                    importance=entry.importance,
                    source="promotion",
                    source_journal_id=entry.id,
                    embedding=emb,
                )
                session.add(ltm)
                await session.flush()
                ltm_id = ltm.id

        await populate_tsvector(
            self._db, "nmem_long_term_memory", ltm_id,
            f"{key} {content[:2000]}",
        )

        # Mark journal entry as promoted
        async with self._db.session() as session:
            result = await session.execute(
                select(JournalEntryModel).where(JournalEntryModel.id == entry.id)
            )
            row = result.scalar_one_or_none()
            if row:
                row.promoted_to_ltm = True

        logger.info("Promoted journal #%d (%s) to LTM for %s",
                     entry.id, entry.title[:40], entry.agent_id)

    async def _dedup_similar_memories(self) -> int:
        """Merge semantically duplicate LTM entries per agent.

        Clusters similar entries (cosine > threshold) using union-find,
        then merges each cluster via LLM into a single entry.
        """
        threshold = self._config.consolidation.similarity_merge_threshold
        max_clusters = 8
        merged_total = 0

        # Get distinct agent_ids with validated LTM entries
        async with self._db.session() as session:
            result = await session.execute(
                select(LTMModel.agent_id)
                .where(LTMModel.status == "validated")
                .group_by(LTMModel.agent_id)
            )
            agent_ids = [r[0] for r in result.all()]

        for agent_id in agent_ids:
            if merged_total >= max_clusters:
                break

            # Find all similar pairs via pgvector cosine distance
            async with self._db.session() as session:
                try:
                    result = await session.execute(
                        sa_text("""
                            SELECT a.id, b.id,
                                   1 - (a.embedding <=> b.embedding) AS similarity
                            FROM nmem_long_term_memory a
                            JOIN nmem_long_term_memory b ON a.agent_id = b.agent_id
                                AND a.id < b.id
                                AND a.status = 'validated' AND b.status = 'validated'
                                AND a.embedding IS NOT NULL AND b.embedding IS NOT NULL
                            WHERE a.agent_id = :agent_id
                                AND 1 - (a.embedding <=> b.embedding) > :threshold
                            ORDER BY similarity DESC
                            LIMIT 50
                        """),
                        {"agent_id": agent_id, "threshold": threshold},
                    )
                    pairs = result.all()
                except Exception as e:
                    logger.debug("Dedup pair search failed for %s: %s", agent_id, e)
                    continue

            if not pairs:
                continue

            # Union-find to build clusters
            parent: dict[int, int] = {}

            def find(x: int) -> int:
                while parent.get(x, x) != x:
                    parent[x] = parent.get(parent[x], parent[x])
                    x = parent[x]
                return x

            def union(a: int, b: int) -> None:
                ra, rb = find(a), find(b)
                if ra != rb:
                    parent[ra] = rb

            for id_a, id_b, _sim in pairs:
                union(id_a, id_b)

            # Group into clusters
            all_ids = set()
            for id_a, id_b, _ in pairs:
                all_ids.add(id_a)
                all_ids.add(id_b)
            for nid in parent:
                all_ids.add(nid)

            clusters: dict[int, set[int]] = {}
            for node_id in all_ids:
                root = find(node_id)
                clusters.setdefault(root, set()).add(node_id)

            # Only process clusters with 2+ members
            clusters = {k: v for k, v in clusters.items() if len(v) >= 2}

            for cluster_ids in clusters.values():
                if merged_total >= max_clusters:
                    break

                async with self._db.session() as session:
                    result = await session.execute(
                        select(LTMModel)
                        .where(LTMModel.id.in_(cluster_ids))
                        .where(LTMModel.status == "validated")
                        .order_by(LTMModel.importance.desc())
                    )
                    entries = result.scalars().all()

                if len(entries) < 2:
                    continue

                keeper = entries[0]
                redundant = entries[1:]

                # Build merge prompt
                entry_texts = [
                    f"Entry {i}:\n{e.content[:400]}"
                    for i, e in enumerate(entries, 1)
                ]

                try:
                    merged_text = await self._llm.complete(
                        "You merge duplicate knowledge entries. Output only the merged text.",
                        f"Merge these {len(entries)} memory entries into one concise entry.\n"
                        f"Keep all unique facts. Remove redundancy. Max 200 words.\n\n"
                        + "\n\n".join(entry_texts) + "\n\nMerged entry:",
                        max_tokens=512, temperature=0.2, timeout=20.0,
                    )
                    merged_text = re.sub(r"<think>[\s\S]*?</think>\s*", "", merged_text).strip()

                    if not merged_text or len(merged_text) < 20:
                        continue

                    # Re-embed merged content
                    new_emb = await asyncio.to_thread(
                        self._embedding.embed, f"{keeper.key} {merged_text[:500]}"
                    )
                    embedding_str = f"[{','.join(str(x) for x in new_emb)}]"

                    async with self._db.session() as session:
                        # Update keeper
                        await session.execute(
                            sa_text("""
                                UPDATE nmem_long_term_memory
                                SET content = :content, importance = :importance,
                                    embedding = CAST(:embedding AS vector),
                                    version = version + 1, updated_at = NOW(),
                                    source = 'consolidation'
                                WHERE id = :id
                            """),
                            {"content": merged_text, "importance": keeper.importance,
                             "embedding": embedding_str, "id": keeper.id},
                        )

                        # Mark redundant as superseded
                        redundant_ids = [e.id for e in redundant]
                        for rid in redundant_ids:
                            await session.execute(
                                sa_text("""
                                    UPDATE nmem_long_term_memory
                                    SET status = 'superseded', supersedes_id = :keeper_id,
                                        updated_at = NOW()
                                    WHERE id = :id
                                """),
                                {"keeper_id": keeper.id, "id": rid},
                            )

                    merged_total += 1
                    logger.info("Merged %d LTM entries for %s → kept #%d",
                                len(entries), agent_id, keeper.id)

                except Exception as e:
                    logger.debug("Cluster merge failed: %s", e)

        return merged_total

    async def _update_confidence_scores(self) -> int:
        """Decay confidence of stale LTM entries."""
        staleness_days = self._config.ltm.staleness_days
        decay_rate_unaccessed = self._config.ltm.confidence_decay_rate
        decay_rate_stale = self._config.ltm.confidence_decay_rate_accessed
        min_confidence = self._config.ltm.min_confidence
        now = datetime.utcnow()
        stale_cutoff = now - timedelta(days=staleness_days)
        age_cutoff = now - timedelta(days=30)  # Don't decay brand new entries
        updated = 0

        async with self._db.session() as session:
            result = await session.execute(
                select(LTMModel).where(
                    LTMModel.confidence > min_confidence,
                    LTMModel.created_at < age_cutoff,
                    LTMModel.status == "validated",
                )
            )
            candidates = result.scalars().all()

            for entry in candidates:
                if entry.access_count and entry.access_count > 0:
                    if entry.last_accessed_at and entry.last_accessed_at < stale_cutoff:
                        entry.confidence = max(entry.confidence - decay_rate_stale, min_confidence)
                        updated += 1
                else:
                    entry.confidence = max(entry.confidence - decay_rate_unaccessed, min_confidence)
                    updated += 1

        return updated

    async def _decay_curiosity_signals(self) -> int:
        """Reduce composite_score for stale pending curiosity signals."""
        stale_threshold = datetime.utcnow() - timedelta(days=3)
        decay_rate = 0.15
        min_score = 0.1

        async with self._db.session() as session:
            result = await session.execute(
                select(CuriositySignalModel).where(
                    CuriositySignalModel.status == "pending",
                    CuriositySignalModel.created_at < stale_threshold,
                    CuriositySignalModel.composite_score > min_score,
                )
            )
            stale = result.scalars().all()

            decayed = 0
            for signal in stale:
                signal.composite_score = max(min_score, signal.composite_score * (1 - decay_rate))
                decayed += 1
                if signal.composite_score <= 0.2:
                    signal.status = "noise"

        return decayed

    async def _retroactive_boost(self, patterns: list[dict], since: datetime) -> None:
        """Boost importance of journal entries that contributed to discovered patterns."""
        boosted = 0
        for pattern in patterns:
            obs = pattern.get("observation", "")
            if not obs:
                continue

            pattern_emb = await asyncio.to_thread(self._embedding.embed, obs[:300])
            embedding_str = f"[{','.join(str(x) for x in pattern_emb)}]"

            try:
                async with self._db.session() as session:
                    related = await session.execute(
                        sa_text("""
                            SELECT id, importance, title
                            FROM nmem_journal_entries
                            WHERE created_at > :since
                              AND importance < 7
                              AND embedding IS NOT NULL
                              AND 1 - (embedding <=> CAST(:embedding AS vector)) > 0.75
                            ORDER BY embedding <=> CAST(:embedding AS vector)
                            LIMIT 5
                        """),
                        {"since": since, "embedding": embedding_str},
                    )
                    for row in related.all():
                        new_imp = min(row[1] + 2, 10)
                        await session.execute(
                            sa_text("""
                                UPDATE nmem_journal_entries
                                SET importance = :importance
                                WHERE id = :id
                            """),
                            {"importance": new_imp, "id": row[0]},
                        )
                        boosted += 1
            except Exception as e:
                logger.debug("Retroactive boost failed for pattern: %s", e)

        if boosted:
            logger.info("Retroactive significance: boosted %d journal entries", boosted)

    # ── Main Loop ────────────────────────────────────────────────────────

    async def _loop(self) -> None:
        """Main consolidation loop."""
        interval = self._config.consolidation.interval_hours * 3600
        cooldown = self._config.consolidation.micro_cycle_cooldown_minutes * 60

        # Wait 60s after startup before first run
        await asyncio.sleep(60)

        while self._running:
            try:
                try:
                    await asyncio.wait_for(self._signal.wait(), timeout=interval)
                    self._signal.clear()
                    if not self._running:
                        break
                    await self.run_micro_cycle(self._signal_reason)
                    await asyncio.sleep(cooldown)
                except asyncio.TimeoutError:
                    if not self._running:
                        break
                    await self.run_full_cycle()

                # Check if nightly synthesis is due
                now = datetime.utcnow()
                target_hour = self._config.consolidation.nightly_synthesis_hour_utc
                if now.hour == target_hour and self._last_synthesis_date != now.date():
                    await self.run_nightly_synthesis()

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Consolidation loop error: %s", e)
                await asyncio.sleep(60)
