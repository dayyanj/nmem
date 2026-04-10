"""
Tier 4: Shared Knowledge — cross-agent canonical facts.

Writable by any agent, readable by all. Changes are versioned with change_log
and emit events for notification.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from sqlalchemy import and_, select

from nmem.db.models import SharedKnowledgeModel
from nmem.search import hybrid_memory_search, populate_tsvector
from nmem.types import SharedEntry

if TYPE_CHECKING:
    from nmem.db.session import DatabaseManager
    from nmem.config import NmemConfig
    from nmem.providers.embedding.base import EmbeddingProvider

logger = logging.getLogger(__name__)


class SharedTier:
    """Tier 4: Cross-agent shared knowledge."""

    def __init__(self, db: DatabaseManager, config: NmemConfig, embedding: "EmbeddingProvider"):
        self._db = db
        self._config = config
        self._embedding = embedding
        self._event_handlers: list = []

    async def save(
        self,
        key: str,
        content: str,
        category: str,
        agent_id: str,
        importance: int = 5,
        *,
        record_type: str = "fact",
        grounding: str = "confirmed",
        project_scope: str | None = ...,
    ) -> SharedEntry:
        """Save or update a shared knowledge entry.

        Args:
            key: Unique key.
            content: Entry content.
            category: Category (e.g., "company_fact", "pricing", "procedure").
            agent_id: Agent making the change.
            importance: Importance 1-10.
            record_type: "fact", "policy", "procedure", etc.
            grounding: "confirmed", "inferred", etc.
            project_scope: Scope filter.

        Returns:
            The created/updated SharedEntry.
        """
        if project_scope is ...:
            project_scope = self._config.project_scope

        emb = await asyncio.to_thread(self._embedding.embed, f"{key} {content[:500]}")

        async with self._db.session() as session:
            filters = [SharedKnowledgeModel.key == key]
            if project_scope is not None:
                filters.append(SharedKnowledgeModel.project_scope == project_scope)
            else:
                filters.append(SharedKnowledgeModel.project_scope.is_(None))
            stmt = select(SharedKnowledgeModel).where(and_(*filters))
            result = await session.execute(stmt)
            existing = result.scalar_one_or_none()

            if existing:
                change = {
                    "agent": agent_id,
                    "date": datetime.now(timezone.utc).isoformat(),
                    "old_value": existing.content[:200],
                }
                log = existing.change_log or []
                log.append(change)

                existing.content = content
                existing.category = category
                existing.last_updated_by = agent_id
                existing.importance = max(existing.importance, importance)
                existing.embedding = emb
                existing.record_type = record_type
                existing.grounding = grounding
                existing.version += 1
                existing.change_log = log
                await session.flush()
                await session.refresh(existing)
                entry = self._row_to_entry(existing)
            else:
                record = SharedKnowledgeModel(
                    key=key,
                    content=content,
                    category=category,
                    created_by=agent_id,
                    last_updated_by=agent_id,
                    importance=importance,
                    embedding=emb,
                    record_type=record_type,
                    grounding=grounding,
                    project_scope=project_scope,
                )
                session.add(record)
                await session.flush()
                await session.refresh(record)
                entry = self._row_to_entry(record)

        # Populate TSVECTOR (outside session to avoid deadlock)
        await populate_tsvector(
            self._db, "nmem_shared_knowledge", entry.id,
            f"{key} {content[:2000]}",
        )
        return entry

    async def search(
        self, query: str, top_k: int = 5, *,
        category: str | None = None,
        project_scope: str | None = ...,
    ) -> list[SharedEntry]:
        """Search shared knowledge using hybrid vector + FTS search.

        Shared knowledge is typically global, but can be project-scoped.
        When scope is set, includes both scoped and global entries.

        Args:
            query: Search query.
            top_k: Maximum results.
            category: Optional category filter.
            project_scope: Scope filter.

        Returns:
            List of SharedEntry objects, ranked by relevance.
        """
        if project_scope is ...:
            project_scope = self._config.project_scope

        query_embedding = await asyncio.to_thread(self._embedding.embed, query)

        where_parts = ["status = 'validated'"]
        params: dict = {}
        if category:
            where_parts.append("category = :category")
            params["category"] = category
        if project_scope is not None:
            where_parts.append("(project_scope = :project_scope OR project_scope IS NULL)")
            params["project_scope"] = project_scope

        ranked = await hybrid_memory_search(
            db=self._db,
            table="nmem_shared_knowledge",
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
                select(SharedKnowledgeModel).where(SharedKnowledgeModel.id.in_(ranked_ids))
            )
            entries_by_id = {e.id: e for e in result.scalars().all()}
            return [self._row_to_entry(entries_by_id[eid]) for eid in ranked_ids if eid in entries_by_id]

    async def get(self, key: str) -> SharedEntry | None:
        """Get a shared knowledge entry by key."""
        async with self._db.session() as session:
            stmt = select(SharedKnowledgeModel).where(SharedKnowledgeModel.key == key)
            result = await session.execute(stmt)
            row = result.scalar_one_or_none()
            return self._row_to_entry(row) if row else None

    async def list(self, category: str | None = None) -> list[SharedEntry]:
        """List all shared knowledge entries."""
        async with self._db.session() as session:
            stmt = select(SharedKnowledgeModel).order_by(SharedKnowledgeModel.key)
            if category:
                stmt = stmt.where(SharedKnowledgeModel.category == category)
            result = await session.execute(stmt)
            return [self._row_to_entry(r) for r in result.scalars().all()]

    async def build_prompt(
        self, max_chars: int | None = None, query: str | None = None
    ) -> str:
        """Build shared knowledge prompt section.

        Uses one-line stubs (60 chars content) with a note to use
        get() for full details.
        """
        max_chars = max_chars or self._config.shared.max_chars_in_prompt
        if query:
            entries = await self.search(query, top_k=20)
        else:
            entries = await self.list()

        if not entries:
            return ""

        lines: list[str] = []
        chars = 0
        for e in entries:
            stub = e.content[:60].replace("\n", " ")
            line = f"- {e.key}: {stub}..."
            if chars + len(line) > max_chars:
                break
            lines.append(line)
            chars += len(line)
        return "\n".join(lines)

    @staticmethod
    def _row_to_entry(row: SharedKnowledgeModel) -> SharedEntry:
        return SharedEntry(
            id=row.id,
            category=row.category,
            key=row.key,
            content=row.content,
            created_by=row.created_by,
            last_updated_by=row.last_updated_by,
            confirmed=row.confirmed,
            importance=row.importance,
            record_type=row.record_type,
            grounding=row.grounding,
            status=row.status,
            version=row.version,
            change_log=row.change_log,
            project_scope=row.project_scope,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )
