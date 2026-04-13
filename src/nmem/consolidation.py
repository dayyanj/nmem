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
  4. Rescore auto-importance entries (heuristic, journal + LTM)
  5. Decay salience on stale LTM entries
  6. Run custom hooks (application-specific steps)
  7. Decay curiosity signal scores
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Callable, Awaitable

from sqlalchemy import or_, select, func, text as sa_text

from nmem.db.models import (
    JournalEntryModel,
    LTMModel,
    MemoryConflictModel,
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


# Heuristic importance scoring (Phase 2).
#
# The scorer is intentionally simple and fully deterministic:
#
#   base = record_type prior (0-8)
#     + grounding bonus (0-2)
#     + access velocity bonus (0-2)
#     - staleness penalty (0-1)
#
# Clamped to [1, 10]. Only runs on rows where `auto_importance=True`.
#
# Weights are hardcoded for now; Phase 5 (the profile system) will lift
# them into config so different domains can tune them.

_RECORD_TYPE_PRIORS: dict[str, int] = {
    "evidence": 6,
    "lesson": 7,
    "lesson_learned": 7,
    "decision": 6,
    "rule": 7,
    "policy": 7,
    "procedure": 6,
    "judgment": 5,
    "fact": 5,
    "observation": 4,
    "summary": 4,
    "task": 4,
    "preference": 4,
}

_GROUNDING_BONUS: dict[str, int] = {
    "source_material": 2,
    "confirmed": 2,
    "inferred": 0,
    "disputed": -1,
}


def _score_heuristic(
    *,
    record_type: str,
    grounding: str,
    access_count: int,
    age_days: float,
) -> int:
    """Deterministic heuristic importance score, 1-10.

    Inputs come straight off the row — no LLM, no embedding, no network.
    The function is free to call on every consolidation cycle.
    """
    base = _RECORD_TYPE_PRIORS.get(record_type or "", 5)
    base += _GROUNDING_BONUS.get(grounding or "", 0)

    # Access velocity: an entry that's been read often relative to its age
    # gets a bump. Clamped so brand-new high-access entries don't explode.
    if age_days > 0 and access_count > 0:
        velocity = access_count / max(age_days, 1.0)
        if velocity >= 1.0:
            base += 2
        elif velocity >= 0.3:
            base += 1

    # Staleness penalty for entries with zero access after their first week.
    if age_days > 7 and access_count == 0:
        base -= 1

    return max(1, min(10, base))


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

        # Knowledge link engine (set by MemorySystem)
        self._link_engine = None

        # Custom consolidation hooks
        self._full_cycle_hooks: list[tuple[str, Callable[[], Awaitable[None]]]] = []
        self._nightly_hooks: list[tuple[str, Callable[[], Awaitable[None]]]] = []

        # Background task reference
        self._task: asyncio.Task | None = None
        self._running = False

        # Timing
        self._last_full_cycle: datetime | None = None
        self._last_micro: float = -(60 * 60)  # Ensure first micro-cycle always runs
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

        # Step 1: Archive expired journal entries to LTM
        # Nothing is deleted — all expired entries flow to the cold tier.
        archived, _ = await self._decay_expired_entries()
        stats.expired_deleted = 0       # nothing is ever deleted
        stats.expired_promoted = archived  # all expired entries → LTM

        # Step 2: Promote high-importance journal entries to LTM
        stats.promoted_to_ltm = await self._promote_important_entries()

        # Step 3: Promote cross-agent LTM entries to shared knowledge
        stats.promoted_to_shared = await self._promote_ltm_to_shared()

        # Step 4: Deduplicate similar LTM entries
        stats.duplicates_merged = await self._dedup_similar_memories()

        # Step 5: Auto-importance rescoring (heuristic, respects auto_importance flag)
        stats.auto_importance_rescored = await self._score_auto_importance()

        # Step 6: Belief revision — resolve pending conflict rows
        auto_resolved, needs_review = await self._resolve_conflicts()
        stats.conflicts_auto_resolved = auto_resolved
        stats.conflicts_needs_review = needs_review

        # Step 7: Salience decay on stale LTM
        stats.salience_decayed = await self._update_salience_scores()

        # Step 8: Custom hooks
        for name, fn in self._full_cycle_hooks:
            try:
                await fn()
            except Exception as e:
                logger.warning("Custom consolidation step '%s' failed: %s", name, e)

        # Step 9: Build knowledge links
        if self._link_engine:
            try:
                stats.links_created = await self._link_engine.build_links()
            except Exception as e:
                logger.warning("Knowledge link building failed: %s", e)

        # Step 10: Curiosity signal decay
        stats.curiosity_decayed = await self._decay_curiosity_signals()

        stats.duration_seconds = time.monotonic() - start
        self._last_full_cycle = datetime.utcnow()
        logger.info(
            "Full consolidation completed in %.1fs: expired_del=%d, expired_promo=%d, "
            "promoted_ltm=%d, promoted_shared=%d, deduped=%d, rescored=%d, "
            "conflicts_auto=%d, conflicts_review=%d, salience_decayed=%d, curiosity=%d",
            stats.duration_seconds, stats.expired_deleted, stats.expired_promoted,
            stats.promoted_to_ltm, stats.promoted_to_shared, stats.duplicates_merged,
            stats.auto_importance_rescored, stats.conflicts_auto_resolved,
            stats.conflicts_needs_review, stats.salience_decayed, stats.curiosity_decayed,
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

            # Record LLM usage for token trends
            from nmem.token_stats import record_llm_usage
            await record_llm_usage(
                self._db, "synthesis",
                self._config.llm.synthesis_max_tokens,
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

        # Retrospective "dreamstate" — validate past lessons against new
        # outcome evidence. Runs after synthesis so the LLM is already warm.
        try:
            validated, disputed = await self._run_retrospective()
            stats.lessons_validated = validated
            stats.lessons_disputed = disputed
        except Exception as e:
            logger.error("Retrospective failed: %s", e)

        self._last_synthesis_date = datetime.utcnow().date()
        return stats

    # ── Consolidation Steps ──────────────────────────────────────────────

    async def _decay_expired_entries(self) -> tuple[int, int]:
        """Archive ALL expired journal entries to LTM.

        Nothing is ever deleted — knowledge flows from the hot tier (journal)
        to the cold tier (LTM). This mirrors how human memory works: episodic
        memories are gradually transferred to semantic memory, becoming less
        detailed but more durable. The dedup/merge pass in dreamstate handles
        corpus size management, not deletion.

        High-importance entries get full salience in LTM.
        Low-importance entries get reduced salience (rank lower in search
        but remain retrievable by association).
        """
        now = datetime.utcnow()
        archived = 0
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
                await self._promote_entry(entry)
                archived += 1

        # Return (archived, promoted) — archived replaces deleted.
        # Both go to LTM; "promoted" kept for backward compat in stats.
        return archived, 0

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

    async def _promote_ltm_to_shared(self) -> int:
        """Promote LTM entries to shared knowledge based on cross-agent access.

        An LTM entry is promoted when ALL of these are true:
          - importance >= shared_promote_importance (default 8)
          - accessed by >= shared_promote_min_agents distinct agents (default 2)
          - access_count >= shared_promote_min_access (default 3)
          - not already promoted to shared

        No LLM involved — promotion is driven entirely by observed
        cross-agent access patterns. The agents vote with their searches.
        """
        min_importance = self._config.ltm.shared_promote_importance
        min_agents = self._config.ltm.shared_promote_min_agents
        min_access = self._config.ltm.shared_promote_min_access
        promoted = 0

        async with self._db.session() as session:
            result = await session.execute(
                select(LTMModel).where(
                    LTMModel.promoted_to_shared == False,
                    LTMModel.status == "validated",
                    LTMModel.importance >= min_importance,
                    LTMModel.access_count >= min_access,
                    LTMModel.accessed_by_agents.isnot(None),
                ).limit(20)
            )
            candidates = result.scalars().all()

        for entry in candidates:
            agents = entry.accessed_by_agents or []
            if len(agents) < min_agents:
                continue

            # Promote to shared knowledge
            from nmem.search import populate_tsvector

            emb = await asyncio.to_thread(
                self._embedding.embed, f"{entry.key} {entry.content[:500]}"
            )

            async with self._db.session() as session:
                # Check if shared key already exists
                existing = await session.execute(
                    select(SharedKnowledgeModel).where(
                        SharedKnowledgeModel.key == entry.key
                    )
                )
                if existing.scalar_one_or_none():
                    # Already exists — just mark as promoted
                    ltm_row = await session.execute(
                        select(LTMModel).where(LTMModel.id == entry.id)
                    )
                    row = ltm_row.scalar_one_or_none()
                    if row:
                        row.promoted_to_shared = True
                    continue

                shared = SharedKnowledgeModel(
                    key=entry.key,
                    content=entry.content,
                    category=entry.category,
                    created_by=entry.agent_id,
                    last_updated_by="consolidator",
                    importance=entry.importance,
                    embedding=emb,
                    project_scope=getattr(entry, 'project_scope', None),
                )
                session.add(shared)
                await session.flush()
                shared_id = shared.id

            await populate_tsvector(
                self._db, "nmem_shared_knowledge", shared_id,
                f"{entry.key} {entry.content[:2000]}",
            )

            # Mark LTM entry as promoted
            async with self._db.session() as session:
                ltm_row = await session.execute(
                    select(LTMModel).where(LTMModel.id == entry.id)
                )
                row = ltm_row.scalar_one_or_none()
                if row:
                    row.promoted_to_shared = True

            promoted += 1
            logger.info(
                "Promoted LTM '%s' to shared (agents: %s, access: %d)",
                entry.key[:40], agents, entry.access_count,
            )

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

        scope = getattr(entry, 'project_scope', None)

        async with self._db.session() as session:
            # Check if key already exists for this agent + scope
            filters = [
                LTMModel.agent_id == entry.agent_id,
                LTMModel.key == key,
            ]
            if scope is not None:
                filters.append(LTMModel.project_scope == scope)
            else:
                filters.append(LTMModel.project_scope.is_(None))
            existing = await session.execute(select(LTMModel).where(*filters))
            row = existing.scalar_one_or_none()

            # Inherit auto_importance from the source journal entry. If the
            # author explicitly set the journal importance, the promoted LTM
            # row keeps that sovereignty forever.
            source_auto = getattr(entry, "auto_importance", True)

            if row:
                row.content = content
                row.importance = max(row.importance, entry.importance)
                if not source_auto:
                    row.auto_importance = False
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
                    auto_importance=source_auto,
                    source="promotion",
                    source_journal_id=entry.id,
                    embedding=emb,
                    project_scope=scope,
                )
                session.add(ltm)
                await session.flush()
                ltm_id = ltm.id

        await populate_tsvector(
            self._db, "nmem_long_term_memory", ltm_id,
            f"{key} {content[:2000]}",
        )

        # Compress journal entry to a stub with pointer to LTM.
        # The journal becomes a lightweight timeline index; full knowledge
        # lives in LTM. This avoids content duplication and keeps the
        # journal scannable.
        stub_content = await self._compress_to_stub(entry.title, content, key)

        async with self._db.session() as session:
            result = await session.execute(
                select(JournalEntryModel).where(JournalEntryModel.id == entry.id)
            )
            row = result.scalar_one_or_none()
            if row:
                row.promoted_to_ltm = True
                row.content = stub_content
                row.pointers = [{"type": "ltm", "id": ltm_id, "key": key}]

        logger.info("Promoted journal #%d (%s) to LTM for %s",
                     entry.id, entry.title[:40], entry.agent_id)

    async def _compress_to_stub(
        self, title: str, full_content: str, ltm_key: str,
    ) -> str:
        """Compress a journal entry to a stub pointing at its LTM archive.

        Uses the LLM to distill the content to a one-line summary.
        Falls back to title + truncation if LLM is unavailable.

        The stub serves as a timeline marker: "what happened" without
        "all the details" — those live in LTM now.
        """
        max_chars = 200

        # Try LLM compression
        system = (
            "Compress this into a single-line summary (max 200 chars). "
            "Keep the key fact, action, or decision. Output ONLY the summary."
        )
        user = f"{title}: {full_content[:800]}"
        try:
            result = await self._llm.complete(
                system, user, max_tokens=64, temperature=0.1, timeout=10.0,
            )
            stub = result.strip()
            if stub and len(stub) <= max_chars * 2:
                return stub[:max_chars]
        except Exception:
            pass

        # Fallback: title + truncation
        if len(title) > 10:
            return title[:max_chars]
        return f"{title}: {full_content[:max_chars - len(title) - 2]}"

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
                                AND a.project_scope IS NOT DISTINCT FROM b.project_scope
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
                                    SET status = 'superseded', superseded_by_id = :keeper_id,
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

    async def _score_auto_importance(self) -> int:
        """Rescore journal + LTM rows where `auto_importance=True`.

        Heuristic-only in v1 (see `_score_heuristic` for the function). Runs
        a bounded batch per cycle so a large backlog doesn't starve other
        consolidation steps. Rows with `auto_importance=False` are NEVER
        touched — author-set importance stays sovereign forever.
        """
        if not getattr(self._config, "importance", None) or not self._config.importance.enabled:
            return 0

        batch_size = self._config.importance.rescore_batch_size
        now = datetime.utcnow()
        rescored = 0

        # Rescore journal entries first (short-lived, high churn).
        async with self._db.session() as session:
            journal_rows = (
                await session.execute(
                    select(JournalEntryModel)
                    .where(JournalEntryModel.auto_importance.is_(True))
                    .order_by(JournalEntryModel.id.desc())
                    .limit(batch_size)
                )
            ).scalars().all()

            for row in journal_rows:
                age_days = (now - row.created_at).total_seconds() / 86400.0 if row.created_at else 0.0
                new_importance = _score_heuristic(
                    record_type=row.record_type or "",
                    grounding=row.grounding or "",
                    access_count=row.access_count or 0,
                    age_days=age_days,
                )
                if new_importance != row.importance:
                    row.importance = new_importance
                    row.relevance_score = min(new_importance / 10.0, 1.0)
                    rescored += 1

        # Now rescore LTM entries (long-lived, lower churn — use a smaller
        # slice so journal rescoring always completes).
        async with self._db.session() as session:
            ltm_rows = (
                await session.execute(
                    select(LTMModel)
                    .where(
                        LTMModel.auto_importance.is_(True),
                        LTMModel.status == "validated",
                    )
                    .order_by(LTMModel.id.desc())
                    .limit(batch_size)
                )
            ).scalars().all()

            for row in ltm_rows:
                age_days = (now - row.created_at).total_seconds() / 86400.0 if row.created_at else 0.0
                new_importance = _score_heuristic(
                    record_type=row.record_type or "",
                    grounding=row.grounding or "",
                    access_count=row.access_count or 0,
                    age_days=age_days,
                )
                if new_importance != row.importance:
                    row.importance = new_importance
                    rescored += 1

        return rescored

    async def _resolve_conflicts(self) -> tuple[int, int]:
        """Resolve pending conflict rows.

        Returns (auto_resolved_count, needs_review_count). Iterates all
        `status='open'` conflicts, applies the resolution priority
        (grounding → agent trust → recency → importance), and either
        auto-resolves (loser goes `superseded`) or flips to `needs_review`.

        Idempotent: rows with `settled_at` set are skipped unless a newer
        write has bumped one side's updated_at past the settled timestamp.
        """
        if not getattr(self._config, "belief", None) or not self._config.belief.enabled:
            return (0, 0)

        from nmem.conflicts import resolve_conflict

        auto_resolved = 0
        needs_review = 0

        async with self._db.session() as session:
            open_conflicts = (
                await session.execute(
                    select(MemoryConflictModel).where(
                        MemoryConflictModel.status == "open"
                    )
                )
            ).scalars().all()

        for conflict in open_conflicts:
            try:
                outcome = await resolve_conflict(
                    self._db, conflict, self._config.belief,
                )
                if outcome == "auto_resolved":
                    auto_resolved += 1
                elif outcome == "needs_review":
                    needs_review += 1
            except Exception as e:
                logger.warning(
                    "Failed to resolve conflict #%d: %s", conflict.id, e
                )

        if auto_resolved or needs_review:
            logger.info(
                "Belief revision: %d auto-resolved, %d need review",
                auto_resolved, needs_review,
            )
        return (auto_resolved, needs_review)

    async def _run_retrospective(self) -> tuple[int, int]:
        """Nightly retrospective — validate past lessons against new evidence.

        Pulls LTM entries where `record_type` is one of
        `config.retrospective.lesson_record_types`, created within
        `lookback_days`, and NOT validated within
        `skip_if_validated_within_days`. For each, searches the journal
        for outcome entries created after the lesson, then asks the LLM
        to classify the overall evidence as reinforces / contradicts /
        neutral. Cap is `max_llm_calls_per_run`.

        Returns (validated_count, disputed_count).
        """
        cfg = getattr(self._config, "retrospective", None)
        if cfg is None or not cfg.enabled:
            return (0, 0)

        now = datetime.utcnow()
        lookback_cutoff = now - timedelta(days=cfg.lookback_days)
        skip_cutoff = now - timedelta(days=cfg.skip_if_validated_within_days)

        # Candidate lessons: LTM rows matching a lesson record_type, within
        # lookback, NOT validated recently. Newest-first so fresh learnings
        # are prioritized (most recent lessons benefit most from early validation).
        async with self._db.session() as session:
            result = await session.execute(
                select(LTMModel)
                .where(
                    LTMModel.record_type.in_(cfg.lesson_record_types),
                    LTMModel.status == "validated",
                    LTMModel.created_at >= lookback_cutoff,
                    or_(
                        LTMModel.last_validated_at.is_(None),
                        LTMModel.last_validated_at < skip_cutoff,
                    ),
                )
                .order_by(LTMModel.created_at.desc())
                .limit(cfg.max_llm_calls_per_run * 3)  # fetch extra in case some have no outcomes
            )
            candidates = list(result.scalars().all())

        if len(candidates) < cfg.min_lessons:
            logger.debug(
                "Retrospective skipped: %d candidates < min_lessons=%d",
                len(candidates), cfg.min_lessons,
            )
            return (0, 0)

        validated = 0
        disputed = 0
        touched_lessons: list[tuple[int, str, str]] = []  # (id, title, verdict)
        llm_calls = 0

        for lesson in candidates:
            if llm_calls >= cfg.max_llm_calls_per_run:
                break

            # Find outcome candidates — journal entries created after this
            # lesson, semantically similar. Use the lesson embedding to
            # search. Small top-k keeps the prompt compact.
            outcomes = await self._find_lesson_outcomes(
                lesson, since=lesson.created_at
            )
            if not outcomes:
                continue

            try:
                verdict = await self._classify_lesson_outcome(lesson, outcomes)
            except Exception as e:
                logger.warning(
                    "Retrospective classification failed for lesson #%d: %s",
                    lesson.id, e,
                )
                continue

            llm_calls += 1

            lesson_label = lesson.key or f"ltm#{lesson.id}"

            if verdict == "reinforces":
                await self._apply_lesson_verdict(lesson.id, "reinforces")
                validated += 1
                touched_lessons.append((lesson.id, lesson_label, "reinforces"))
            elif verdict == "contradicts":
                await self._apply_lesson_verdict(lesson.id, "contradicts")
                disputed += 1
                touched_lessons.append((lesson.id, lesson_label, "contradicts"))
            elif verdict == "neutral":
                # Bump last_validated_at only — skip guard will exclude
                # this lesson from tomorrow's scan.
                await self._apply_lesson_verdict(lesson.id, "neutral")

        if touched_lessons:
            await self._write_retrospective_synthesis(touched_lessons, now)
            logger.info(
                "Retrospective: %d validated, %d disputed (%d LLM calls)",
                validated, disputed, llm_calls,
            )
        return (validated, disputed)

    async def _find_lesson_outcomes(
        self, lesson: LTMModel, *, since: datetime, top_k: int = 5,
    ) -> list[dict]:
        """Find journal entries that could validate or contradict a lesson.

        Searches journal entries created after the lesson via embedding
        similarity (cosine > 0.55), scoped to the lesson's project_scope.

        Returns a list of dicts with {tier, title, content, created_at}
        suitable for stuffing into the LLM prompt.
        """
        lesson_embedding = getattr(lesson, "embedding", None)
        if lesson_embedding is None:
            return []
        if hasattr(lesson_embedding, "tolist"):
            lesson_embedding = lesson_embedding.tolist()
        embedding_str = f"[{','.join(str(x) for x in lesson_embedding)}]"

        # Scope-aware: match the lesson's project_scope (or global if NULL).
        scope = getattr(lesson, "project_scope", None)
        scope_clause = (
            "AND project_scope IS NULL" if scope is None
            else "AND (project_scope = :scope OR project_scope IS NULL)"
        )
        params: dict = {
            "since": since,
            "embedding": embedding_str,
            "top_k": top_k,
        }
        if scope is not None:
            params["scope"] = scope

        outcomes: list[dict] = []
        try:
            async with self._db.session() as session:
                # Cross-agent search: evidence from ANY agent can validate
                # or contradict a lesson. Same-agent evidence is common but
                # cross-agent corroboration is arguably more valuable.
                # Scope-filtered: only evidence from the same project scope
                # (or global entries) can validate a scoped lesson.
                result = await session.execute(
                    sa_text(f"""
                        SELECT id, title, content, created_at
                        FROM nmem_journal_entries
                        WHERE created_at > :since
                          AND embedding IS NOT NULL
                          {scope_clause}
                          AND 1 - (embedding <=> CAST(:embedding AS vector)) > 0.55
                        ORDER BY embedding <=> CAST(:embedding AS vector)
                        LIMIT :top_k
                    """),
                    params,
                )
                for row in result.all():
                    outcomes.append({
                        "tier": "journal",
                        "title": row[1],
                        "content": row[2][:500] if row[2] else "",
                        "created_at": row[3],
                    })
        except Exception as e:
            logger.debug("Outcome search failed for lesson #%d: %s", lesson.id, e)

        return outcomes

    async def _classify_lesson_outcome(
        self, lesson: LTMModel, outcomes: list[dict]
    ) -> str:
        """Ask the LLM to classify lesson vs outcome evidence.

        Returns one of: "reinforces", "contradicts", "neutral".

        Isolated as a method so tests can monkeypatch it without mocking
        the entire LLM provider.
        """
        outcome_block = "\n".join(
            f"  - [{o['tier']}] {o['title']}: {o['content'][:200]}"
            for o in outcomes
        )
        system_prompt = (
            "You are reviewing whether a past lesson still holds up against "
            "subsequent evidence. Classify the evidence as one of:\n"
            "  reinforces  — evidence aligns with the lesson\n"
            "  contradicts — evidence contradicts the lesson\n"
            "  neutral     — evidence is unrelated or inconclusive\n\n"
            'Respond as JSON: {"verdict": "reinforces|contradicts|neutral", "rationale": "..."}'
        )
        user_prompt = (
            f"LESSON: {lesson.key}\n{lesson.content[:800]}\n\n"
            f"SUBSEQUENT EVIDENCE:\n{outcome_block}"
        )

        result = await self._llm.complete_json(
            system_prompt, user_prompt,
            max_tokens=self._config.llm.synthesis_max_tokens,
            temperature=0.2,
            timeout=20.0,
        )

        # Record LLM usage for token trends
        try:
            from nmem.token_stats import record_llm_usage
            await record_llm_usage(
                self._db, "retrospective",
                self._config.llm.synthesis_max_tokens,
            )
        except Exception:
            pass

        if not result or not isinstance(result, dict):
            return "neutral"

        verdict = str(result.get("verdict", "")).lower().strip()
        if verdict not in ("reinforces", "contradicts", "neutral"):
            return "neutral"
        return verdict

    async def _apply_lesson_verdict(self, lesson_id: int, verdict: str) -> None:
        """Apply a retrospective verdict to a lesson row."""
        now = datetime.utcnow()
        async with self._db.session() as session:
            row = await session.get(LTMModel, lesson_id)
            if row is None:
                return
            row.last_validated_at = now
            if verdict == "reinforces":
                # Bump importance if the row is auto-managed; manual rows
                # stay sovereign.
                if row.auto_importance:
                    row.importance = min(10, row.importance + 1)
                # Refresh salience to 1.0 — reinforced lessons are "hot" again.
                row.salience = 1.0
            elif verdict == "contradicts":
                row.grounding = "disputed"

    async def _write_retrospective_synthesis(
        self, touched: list[tuple[int, str, str]], now: datetime,
    ) -> None:
        """Persist a single shared-knowledge row summarizing the nightly run.

        Mirrors the daily_synthesis pattern used by nightly_synthesis so
        downstream search + decay already handle it.
        """
        from nmem.search import populate_tsvector

        validated = [key for _id, key, v in touched if v == "reinforces"]
        disputed = [key for _id, key, v in touched if v == "contradicts"]

        lines = [f"Retrospective {now.strftime('%Y-%m-%d')}:"]
        if validated:
            lines.append(f"Reinforced: {', '.join(validated)}")
        if disputed:
            lines.append(f"Disputed: {', '.join(disputed)}")
        content = "\n".join(lines)

        key = f"retrospective_{now.strftime('%Y%m%d')}"
        emb = await asyncio.to_thread(self._embedding.embed, content[:500])

        try:
            async with self._db.session() as session:
                record = SharedKnowledgeModel(
                    key=key,
                    content=content,
                    category="retrospective_synthesis",
                    created_by="consolidator",
                    last_updated_by="consolidator",
                    importance=6,
                    embedding=emb,
                )
                session.add(record)
                await session.flush()
                record_id = record.id
            await populate_tsvector(
                self._db, "nmem_shared_knowledge", record_id,
                f"{key} {content[:2000]}",
            )
        except Exception as e:
            # Same-key collision in one night — benign, retrospective can
            # run more than once per day in tests.
            logger.debug("Retrospective synthesis write failed: %s", e)

    async def _update_salience_scores(self) -> int:
        """Decay salience of stale LTM entries.

        Salience reflects how strongly an entry should influence reasoning
        *now*, not whether it is true. Decay fires when the entry has gone
        unaccessed beyond `staleness_days`, with a faster rate for entries
        that have been read but not re-validated.
        """
        staleness_days = self._config.ltm.staleness_days
        decay_rate_unaccessed = self._config.ltm.salience_decay_rate
        decay_rate_stale = self._config.ltm.salience_decay_rate_accessed
        min_salience = self._config.ltm.min_salience
        now = datetime.utcnow()
        stale_cutoff = now - timedelta(days=staleness_days)
        age_cutoff = now - timedelta(days=30)  # Don't decay brand new entries
        updated = 0

        async with self._db.session() as session:
            result = await session.execute(
                select(LTMModel).where(
                    LTMModel.salience > min_salience,
                    LTMModel.created_at < age_cutoff,
                    LTMModel.status == "validated",
                )
            )
            candidates = result.scalars().all()

            for entry in candidates:
                if entry.access_count and entry.access_count > 0:
                    if entry.last_accessed_at and entry.last_accessed_at < stale_cutoff:
                        entry.salience = max(entry.salience - decay_rate_stale, min_salience)
                        updated += 1
                else:
                    entry.salience = max(entry.salience - decay_rate_unaccessed, min_salience)
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
        """Boost importance of journal entries that contributed to discovered patterns.

        Only operates on entries where `auto_importance=True`. Author-set
        importance is sovereign and must never be silently drifted upward
        by pattern synthesis.
        """
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
                              AND auto_importance = TRUE
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
                                  AND auto_importance = TRUE
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
