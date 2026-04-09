"""
Tier 3: Long-Term Memory — permanent, versioned, per-agent knowledge.

LTM entries are categorized, versioned, and subject to confidence decay.
Upserts by (agent_id, key) — updating an existing key creates a new version.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import select, and_

from nmem.db.models import LTMModel
from nmem.search import hybrid_memory_search, populate_tsvector, assign_context_thread
from nmem.types import LTMEntry

if TYPE_CHECKING:
    from nmem.db.session import DatabaseManager
    from nmem.config import NmemConfig
    from nmem.providers.embedding.base import EmbeddingProvider
    from nmem.providers.llm.base import LLMProvider

logger = logging.getLogger(__name__)


class LTMTier:
    """Tier 3: Permanent per-agent long-term memory."""

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

    async def save(
        self,
        agent_id: str,
        category: str,
        key: str,
        content: str,
        importance: int = 5,
        *,
        source: str = "agent",
        source_journal_id: int | None = None,
        record_type: str = "fact",
        grounding: str = "inferred",
        compress: bool = True,
    ) -> LTMEntry:
        """Save or update a long-term memory entry.

        Upserts by (agent_id, key). If key exists, creates new version.

        Args:
            agent_id: Agent identifier.
            category: Category (e.g., "fact", "procedure", "lesson", "pattern").
            key: Unique key within the agent's memory.
            content: Entry content.
            importance: Importance 1-10.
            source: "agent", "promotion", "consolidation", "migration".
            source_journal_id: Journal entry that spawned this (if promoted).
            record_type: "fact", "preference", "procedure", "lesson", etc.
            grounding: "source_material", "inferred", "confirmed", "disputed".
            compress: Whether to LLM-compress content.

        Returns:
            The created/updated LTMEntry.
        """
        emb = await asyncio.to_thread(
            self._embedding.embed, f"{key} {content[:500]}"
        )

        if compress and len(content) > self._config.llm.compression_max_chars:
            content = await self._compress(key, content)

        async with self._db.session() as session:
            stmt = select(LTMModel).where(
                and_(LTMModel.agent_id == agent_id, LTMModel.key == key)
            )
            result = await session.execute(stmt)
            existing = result.scalar_one_or_none()

            if existing:
                existing.content = content
                existing.category = category
                existing.importance = max(existing.importance, importance)
                existing.confidence = 1.0
                existing.embedding = emb
                existing.record_type = record_type
                existing.grounding = grounding
                existing.version += 1
                existing.last_validated_at = datetime.utcnow()
                await session.flush()
                await session.refresh(existing)
                entry = self._row_to_entry(existing)
            else:
                record = LTMModel(
                    agent_id=agent_id,
                    category=category,
                    key=key,
                    content=content,
                    importance=importance,
                    source=source,
                    source_journal_id=source_journal_id,
                    record_type=record_type,
                    grounding=grounding,
                    embedding=emb,
                )
                session.add(record)
                await session.flush()
                await session.refresh(record)
                entry = self._row_to_entry(record)

        # Populate TSVECTOR (outside session to avoid deadlock)
        await populate_tsvector(
            self._db, "nmem_long_term_memory", entry.id,
            f"{key} {content[:2000]}",
        )
        return entry

    async def search(
        self,
        agent_id: str,
        query: str,
        top_k: int = 5,
        *,
        category: str | None = None,
    ) -> list[LTMEntry]:
        """Search LTM using hybrid vector + FTS search.

        Bumps access_count on returned entries.

        Args:
            agent_id: Agent identifier.
            query: Search query text.
            top_k: Maximum results.
            category: Filter by category.

        Returns:
            List of LTMEntry objects, ranked by relevance.
        """
        query_embedding = await asyncio.to_thread(self._embedding.embed, query)

        where_parts = ["agent_id = :agent_id", "status = 'validated'"]
        params: dict = {"agent_id": agent_id}
        if category:
            where_parts.append("category = :category")
            params["category"] = category

        ranked = await hybrid_memory_search(
            db=self._db,
            table="nmem_long_term_memory",
            query_embedding=query_embedding,
            query_text=query,
            where_clause=" AND ".join(where_parts),
            params=params,
            top_k=top_k,
        )

        if not ranked:
            return []

        ranked_ids = [r[0] for r in ranked]

        async with self._db.session() as session:
            result = await session.execute(
                select(LTMModel).where(LTMModel.id.in_(ranked_ids))
            )
            entries_by_id = {e.id: e for e in result.scalars().all()}

            now = datetime.utcnow()
            results = []
            for eid in ranked_ids:
                e = entries_by_id.get(eid)
                if not e:
                    continue
                e.access_count += 1
                e.last_accessed_at = now
                # Track which agents have accessed this entry
                agents = set(e.accessed_by_agents or [])
                agents.add(agent_id)
                e.accessed_by_agents = sorted(agents)
                results.append(self._row_to_entry(e))

        return results

    async def get(self, agent_id: str, key: str) -> LTMEntry | None:
        """Get a specific LTM entry by key. O(1) lookup.

        Args:
            agent_id: Agent identifier.
            key: Entry key.

        Returns:
            LTMEntry or None if not found.
        """
        async with self._db.session() as session:
            stmt = select(LTMModel).where(
                and_(LTMModel.agent_id == agent_id, LTMModel.key == key)
            )
            result = await session.execute(stmt)
            row = result.scalar_one_or_none()
            if row:
                row.access_count += 1
                row.last_accessed_at = datetime.utcnow()
                return self._row_to_entry(row)
            return None

    async def list_keys(
        self, agent_id: str, category: str | None = None
    ) -> list[str]:
        """List all LTM keys for an agent.

        Args:
            agent_id: Agent identifier.
            category: Optional category filter.

        Returns:
            List of key strings.
        """
        async with self._db.session() as session:
            stmt = select(LTMModel.key).where(LTMModel.agent_id == agent_id)
            if category:
                stmt = stmt.where(LTMModel.category == category)
            stmt = stmt.order_by(LTMModel.key)
            result = await session.execute(stmt)
            return [row[0] for row in result.all()]

    async def delete(self, agent_id: str, key: str) -> bool:
        """Delete an LTM entry.

        Args:
            agent_id: Agent identifier.
            key: Entry key.

        Returns:
            True if deleted, False if not found.
        """
        from sqlalchemy import delete as sa_delete

        async with self._db.session() as session:
            stmt = sa_delete(LTMModel).where(
                and_(LTMModel.agent_id == agent_id, LTMModel.key == key)
            )
            result = await session.execute(stmt)
            return result.rowcount > 0  # type: ignore[return-value]

    async def build_prompt(
        self, agent_id: str, max_chars: int | None = None, query: str | None = None
    ) -> str:
        """Build a prompt section from LTM.

        If query is provided, uses relevance-ranked search.
        Otherwise uses importance-ordered dump.

        Returns:
            Formatted LTM text with full content.
        """
        max_chars = max_chars or self._config.ltm.max_chars_in_prompt
        if query:
            entries = await self.search(agent_id, query, top_k=20)
        else:
            async with self._db.session() as session:
                stmt = (
                    select(LTMModel)
                    .where(LTMModel.agent_id == agent_id)
                    .order_by(LTMModel.importance.desc())
                    .limit(50)
                )
                result = await session.execute(stmt)
                entries = [self._row_to_entry(r) for r in result.scalars().all()]

        if not entries:
            return ""

        lines: list[str] = []
        chars = 0
        for e in entries:
            line = f"- [{e.category}] {e.key}: {e.content}"
            if chars + len(line) > max_chars:
                break
            lines.append(line)
            chars += len(line)
        return "\n".join(lines)

    async def _compress(self, key: str, content: str) -> str:
        max_chars = self._config.llm.compression_max_chars
        system = (
            f"Distill the following into a single factual statement. "
            f"Keep names, dates, numbers, and decisions. "
            f"Max {max_chars} characters. Output ONLY the compressed fact."
        )
        try:
            result = await self._llm.complete(
                system, f"{key}: {content[:1000]}",
                max_tokens=self._config.llm.compression_max_tokens,
                temperature=0.1, timeout=10.0,
            )
            compressed = result.strip()
            if compressed and len(compressed) <= max_chars * 2:
                return compressed[:max_chars]
        except Exception as e:
            logger.debug("LTM compression failed: %s", e)
        return f"{key}: {content[:max_chars]}"

    @staticmethod
    def _row_to_entry(row: LTMModel) -> LTMEntry:
        return LTMEntry(
            id=row.id,
            agent_id=row.agent_id,
            category=row.category,
            key=row.key,
            content=row.content,
            importance=row.importance,
            confidence=row.confidence,
            access_count=row.access_count,
            source=row.source,
            record_type=row.record_type,
            grounding=row.grounding,
            status=row.status,
            version=row.version,
            context_thread_id=row.context_thread_id,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )
