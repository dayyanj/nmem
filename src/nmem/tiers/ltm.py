"""
Tier 3: Long-Term Memory — permanent, versioned, per-agent knowledge.

LTM entries are categorized, versioned, and subject to salience decay.
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
        importance: int | None = None,
        *,
        source: str = "agent",
        source_journal_id: int | None = None,
        record_type: str = "fact",
        grounding: str = "inferred",
        compress: bool = True,
        project_scope: str | None = ...,
        created_at: datetime | None = None,
    ) -> LTMEntry:
        """Save or update a long-term memory entry.

        Upserts by (agent_id, key, project_scope). If key exists, creates new version.

        Args:
            agent_id: Agent identifier.
            category: Category (e.g., "fact", "procedure", "lesson", "pattern").
            key: Unique key within the agent's memory.
            content: Entry content.
            importance: Importance 1-10. If None (default), the consolidation
                heuristic scorer will manage this value at cycle time and the
                row is marked `auto_importance=True`. If the caller passes an
                explicit int, `auto_importance` flips to False and the scorer
                leaves the value alone.
            source: "agent", "promotion", "consolidation", "migration".
            source_journal_id: Journal entry that spawned this (if promoted).
            record_type: "fact", "preference", "procedure", "lesson", etc.
            grounding: "source_material", "inferred", "confirmed", "disputed".
            compress: Whether to LLM-compress content.
            created_at: Override creation timestamp (for bulk imports). When
                set, staleness_days for salience decay is computed from this
                date, not the import date.

        Returns:
            The created/updated LTMEntry.
        """
        auto_importance = importance is None
        if auto_importance:
            importance = 5
        importance = min(max(importance, 1), 10)
        if project_scope is ...:
            project_scope = self._config.project_scope

        emb = await asyncio.to_thread(
            self._embedding.embed, f"{key} {content[:500]}"
        )

        if compress and len(content) > self._config.llm.compression_max_chars:
            content = await self._compress(key, content)

        async with self._db.session() as session:
            # Upsert by (agent_id, key, project_scope)
            filters = [LTMModel.agent_id == agent_id, LTMModel.key == key]
            if project_scope is not None:
                filters.append(LTMModel.project_scope == project_scope)
            else:
                filters.append(LTMModel.project_scope.is_(None))
            stmt = select(LTMModel).where(and_(*filters))
            result = await session.execute(stmt)
            existing = result.scalar_one_or_none()

            if existing:
                existing.content = content
                existing.category = category
                # Explicit importance always wins over whatever was there
                # before (manual or auto) and disables auto-scoring forever.
                # Auto-importance saves only raise the floor, never lower it.
                if not auto_importance:
                    existing.importance = max(existing.importance, importance)
                    existing.auto_importance = False
                else:
                    existing.importance = max(existing.importance, importance)
                existing.salience = 1.0
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
                    auto_importance=auto_importance,
                    source=source,
                    source_journal_id=source_journal_id,
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
                await session.refresh(record)
                entry = self._row_to_entry(record)

        # Populate TSVECTOR (outside session to avoid deadlock)
        await populate_tsvector(
            self._db, "nmem_long_term_memory", entry.id,
            f"{key} {content[:2000]}",
        )

        # Scan for conflicts with peer entries in the same scope. Fire
        # inline (not fire-and-forget) so tests can observe the detection;
        # bounded by BeliefRevisionConfig.scan_candidates_limit.
        try:
            from nmem.conflicts import scan_conflicts
            await scan_conflicts(
                self._db,
                content=content,
                embedding=list(emb),
                agent_id=agent_id,
                target_table="nmem_long_term_memory",
                target_id=entry.id,
                project_scope=project_scope,
                config=self._config.belief,
            )
        except Exception as e:
            logger.debug("LTM conflict scan failed (non-fatal): %s", e)

        return entry

    async def save_batch(
        self,
        entries: list[dict],
        *,
        compress: bool = False,
    ) -> list[LTMEntry]:
        """Save multiple LTM entries with batched embedding.

        Embeds all entries in a single batch call (3-5x faster than individual
        save() calls for sentence-transformers). Each dict in entries should have:
            agent_id, category, key, content, importance (optional: source, record_type, grounding)

        Args:
            entries: List of entry dicts.
            compress: Whether to LLM-compress content.

        Returns:
            List of created/updated LTMEntry objects.
        """
        if not entries:
            return []

        # Batch embed all texts at once
        texts = [f"{e['key']} {e['content'][:500]}" for e in entries]
        embeddings = await asyncio.to_thread(self._embedding.embed_batch, texts)

        results = []
        for entry_dict, emb in zip(entries, embeddings):
            agent_id = entry_dict["agent_id"]
            category = entry_dict.get("category", "fact")
            key = entry_dict["key"]
            content = entry_dict["content"]
            # Three-way branch: missing key OR explicit None = auto; int = manual.
            raw_importance = entry_dict.get("importance")
            auto_importance = raw_importance is None
            importance = 5 if auto_importance else min(max(int(raw_importance), 1), 10)
            source = entry_dict.get("source", "agent")
            record_type = entry_dict.get("record_type", "fact")
            grounding = entry_dict.get("grounding", "inferred")
            scope = entry_dict.get("project_scope", self._config.project_scope)

            if compress and len(content) > self._config.llm.compression_max_chars:
                content = await self._compress(key, content)

            async with self._db.session() as session:
                filters = [LTMModel.agent_id == agent_id, LTMModel.key == key]
                if scope is not None:
                    filters.append(LTMModel.project_scope == scope)
                else:
                    filters.append(LTMModel.project_scope.is_(None))
                stmt = select(LTMModel).where(and_(*filters))
                result = await session.execute(stmt)
                existing = result.scalar_one_or_none()

                if existing:
                    existing.content = content
                    existing.category = category
                    existing.importance = max(existing.importance, importance)
                    if not auto_importance:
                        existing.auto_importance = False
                    existing.salience = 1.0
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
                        auto_importance=auto_importance,
                        source=source,
                        record_type=record_type,
                        grounding=grounding,
                        embedding=emb,
                        project_scope=scope,
                    )
                    # Override created_at for historical imports
                    entry_created_at = entry_dict.get("created_at")
                    if entry_created_at is not None:
                        record.created_at = entry_created_at
                    session.add(record)
                    await session.flush()
                    await session.refresh(record)
                    entry = self._row_to_entry(record)

            await populate_tsvector(
                self._db, "nmem_long_term_memory", entry.id,
                f"{key} {content[:2000]}",
            )
            results.append(entry)

        return results

    async def search(
        self,
        agent_id: str,
        query: str,
        top_k: int = 5,
        *,
        category: str | None = None,
        project_scope: str | None = ...,
        include_superseded: bool = False,
    ) -> list[tuple[LTMEntry, float]]:
        """Search LTM using hybrid vector + FTS search.

        Bumps access_count on returned entries.
        When project_scope is set, includes both scoped and global entries.

        Args:
            agent_id: Agent identifier.
            query: Search query text.
            top_k: Maximum results.
            category: Filter by category.
            project_scope: Scope filter. Sentinel (...) = use config default.
            include_superseded: Audit hatch — when True, drops the
                `status='validated'` filter and returns superseded rows
                alongside winners. Default False excludes them.

        Returns:
            List of (LTMEntry, relevance_score) tuples, ranked by relevance.
            The relevance_score is the hybrid vector+FTS search score.
        """
        if project_scope is ...:
            project_scope = self._config.project_scope

        query_embedding = await asyncio.to_thread(self._embedding.embed, query)

        where_parts = ["agent_id = :agent_id"]
        if not include_superseded:
            where_parts.append("status = 'validated'")
        params: dict = {"agent_id": agent_id}
        if category:
            where_parts.append("category = :category")
            params["category"] = category
        if project_scope == "*":
            pass  # Cross-scope search: no scope filter
        elif project_scope is not None:
            where_parts.append("(project_scope = :project_scope OR project_scope IS NULL)")
            params["project_scope"] = project_scope

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

        score_by_id = {r[0]: r[1] for r in ranked}
        ranked_ids = [r[0] for r in ranked]

        async with self._db.session() as session:
            result = await session.execute(
                select(LTMModel).where(LTMModel.id.in_(ranked_ids))
            )
            entries_by_id = {e.id: e for e in result.scalars().all()}

            now = datetime.utcnow()
            results: list[tuple[LTMEntry, float]] = []
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
                results.append((self._row_to_entry(e), score_by_id.get(eid, 0.5)))

        return results

    async def get(
        self, agent_id: str, key: str, *, project_scope: str | None = ...,
    ) -> LTMEntry | None:
        """Get a specific LTM entry by key. O(1) lookup.

        Args:
            agent_id: Agent identifier.
            key: Entry key.
            project_scope: Scope filter.

        Returns:
            LTMEntry or None if not found.
        """
        if project_scope is ...:
            project_scope = self._config.project_scope

        async with self._db.session() as session:
            filters = [LTMModel.agent_id == agent_id, LTMModel.key == key]
            if project_scope == "*":
                # Cross-scope: return first match (ambiguous but useful for lookups)
                pass
            elif project_scope is not None:
                filters.append(LTMModel.project_scope == project_scope)
            else:
                filters.append(LTMModel.project_scope.is_(None))
            stmt = select(LTMModel).where(and_(*filters)).limit(1)
            result = await session.execute(stmt)
            row = result.scalar_one_or_none()
            if row:
                row.access_count += 1
                row.last_accessed_at = datetime.utcnow()
                return self._row_to_entry(row)
            return None

    async def list_keys(
        self, agent_id: str, category: str | None = None,
        *, project_scope: str | None = ...,
    ) -> list[str]:
        """List all LTM keys for an agent.

        Args:
            agent_id: Agent identifier.
            category: Optional category filter.
            project_scope: Scope filter.

        Returns:
            List of key strings.
        """
        if project_scope is ...:
            project_scope = self._config.project_scope

        async with self._db.session() as session:
            filters = [LTMModel.agent_id == agent_id]
            if category:
                filters.append(LTMModel.category == category)
            if project_scope == "*":
                pass  # Cross-scope: no filter
            elif project_scope is not None:
                from sqlalchemy import or_
                filters.append(or_(
                    LTMModel.project_scope == project_scope,
                    LTMModel.project_scope.is_(None),
                ))
            stmt = select(LTMModel.key).where(and_(*filters)).order_by(LTMModel.key)
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
            auto_importance=row.auto_importance,
            salience=row.salience,
            access_count=row.access_count,
            source=row.source,
            record_type=row.record_type,
            grounding=row.grounding,
            status=row.status,
            version=row.version,
            context_thread_id=row.context_thread_id,
            project_scope=row.project_scope,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )
