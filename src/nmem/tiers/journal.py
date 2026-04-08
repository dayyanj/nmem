"""
Tier 2: Journal — 30-day activity log with promotion and decay.

Journal entries capture session summaries, decisions, outcomes, and lessons.
High-importance entries auto-promote to LTM. Hybrid search (vector + FTS).
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

from sqlalchemy import select, func, and_

from nmem.db.models import JournalEntryModel
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

    async def add(
        self,
        agent_id: str,
        entry_type: str,
        title: str,
        content: str,
        importance: int = 5,
        *,
        session_id: str | None = None,
        tags: list[str] | None = None,
        record_type: str = "evidence",
        grounding: str = "inferred",
        compress: bool = True,
    ) -> JournalEntry:
        """Add a journal entry.

        If importance >= auto_promote_importance, signals the consolidator
        for a reactive micro-cycle.

        Args:
            agent_id: Agent identifier.
            entry_type: Entry type (e.g., "session_summary", "decision", "outcome").
            title: Short descriptive title.
            content: Full entry content.
            importance: Importance 1-10 (default 5).
            session_id: Optional session ID.
            tags: Optional tags for filtering.
            record_type: "evidence", "fact", "judgment", "task", "rule", "summary".
            grounding: "source_material", "inferred", "confirmed", "disputed".
            compress: Whether to LLM-compress content (default True).

        Returns:
            The created JournalEntry.
        """
        # TODO: Phase 2 — implement dedup check, embedding, compression, context threading
        import asyncio

        # Compute embedding in thread pool
        embedding = await asyncio.to_thread(
            self._embedding.embed, f"{title} {content[:500]}"
        )

        # Compress if enabled and content is long
        if compress and len(content) > self._config.llm.compression_max_chars:
            compressed = await self._compress(title, content)
        else:
            compressed = content

        expires_at = datetime.now(timezone.utc) + timedelta(
            days=self._config.journal.default_expiry_days
        )

        async with self._db.session() as session:
            record = JournalEntryModel(
                agent_id=agent_id,
                session_id=session_id,
                entry_type=entry_type,
                title=title,
                content=compressed,
                importance=importance,
                expires_at=expires_at,
                tags=tags,
                record_type=record_type,
                grounding=grounding,
                embedding=embedding,
            )
            session.add(record)
            await session.flush()
            entry_id = record.id
            created_at = record.created_at

        return JournalEntry(
            id=entry_id,
            agent_id=agent_id,
            entry_type=entry_type,
            title=title,
            content=compressed,
            importance=importance,
            expires_at=expires_at,
            tags=tags,
            record_type=record_type,
            grounding=grounding,
            created_at=created_at,
        )

    async def search(
        self,
        agent_id: str,
        query: str,
        top_k: int = 5,
        *,
        entry_type: str | None = None,
        min_importance: int = 0,
    ) -> list[JournalEntry]:
        """Search journal entries using hybrid vector + FTS search.

        Bumps access_count on returned entries.

        Args:
            agent_id: Agent identifier.
            query: Search query text.
            top_k: Maximum results to return.
            entry_type: Filter by entry type.
            min_importance: Minimum importance threshold.

        Returns:
            List of JournalEntry objects, ranked by relevance.
        """
        # TODO: Phase 2 — implement hybrid search with CTE
        import asyncio

        query_embedding = await asyncio.to_thread(self._embedding.embed, query)

        async with self._db.session() as session:
            stmt = (
                select(JournalEntryModel)
                .where(
                    and_(
                        JournalEntryModel.agent_id == agent_id,
                        JournalEntryModel.importance >= min_importance,
                    )
                )
                .order_by(JournalEntryModel.importance.desc())
                .limit(top_k)
            )
            if entry_type:
                stmt = stmt.where(JournalEntryModel.entry_type == entry_type)

            result = await session.execute(stmt)
            rows = result.scalars().all()

            return [self._row_to_entry(row) for row in rows]

    async def recent(
        self, agent_id: str, days: int = 7, limit: int = 10
    ) -> list[JournalEntry]:
        """Get recent journal entries.

        Args:
            agent_id: Agent identifier.
            days: Look back N days.
            limit: Maximum entries.

        Returns:
            List of JournalEntry objects, newest first.
        """
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        async with self._db.session() as session:
            stmt = (
                select(JournalEntryModel)
                .where(
                    and_(
                        JournalEntryModel.agent_id == agent_id,
                        JournalEntryModel.created_at >= cutoff,
                    )
                )
                .order_by(JournalEntryModel.created_at.desc())
                .limit(limit)
            )
            result = await session.execute(stmt)
            return [self._row_to_entry(row) for row in result.scalars().all()]

    async def activity_summary(self, agent_id: str, days: int = 1) -> str:
        """Get a text summary of recent activity.

        Args:
            agent_id: Agent identifier.
            days: Look back N days.

        Returns:
            Formatted activity summary string.
        """
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
        """Build a prompt section from journal entries.

        If query is provided, uses relevance-ranked search. Otherwise falls back
        to chronological (last 3 days).

        Returns:
            Formatted journal text with tiered verbosity (title stubs).
        """
        max_chars = max_chars or self._config.journal.max_chars_in_prompt
        if query:
            entries = await self.search(agent_id, query, top_k=10)
        else:
            entries = await self.recent(agent_id, days=3, limit=10)

        if not entries:
            return ""

        lines: list[str] = []
        chars = 0
        for e in entries:
            line = f"- [{e.entry_type}] {e.title}"
            if chars + len(line) > max_chars:
                break
            lines.append(line)
            chars += len(line)
        return "\n".join(lines)

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
            created_at=row.created_at,
        )
