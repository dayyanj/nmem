"""
Tier 2: Journal — 30-day activity log with promotion and decay.

Journal entries capture session summaries, decisions, outcomes, and lessons.
High-importance entries auto-promote to LTM. Hybrid search (vector + FTS).
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from sqlalchemy import select, and_

from nmem.db.models import JournalEntryModel
from nmem.search import (
    hybrid_memory_search,
    populate_tsvector,
    assign_context_thread,
    cosine_similarity,
)
from nmem.types import JournalEntry

if TYPE_CHECKING:
    from nmem.db.session import DatabaseManager
    from nmem.config import NmemConfig
    from nmem.providers.embedding.base import EmbeddingProvider
    from nmem.providers.llm.base import LLMProvider

logger = logging.getLogger(__name__)


class JournalTier:
    """Tier 2: Short-term activity journal with auto-promotion."""

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
        # Consolidation signal callback (set by MemorySystem)
        self._on_high_importance: callable | None = None
        self._on_event: callable | None = None

    async def add(
        self,
        agent_id: str,
        entry_type: str,
        title: str,
        content: str,
        importance: int | None = None,
        *,
        session_id: str | None = None,
        tags: list[str] | None = None,
        record_type: str = "evidence",
        grounding: str = "inferred",
        compress: bool = True,
        project_scope: str | None = ...,
        created_at: datetime | None = None,
        expires_at: datetime | None = None,
    ) -> JournalEntry:
        """Add a journal entry with embedding, compression, dedup, and context threading.

        Args:
            agent_id: Agent identifier.
            entry_type: Entry type (e.g., "session_summary", "decision", "outcome").
            title: Short descriptive title.
            content: Full entry content.
            importance: Importance 1-10. If None (default), the consolidation
                heuristic scorer will manage this value at cycle time and the
                row is marked `auto_importance=True`. If the caller passes an
                explicit int, `auto_importance` flips to False and the scorer
                leaves the value alone forever.
            session_id: Optional session ID.
            tags: Optional tags for filtering.
            record_type: "evidence", "fact", "judgment", "task", "rule", "summary".
            grounding: "source_material", "inferred", "confirmed", "disputed".
            compress: Whether to LLM-compress content (default True).
            created_at: Override creation timestamp (for bulk imports). When set,
                expiry is computed from this date, not NOW(), so historical
                entries expire based on their original age.
            expires_at: Override expiry timestamp directly. Takes precedence
                over the created_at-based calculation.

        Returns:
            The created JournalEntry, or existing entry if deduplicated.
        """
        # Explicit importance flips auto_importance off. Missing / None means
        # "let the consolidator score this" and gets the default of 5 for now.
        auto_importance = importance is None
        if auto_importance:
            importance = 5
        importance = min(max(importance, 1), 10)

        # Resolve project scope: sentinel (...) means "use config default"
        if project_scope is ...:
            project_scope = self._config.project_scope

        # Embed from RAW content (full semantic richness before compression)
        emb = await asyncio.to_thread(
            self._embedding.embed, f"{title} {content[:500]}"
        )

        # Dedup check: skip near-identical entries from last 24 hours
        dedup_result = await self._check_dedup(agent_id, emb, project_scope=project_scope)
        if dedup_result is not None:
            return dedup_result

        # Compress content to distilled fact (embed first, compress second)
        if compress and len(content) > self._config.llm.compression_max_chars:
            compressed = await self._compress(title, content)
        else:
            compressed = content

        # Assign context thread
        thread_id = assign_context_thread(
            emb, agent_id, self._config.clustering.similarity_threshold
        )

        # Compute timestamps: explicit expires_at wins, then created_at-based,
        # then default (NOW + expiry_days). This ensures imported historical
        # entries expire based on their original age, not the import date.
        base_time = created_at or datetime.utcnow()
        if expires_at is not None:
            effective_expires = expires_at
        else:
            effective_expires = base_time + timedelta(
                days=self._config.journal.default_expiry_days
            )

        async with self._db.session() as session:
            record = JournalEntryModel(
                agent_id=agent_id,
                session_id=session_id,
                entry_type=entry_type,
                title=title[:300],
                content=compressed,
                importance=importance,
                auto_importance=auto_importance,
                relevance_score=min(importance / 10.0, 1.0),
                expires_at=effective_expires,
                context_thread_id=thread_id,
                tags=tags,
                record_type=record_type,
                grounding=grounding,
                embedding=emb,
                project_scope=project_scope,
            )
            # Override created_at for historical imports (bypasses server_default)
            if created_at is not None:
                record.created_at = created_at
            session.add(record)
            await session.flush()
            entry_id = record.id
            actual_created_at = record.created_at

        # Populate TSVECTOR (fire-and-forget, non-blocking)
        await populate_tsvector(
            self._db, "nmem_journal_entries", entry_id,
            f"{title} {compressed[:2000]}",
        )

        # Signal consolidator for high-importance entries
        if importance >= self._config.journal.auto_promote_importance:
            if self._on_high_importance:
                self._on_high_importance(f"journal_{entry_type}_imp{importance}")

        entry = JournalEntry(
            id=entry_id,
            agent_id=agent_id,
            entry_type=entry_type,
            title=title[:300],
            content=compressed,
            importance=importance,
            auto_importance=auto_importance,
            relevance_score=min(importance / 10.0, 1.0),
            expires_at=effective_expires,
            context_thread_id=thread_id,
            tags=tags,
            record_type=record_type,
            grounding=grounding,
            project_scope=project_scope,
            created_at=actual_created_at,
        )

        if self._on_event:
            try:
                await self._on_event("journal.added", {
                    "id": entry.id, "agent_id": agent_id,
                    "title": title[:300], "importance": importance,
                    "entry_type": entry_type,
                })
            except Exception:
                pass

        return entry

    async def add_batch(
        self,
        entries: list[dict],
        *,
        compress: bool = False,
    ) -> list[JournalEntry]:
        """Add multiple journal entries with batched embedding.

        Embeds all entries in a single batch call for 3-5x speedup.
        Deduplication is skipped for batch speed — rely on consolidation.

        Each dict should have: agent_id, entry_type, title, content.
        Optional: importance, tags, record_type, grounding, session_id,
        project_scope, created_at, expires_at.

        Args:
            entries: List of entry dicts.
            compress: Whether to LLM-compress content (default False for speed).

        Returns:
            List of created JournalEntry objects.
        """
        if not entries:
            return []

        # Batch embed all texts at once
        texts = [f"{e['title']} {e['content'][:500]}" for e in entries]
        embeddings = await asyncio.to_thread(self._embedding.embed_batch, texts)

        results = []
        for entry_dict, emb in zip(entries, embeddings):
            agent_id = entry_dict["agent_id"]
            entry_type = entry_dict["entry_type"]
            title = entry_dict["title"]
            content = entry_dict["content"]
            session_id = entry_dict.get("session_id")
            tags = entry_dict.get("tags")
            record_type = entry_dict.get("record_type", "evidence")
            grounding = entry_dict.get("grounding", "inferred")

            # Explicit importance flips auto_importance off. Missing / None means
            # "let the consolidator score this" and gets the default of 5 for now.
            raw_importance = entry_dict.get("importance")
            auto_importance = raw_importance is None
            importance = 5 if auto_importance else min(max(int(raw_importance), 1), 10)

            # Resolve project scope: sentinel (...) pattern — missing key uses config default
            scope = entry_dict.get("project_scope", ...)
            if scope is ...:
                scope = self._config.project_scope

            # Compress content if requested
            if compress and len(content) > self._config.llm.compression_max_chars:
                content = await self._compress(title, content)

            # Assign context thread
            thread_id = assign_context_thread(
                emb, agent_id, self._config.clustering.similarity_threshold
            )

            # Compute timestamps: explicit expires_at wins, then created_at-based,
            # then default (NOW + expiry_days).
            created_at = entry_dict.get("created_at")
            base_time = created_at or datetime.utcnow()
            entry_expires_at = entry_dict.get("expires_at")
            if entry_expires_at is not None:
                effective_expires = entry_expires_at
            else:
                effective_expires = base_time + timedelta(
                    days=self._config.journal.default_expiry_days
                )

            async with self._db.session() as session:
                record = JournalEntryModel(
                    agent_id=agent_id,
                    session_id=session_id,
                    entry_type=entry_type,
                    title=title[:300],
                    content=content,
                    importance=importance,
                    auto_importance=auto_importance,
                    relevance_score=min(importance / 10.0, 1.0),
                    expires_at=effective_expires,
                    context_thread_id=thread_id,
                    tags=tags,
                    record_type=record_type,
                    grounding=grounding,
                    embedding=emb,
                    project_scope=scope,
                )
                # Override created_at for historical imports (bypasses server_default)
                if created_at is not None:
                    record.created_at = created_at
                session.add(record)
                await session.flush()
                entry_id = record.id
                actual_created_at = record.created_at

            # Populate TSVECTOR (fire-and-forget, non-blocking)
            await populate_tsvector(
                self._db, "nmem_journal_entries", entry_id,
                f"{title} {content[:2000]}",
            )

            # Signal consolidator for high-importance entries
            if importance >= self._config.journal.auto_promote_importance:
                if self._on_high_importance:
                    self._on_high_importance(f"journal_{entry_type}_imp{importance}")

            results.append(JournalEntry(
                id=entry_id,
                agent_id=agent_id,
                entry_type=entry_type,
                title=title[:300],
                content=content,
                importance=importance,
                auto_importance=auto_importance,
                relevance_score=min(importance / 10.0, 1.0),
                expires_at=effective_expires,
                context_thread_id=thread_id,
                tags=tags,
                record_type=record_type,
                grounding=grounding,
                project_scope=scope,
                created_at=actual_created_at,
            ))

        return results

    async def search(
        self,
        agent_id: str,
        query: str,
        top_k: int = 5,
        *,
        entry_type: str | None = None,
        min_importance: int = 0,
        project_scope: str | None = ...,
        bump_access: bool = True,
    ) -> list[JournalEntry]:
        """Search journal entries using hybrid vector + FTS search.

        Bumps access_count on returned entries.
        When project_scope is set, includes both scoped and global entries.

        Args:
            agent_id: Agent identifier.
            query: Search query text.
            top_k: Maximum results to return.
            entry_type: Filter by entry type.
            min_importance: Minimum importance threshold.
            project_scope: Scope filter. Sentinel (...) = use config default.

        Returns:
            List of JournalEntry objects, ranked by relevance.
        """
        if project_scope is ...:
            project_scope = self._config.project_scope

        query_embedding = await asyncio.to_thread(self._embedding.embed, query)

        # Build WHERE clause for hybrid search
        where_parts = [
            "agent_id = :agent_id",
            "promoted_to_ltm = FALSE",
            "importance >= :min_importance",
        ]
        params: dict = {"agent_id": agent_id, "min_importance": min_importance}
        if entry_type:
            where_parts.append("entry_type = :entry_type")
            params["entry_type"] = entry_type
        if project_scope == "*":
            pass  # Cross-scope search: no scope filter
        elif project_scope is not None:
            where_parts.append("(project_scope = :project_scope OR project_scope IS NULL)")
            params["project_scope"] = project_scope

        # Run hybrid search
        ranked = await hybrid_memory_search(
            db=self._db,
            table="nmem_journal_entries",
            query_embedding=query_embedding,
            query_text=query,
            where_clause=" AND ".join(where_parts),
            params=params,
            top_k=top_k,
        )

        if not ranked:
            return []

        ranked_ids = [r[0] for r in ranked]
        scores = {r[0]: r[1] for r in ranked}

        # Fetch full ORM objects and bump access stats
        async with self._db.session() as session:
            result = await session.execute(
                select(JournalEntryModel).where(JournalEntryModel.id.in_(ranked_ids))
            )
            entries_by_id = {e.id: e for e in result.scalars().all()}

            now = datetime.utcnow()
            results = []
            for eid in ranked_ids:
                e = entries_by_id.get(eid)
                if not e:
                    continue
                if bump_access:
                    e.access_count += 1
                    e.last_accessed_at = now
                entry = self._row_to_entry(e)
                # Override relevance_score with hybrid search score
                results.append(JournalEntry(
                    id=entry.id, agent_id=entry.agent_id, entry_type=entry.entry_type,
                    title=entry.title, content=entry.content, importance=entry.importance,
                    auto_importance=entry.auto_importance,
                    relevance_score=scores.get(eid, 0.0), access_count=entry.access_count,
                    expires_at=entry.expires_at, promoted_to_ltm=entry.promoted_to_ltm,
                    context_thread_id=entry.context_thread_id, record_type=entry.record_type,
                    grounding=entry.grounding, status=entry.status, tags=entry.tags,
                    pointers=entry.pointers, created_at=entry.created_at,
                ))

        return results

    async def recent(
        self, agent_id: str, days: int = 7, limit: int = 10,
        *, project_scope: str | None = ...,
    ) -> list[JournalEntry]:
        """Get recent journal entries.

        Args:
            agent_id: Agent identifier.
            days: Look back N days.
            limit: Maximum entries.

        Returns:
            List of JournalEntry objects, newest first.
        """
        if project_scope is ...:
            project_scope = self._config.project_scope

        cutoff = datetime.utcnow() - timedelta(days=days)
        async with self._db.session() as session:
            filters = [
                JournalEntryModel.agent_id == agent_id,
                JournalEntryModel.created_at >= cutoff,
            ]
            if project_scope == "*":
                pass  # Cross-scope: no filter
            elif project_scope is not None:
                from sqlalchemy import or_
                filters.append(or_(
                    JournalEntryModel.project_scope == project_scope,
                    JournalEntryModel.project_scope.is_(None),
                ))
            stmt = (
                select(JournalEntryModel)
                .where(and_(*filters))
                .order_by(JournalEntryModel.created_at.desc(), JournalEntryModel.id.desc())
                .limit(limit)
            )
            result = await session.execute(stmt)
            return [self._row_to_entry(row) for row in result.scalars().all()]

    async def activity_summary(self, agent_id: str, days: int = 1) -> str:
        """Get a text summary of recent activity."""
        entries = await self.recent(agent_id, days=days, limit=20)
        if not entries:
            return f"No activity in the last {days} day(s)."
        lines = [f"Activity summary ({days}d, {len(entries)} entries):"]
        for e in entries:
            lines.append(f"  - [{e.entry_type}] {e.title} (importance: {e.importance})")
        return "\n".join(lines)

    async def build_prompt(
        self, agent_id: str, max_chars: int | None = None, query: str | None = None
    ) -> str:
        """Build prompt section from journal entries.

        Relevance-ranked when query provided, otherwise chronological (last 3 days).
        Tiered verbosity: title stubs only.
        """
        max_chars = max_chars or self._config.journal.max_chars_in_prompt
        if query:
            entries = await self.search(agent_id, query, top_k=8)
        else:
            entries = await self.recent(agent_id, days=3, limit=5)

        if not entries:
            return ""

        lines: list[str] = []
        chars = 0
        for e in entries:
            ts = e.created_at.strftime("%Y-%m-%d") if e.created_at else "?"
            line = f"- [{ts}] ({e.entry_type}) {e.title[:60]}"
            if chars + len(line) > max_chars:
                break
            lines.append(line)
            chars += len(line) + 1
        return "\n".join(lines)

    async def _check_dedup(
        self, agent_id: str, embedding: list[float],
        *, project_scope: str | None = None,
    ) -> JournalEntry | None:
        """Check for near-duplicate entries in the last 24 hours.

        If a similar entry exists (cosine > dedup threshold), bump it
        instead of creating a new one. Only deduplicates within same scope.

        Returns:
            Existing JournalEntry if deduplicated, None otherwise.
        """
        threshold = self._config.journal.dedup_similarity_threshold
        try:
            since = datetime.utcnow() - timedelta(hours=24)
            embedding_str = f"[{','.join(str(x) for x in embedding)}]"

            async with self._db.session() as session:
                filters = [
                    JournalEntryModel.agent_id == agent_id,
                    JournalEntryModel.created_at >= since,
                    JournalEntryModel.embedding.isnot(None),
                ]
                if project_scope is not None:
                    filters.append(JournalEntryModel.project_scope == project_scope)
                else:
                    filters.append(JournalEntryModel.project_scope.is_(None))

                result = await session.execute(
                    select(JournalEntryModel)
                    .where(and_(*filters))
                    .order_by(
                        JournalEntryModel.embedding.cosine_distance(embedding)
                    )
                    .limit(1)
                )
                existing = result.scalar_one_or_none()
                if existing and existing.embedding is not None:
                    sim = cosine_similarity(embedding, list(existing.embedding))
                    if sim > threshold:
                        existing.access_count = (existing.access_count or 0) + 1
                        logger.debug(
                            "Journal dedup: sim=%.2f with entry #%d",
                            sim, existing.id,
                        )
                        return self._row_to_entry(existing)
        except Exception as e:
            logger.debug("Dedup check failed (non-fatal): %s", e)
        return None

    async def _compress(self, title: str, content: str) -> str:
        """Compress content using LLM distillation."""
        max_chars = self._config.llm.compression_max_chars
        system = (
            f"Distill the following into a single factual statement. "
            f"Keep names, dates, numbers, and decisions. "
            f"Max {max_chars} characters. Output ONLY the compressed fact."
        )
        user = f"{title}: {content[:1000]}"
        try:
            result = await self._llm.complete(
                system, user,
                max_tokens=self._config.llm.compression_max_tokens,
                temperature=0.1,
                timeout=10.0,
            )
            compressed = result.strip()
            if compressed and len(compressed) <= max_chars * 2:
                return compressed[:max_chars]
        except Exception as e:
            logger.debug("Compression failed (falling back to truncation): %s", e)
        return f"{title}: {content[:max_chars]}"

    @staticmethod
    def _row_to_entry(row: JournalEntryModel) -> JournalEntry:
        return JournalEntry(
            id=row.id,
            agent_id=row.agent_id,
            entry_type=row.entry_type,
            title=row.title,
            content=row.content,
            importance=row.importance,
            auto_importance=row.auto_importance,
            relevance_score=row.relevance_score,
            access_count=row.access_count,
            expires_at=row.expires_at,
            promoted_to_ltm=row.promoted_to_ltm,
            context_thread_id=row.context_thread_id,
            record_type=row.record_type,
            grounding=row.grounding,
            status=row.status,
            tags=row.tags,
            pointers=row.pointers,
            project_scope=row.project_scope,
            created_at=row.created_at,
        )
