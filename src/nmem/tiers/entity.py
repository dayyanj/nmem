"""
Tier 5: Entity Memory — collaborative workspace per business object.

Multiple agents read/write with evidence-based grounding levels.
Supports records of type: evidence, judgment, task, summary.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from sqlalchemy import select, and_

from nmem.db.models import EntityMemoryModel
from nmem.exceptions import PermissionError
from nmem.search import hybrid_memory_search, populate_tsvector
from nmem.types import EntityRecord

if TYPE_CHECKING:
    from nmem.db.session import DatabaseManager
    from nmem.config import NmemConfig
    from nmem.providers.embedding.base import EmbeddingProvider

logger = logging.getLogger(__name__)


class EntityTier:
    """Tier 5: Entity-scoped collaborative memory."""

    def __init__(self, db: DatabaseManager, config: NmemConfig, embedding: "EmbeddingProvider"):
        self._db = db
        self._config = config
        self._embedding = embedding
        # Auto-journal callback (set by MemorySystem)
        self._auto_journal_callback: callable | None = None

    def _check_permission(self, agent_id: str, entity_type: str) -> None:
        """Check if agent has write permission for this entity type."""
        perms = self._config.entity.write_permissions
        if not perms:
            return  # Empty = full access for all
        if agent_id not in perms:
            return  # Agent not in permissions map = full access
        allowed = perms[agent_id]
        if "*" in allowed or entity_type in allowed:
            return
        raise PermissionError(
            f"Agent '{agent_id}' cannot write to entity type '{entity_type}'. "
            f"Allowed types: {allowed}"
        )

    async def save(
        self,
        entity_type: str,
        entity_id: str,
        entity_name: str,
        agent_id: str,
        content: str,
        *,
        record_type: str = "evidence",
        confidence: float = 0.8,
        grounding: str = "inferred",
        tags: list[str] | None = None,
        evidence_refs: list[dict] | None = None,
        project_scope: str | None = ...,
    ) -> EntityRecord:
        """Save an entity memory record.

        Args:
            entity_type: Type (e.g., "lead", "customer", "bug", "deployment").
            entity_id: Unique entity identifier.
            entity_name: Human-readable name.
            agent_id: Agent writing the record.
            content: Record content.
            record_type: "evidence", "judgment", "task", "summary".
            confidence: Confidence 0.0-1.0 (forced <1.0 for judgments).
            grounding: "source_material", "inferred", "confirmed", "disputed".
            tags: Optional tags.
            evidence_refs: References to supporting evidence.

        Returns:
            The created EntityRecord.
        """
        self._check_permission(agent_id, entity_type)

        if project_scope is ...:
            project_scope = self._config.project_scope

        # Force judgment confidence below 1.0
        if record_type == "judgment" and confidence >= 1.0:
            confidence = 0.9

        emb = await asyncio.to_thread(
            self._embedding.embed, f"{entity_name} {content[:500]}"
        )

        status = "validated" if record_type == "evidence" else "draft"

        async with self._db.session() as session:
            record = EntityMemoryModel(
                entity_type=entity_type,
                entity_id=entity_id,
                entity_name=entity_name,
                agent_id=agent_id,
                record_type=record_type,
                content=content,
                confidence=confidence,
                grounding=grounding,
                status=status,
                tags=tags,
                evidence_refs=evidence_refs,
                embedding=emb,
                project_scope=project_scope,
            )
            session.add(record)
            await session.flush()
            entry_id = record.id
            await session.refresh(record)
            entity_record = self._row_to_record(record)

        # Populate TSVECTOR (outside session to avoid deadlock)
        await populate_tsvector(
            self._db, "nmem_entity_memory", entry_id,
            f"{entity_name} {content[:2000]}",
        )
        return entity_record

    async def get(
        self,
        entity_type: str,
        entity_id: str,
        *,
        record_type: str | None = None,
        limit: int = 20,
    ) -> list[EntityRecord]:
        """Get entity memory records.

        Args:
            entity_type: Entity type.
            entity_id: Entity identifier.
            record_type: Filter by record type.
            limit: Maximum records.

        Returns:
            List of EntityRecord objects.
        """
        async with self._db.session() as session:
            conditions = [
                EntityMemoryModel.entity_type == entity_type,
                EntityMemoryModel.entity_id == entity_id,
            ]
            if record_type:
                conditions.append(EntityMemoryModel.record_type == record_type)

            stmt = (
                select(EntityMemoryModel)
                .where(and_(*conditions))
                .order_by(EntityMemoryModel.created_at.desc())
                .limit(limit)
            )
            result = await session.execute(stmt)
            return [self._row_to_record(r) for r in result.scalars().all()]

    async def search(
        self,
        query: str,
        *,
        entity_type: str | None = None,
        entity_id: str | None = None,
        top_k: int = 5,
        project_scope: str | None = ...,
        agent_id: str | None = None,
    ) -> list[EntityRecord]:
        """Search entity memory using hybrid vector + FTS search.

        Args:
            query: Search query.
            entity_type: Filter by type.
            entity_id: Filter by entity.
            top_k: Maximum results.

        Returns:
            List of EntityRecord objects, ranked by relevance.
        """
        if project_scope is ...:
            project_scope = self._config.project_scope

        query_embedding = await asyncio.to_thread(self._embedding.embed, query)

        where_parts = ["1=1"]
        params: dict = {}
        if entity_type:
            where_parts.append("entity_type = :entity_type")
            params["entity_type"] = entity_type
        if entity_id:
            where_parts.append("entity_id = :entity_id")
            params["entity_id"] = entity_id
        if project_scope == "*":
            pass  # Cross-scope search: no scope filter
        elif project_scope is not None:
            where_parts.append("(project_scope = :project_scope OR project_scope IS NULL)")
            params["project_scope"] = project_scope

        ranked = await hybrid_memory_search(
            db=self._db,
            table="nmem_entity_memory",
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
                select(EntityMemoryModel).where(EntityMemoryModel.id.in_(ranked_ids))
            )
            records_by_id = {r.id: r for r in result.scalars().all()}
            records = [self._row_to_record(records_by_id[eid]) for eid in ranked_ids if eid in records_by_id]

        # Auto-journal entity access (background, non-blocking)
        if (
            records
            and agent_id
            and self._auto_journal_callback
            and self._config.entity.auto_journal_on_search
        ):
            meaningful = [r for r in records if r.confidence >= self._config.entity.auto_journal_min_score]
            if len(meaningful) >= self._config.entity.auto_journal_min_results:
                top = meaningful[0]
                asyncio.create_task(
                    self._auto_journal_callback(
                        agent_id=agent_id,
                        entity_type=top.entity_type,
                        entity_id=top.entity_id,
                        entity_name=top.entity_name,
                        result_count=len(meaningful),
                        top_content=top.content[:150],
                        project_scope=top.project_scope,
                    )
                )

        return records

    async def get_summary(
        self, entity_type: str, entity_id: str
    ) -> EntityRecord | None:
        """Get the latest summary record for an entity.

        Returns:
            EntityRecord with record_type="summary", or None.
        """
        async with self._db.session() as session:
            stmt = (
                select(EntityMemoryModel)
                .where(
                    and_(
                        EntityMemoryModel.entity_type == entity_type,
                        EntityMemoryModel.entity_id == entity_id,
                        EntityMemoryModel.record_type == "summary",
                    )
                )
                .order_by(EntityMemoryModel.created_at.desc())
                .limit(1)
            )
            result = await session.execute(stmt)
            row = result.scalar_one_or_none()
            return self._row_to_record(row) if row else None

    async def build_prompt(
        self, entity_type: str, entity_id: str, max_chars: int | None = None
    ) -> str:
        """Build entity dossier prompt section.

        Returns:
            Formatted entity memory markdown.
        """
        max_chars = max_chars or self._config.entity.max_chars_in_prompt
        records = await self.get(entity_type, entity_id, limit=30)
        if not records:
            return ""

        entity_name = records[0].entity_name if records else "Unknown"
        lines = [f"**{entity_name}** ({entity_type}/{entity_id})"]
        chars = len(lines[0])

        for r in records:
            tag = f"[{r.record_type}|{r.grounding}|{r.confidence:.1f}]"
            line = f"  - {tag} {r.content}"
            if chars + len(line) > max_chars:
                break
            lines.append(line)
            chars += len(line)

        return "\n".join(lines)

    @staticmethod
    def _row_to_record(row: EntityMemoryModel) -> EntityRecord:
        return EntityRecord(
            id=row.id,
            entity_type=row.entity_type,
            entity_id=row.entity_id,
            entity_name=row.entity_name,
            agent_id=row.agent_id,
            record_type=row.record_type,
            content=row.content,
            confidence=row.confidence,
            grounding=row.grounding,
            status=row.status,
            evidence_refs=row.evidence_refs,
            tags=row.tags,
            context_thread_id=row.context_thread_id,
            version=row.version,
            project_scope=row.project_scope,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )
