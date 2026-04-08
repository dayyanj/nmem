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

from sqlalchemy import select

from nmem.db.models import SharedKnowledgeModel
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

        Returns:
            The created/updated SharedEntry.
        """
        emb = await asyncio.to_thread(self._embedding.embed, f"{key} {content[:500]}")

        async with self._db.session() as session:
            stmt = select(SharedKnowledgeModel).where(SharedKnowledgeModel.key == key)
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
                return self._row_to_entry(existing)
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
                )
                session.add(record)
                await session.flush()
                return self._row_to_entry(record)

    async def search(
        self, query: str, top_k: int = 5, *, category: str | None = None
    ) -> list[SharedEntry]:
        """Search shared knowledge using hybrid search.

        Args:
            query: Search query.
            top_k: Maximum results.
            category: Optional category filter.

        Returns:
            List of SharedEntry objects.
        """
        # TODO: Phase 2 — implement full hybrid search
        async with self._db.session() as session:
            stmt = (
                select(SharedKnowledgeModel)
                .order_by(SharedKnowledgeModel.importance.desc())
                .limit(top_k)
            )
            if category:
                stmt = stmt.where(SharedKnowledgeModel.category == category)
            result = await session.execute(stmt)
            return [self._row_to_entry(r) for r in result.scalars().all()]

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
            created_at=row.created_at,
            updated_at=row.updated_at,
        )
