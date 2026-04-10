"""
Associative Knowledge Link Engine.

Builds evidence-based links between memory entries across tiers.
Unlike context threads (similarity-based, 1:1), knowledge links connect
orthogonal entries that share entities, tags, temporal proximity, or
causal relationships. Entries can belong to multiple link groups.

Link types:
  - shared_entity: Both reference the same entity (via tags)
  - shared_tag: Share non-meta tags
  - temporal: Journal entries within N minutes in the same session
  - causal: Decision→Outcome entry type pairs
  - pattern: Nightly synthesis detected cross-cutting pattern
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from sqlalchemy import select, and_, text as sa_text

from nmem.db.models import (
    JournalEntryModel,
    LTMModel,
    EntityMemoryModel,
    KnowledgeLinkModel,
)
from nmem.types import KnowledgeLink, SearchResult

if TYPE_CHECKING:
    from nmem.db.session import DatabaseManager
    from nmem.config import NmemConfig

logger = logging.getLogger(__name__)

# Tags to exclude from shared_tag linking (meta/noise)
_META_TAGS = {"entity_access"}

# Entity tag pattern: "entity:type/id"
_ENTITY_TAG_RE = re.compile(r"^entity:(\w+)/(.+)$")


class KnowledgeLinkEngine:
    """Builds and queries associative knowledge links."""

    def __init__(self, db: DatabaseManager, config: NmemConfig):
        self._db = db
        self._config = config

    async def build_links(self, scope: str | None = None) -> int:
        """Build knowledge links from all tiers. Called during consolidation.

        Returns number of new links created.
        """
        if not self._config.knowledge_links.enabled:
            return 0

        total = 0
        total += await self._link_shared_entities(scope)
        total += await self._link_shared_tags(scope)
        total += await self._link_temporal_proximity(scope)
        return total

    async def get_linked(
        self,
        entry_id: int,
        tier: str,
        *,
        link_types: list[str] | None = None,
        min_strength: float = 0.0,
    ) -> list[KnowledgeLink]:
        """Get all entries linked to a given entry.

        Args:
            entry_id: Entry ID.
            tier: Tier name.
            link_types: Optional filter by link type.
            min_strength: Minimum link strength.

        Returns:
            List of KnowledgeLink objects.
        """
        async with self._db.session() as session:
            # Find links where this entry is source or target
            filters_source = [
                KnowledgeLinkModel.source_id == entry_id,
                KnowledgeLinkModel.source_tier == tier,
                KnowledgeLinkModel.strength >= min_strength,
            ]
            filters_target = [
                KnowledgeLinkModel.target_id == entry_id,
                KnowledgeLinkModel.target_tier == tier,
                KnowledgeLinkModel.strength >= min_strength,
            ]
            if link_types:
                filters_source.append(KnowledgeLinkModel.link_type.in_(link_types))
                filters_target.append(KnowledgeLinkModel.link_type.in_(link_types))

            # Union both directions
            from sqlalchemy import union_all
            stmt_source = (
                select(KnowledgeLinkModel)
                .where(and_(*filters_source))
            )
            stmt_target = (
                select(KnowledgeLinkModel)
                .where(and_(*filters_target))
            )

            results = []
            for stmt in [stmt_source, stmt_target]:
                result = await session.execute(stmt)
                for row in result.scalars().all():
                    # Determine the "other" side of the link
                    if row.source_id == entry_id and row.source_tier == tier:
                        linked_id, linked_tier = row.target_id, row.target_tier
                    else:
                        linked_id, linked_tier = row.source_id, row.source_tier

                    results.append(KnowledgeLink(
                        id=row.id,
                        source_id=row.source_id,
                        source_tier=row.source_tier,
                        target_id=linked_id,
                        target_tier=linked_tier,
                        link_type=row.link_type,
                        strength=row.strength,
                        evidence=row.evidence,
                        created_at=row.created_at,
                    ))

        return results

    async def expand_search_results(
        self,
        results: list[SearchResult],
        max_expansion: int | None = None,
        min_strength: float | None = None,
    ) -> list[SearchResult]:
        """Expand search results by including linked entries.

        For each result, find linked entries not already in results.
        Score = original_score * link_strength * 0.5.
        Expanded entries are marked with metadata["expanded_via_link"] = True.

        Args:
            results: Original search results.
            max_expansion: Max additional entries (default from config).
            min_strength: Min link strength (default from config).

        Returns:
            Original results + expanded entries, sorted by score.
        """
        if not self._config.knowledge_links.search_expansion_enabled:
            return results

        max_exp = max_expansion or self._config.knowledge_links.search_expansion_max
        min_str = min_strength or self._config.knowledge_links.search_expansion_min_strength

        existing_ids = {(r.tier, r.id) for r in results}
        candidates: list[tuple[float, int, str, str]] = []  # (score, id, tier, link_type)

        for r in results:
            linked = await self.get_linked(r.id, r.tier, min_strength=min_str)
            for link in linked:
                key = (link.target_tier, link.target_id)
                if key not in existing_ids:
                    score = r.score * link.strength * 0.5
                    candidates.append((score, link.target_id, link.target_tier, link.link_type))
                    existing_ids.add(key)

        # Sort by score, take top N
        candidates.sort(key=lambda x: x[0], reverse=True)
        top = candidates[:max_exp]

        # Fetch content for expanded entries
        for score, entry_id, tier, link_type in top:
            content = await self._fetch_content(entry_id, tier)
            if content:
                results.append(SearchResult(
                    tier=tier,
                    id=entry_id,
                    score=score,
                    content=content,
                    metadata={"expanded_via_link": True, "link_type": link_type},
                ))

        return results

    # ── Link Builders ────────────────────────────────────────────────────

    async def _link_shared_entities(self, scope: str | None) -> int:
        """Link entries that reference the same entities via tags."""
        created = 0

        # Collect entity references from journal tags
        async with self._db.session() as session:
            result = await session.execute(
                select(JournalEntryModel.id, JournalEntryModel.tags)
                .where(JournalEntryModel.tags.isnot(None))
            )
            rows = result.all()

        # Build entity → entry_id mapping
        entity_entries: dict[str, list[tuple[int, str]]] = {}  # entity_key → [(id, tier)]
        for entry_id, tags in rows:
            if not tags:
                continue
            for tag in tags:
                m = _ENTITY_TAG_RE.match(str(tag))
                if m:
                    entity_key = f"{m.group(1)}/{m.group(2)}"
                    entity_entries.setdefault(entity_key, []).append((entry_id, "journal"))

        # Create pairwise links for entities with 2+ entries (cap at 10)
        for entity_key, entries in entity_entries.items():
            if len(entries) < 2 or len(entries) > 10:
                continue
            for i in range(len(entries)):
                for j in range(i + 1, len(entries)):
                    if created >= 100:
                        break
                    c = await self._create_link(
                        entries[i][0], entries[i][1],
                        entries[j][0], entries[j][1],
                        "shared_entity", 0.7,
                        f"Both reference entity:{entity_key}",
                        scope,
                    )
                    created += c

        return created

    async def _link_shared_tags(self, scope: str | None) -> int:
        """Link entries that share non-meta tags."""
        created = 0

        async with self._db.session() as session:
            result = await session.execute(
                select(JournalEntryModel.id, JournalEntryModel.tags)
                .where(JournalEntryModel.tags.isnot(None))
            )
            rows = result.all()

        # Build tag → entry_id mapping (exclude meta tags and entity tags)
        tag_entries: dict[str, list[int]] = {}
        for entry_id, tags in rows:
            if not tags:
                continue
            for tag in tags:
                tag_str = str(tag)
                if tag_str in _META_TAGS or _ENTITY_TAG_RE.match(tag_str):
                    continue
                tag_entries.setdefault(tag_str, []).append(entry_id)

        # Create links for tag groups with 2-10 entries
        min_tags = self._config.knowledge_links.min_shared_tags
        for tag, entries in tag_entries.items():
            if len(entries) < 2 or len(entries) > 10:
                continue

            # Calculate how many tags each pair shares
            for i in range(len(entries)):
                for j in range(i + 1, len(entries)):
                    if created >= 100:
                        break
                    strength = min(0.3 + 0.1 * min_tags, 0.7)
                    c = await self._create_link(
                        entries[i], "journal",
                        entries[j], "journal",
                        "shared_tag", strength,
                        f"Both tagged: {tag}",
                        scope,
                    )
                    created += c

        return created

    async def _link_temporal_proximity(self, scope: str | None) -> int:
        """Link journal entries within a temporal window in the same session."""
        created = 0
        window = timedelta(minutes=self._config.knowledge_links.temporal_window_minutes)

        async with self._db.session() as session:
            # Get recent journal entries ordered by time, grouped by session
            result = await session.execute(
                select(
                    JournalEntryModel.id,
                    JournalEntryModel.session_id,
                    JournalEntryModel.created_at,
                )
                .where(JournalEntryModel.session_id.isnot(None))
                .order_by(JournalEntryModel.session_id, JournalEntryModel.created_at)
                .limit(200)
            )
            rows = result.all()

        # Group by session
        sessions: dict[str, list[tuple[int, datetime]]] = {}
        for entry_id, session_id, created_at in rows:
            if session_id:
                sessions.setdefault(session_id, []).append((entry_id, created_at))

        # Create links for entries within the temporal window
        for session_entries in sessions.values():
            for i in range(len(session_entries)):
                for j in range(i + 1, len(session_entries)):
                    if created >= 50:
                        break
                    id_a, time_a = session_entries[i]
                    id_b, time_b = session_entries[j]
                    if abs((time_b - time_a).total_seconds()) <= window.total_seconds():
                        c = await self._create_link(
                            id_a, "journal", id_b, "journal",
                            "temporal", 0.4,
                            f"Within {self._config.knowledge_links.temporal_window_minutes}min window",
                            scope,
                        )
                        created += c

        return created

    async def create_pattern_links(
        self, entry_ids: list[int], tier: str = "journal",
        scope: str | None = None,
    ) -> int:
        """Create pattern links between entries that contributed to a synthesis pattern."""
        created = 0
        for i in range(len(entry_ids)):
            for j in range(i + 1, len(entry_ids)):
                c = await self._create_link(
                    entry_ids[i], tier, entry_ids[j], tier,
                    "pattern", 0.6,
                    "Nightly synthesis detected cross-cutting pattern",
                    scope,
                )
                created += c
        return created

    async def cleanup_orphans(self) -> int:
        """Delete links referencing deleted entries."""
        if not self._db.is_postgres:
            return 0  # SQLite doesn't support the subquery pattern well

        deleted = 0
        try:
            async with self._db.session() as session:
                result = await session.execute(sa_text("""
                    DELETE FROM nmem_knowledge_links
                    WHERE (source_tier = 'journal'
                           AND source_id NOT IN (SELECT id FROM nmem_journal_entries))
                       OR (target_tier = 'journal'
                           AND target_id NOT IN (SELECT id FROM nmem_journal_entries))
                """))
                deleted = result.rowcount or 0
        except Exception as e:
            logger.debug("Orphan link cleanup failed: %s", e)
        return deleted

    # ── Helpers ───────────────────────────────────────────────────────────

    async def _create_link(
        self,
        source_id: int, source_tier: str,
        target_id: int, target_tier: str,
        link_type: str, strength: float,
        evidence: str, scope: str | None,
    ) -> int:
        """Create a link if it doesn't exist. Returns 1 if created, 0 if exists."""
        # Normalize direction (smaller id first) to avoid duplicates
        if (source_tier, source_id) > (target_tier, target_id):
            source_id, source_tier, target_id, target_tier = (
                target_id, target_tier, source_id, source_tier
            )

        try:
            async with self._db.session() as session:
                # Check if link exists
                existing = await session.execute(
                    select(KnowledgeLinkModel.id).where(
                        KnowledgeLinkModel.source_id == source_id,
                        KnowledgeLinkModel.source_tier == source_tier,
                        KnowledgeLinkModel.target_id == target_id,
                        KnowledgeLinkModel.target_tier == target_tier,
                        KnowledgeLinkModel.link_type == link_type,
                    )
                )
                if existing.scalar_one_or_none():
                    return 0

                session.add(KnowledgeLinkModel(
                    source_id=source_id,
                    source_tier=source_tier,
                    target_id=target_id,
                    target_tier=target_tier,
                    link_type=link_type,
                    strength=strength,
                    evidence=evidence,
                    project_scope=scope,
                ))
            return 1
        except Exception as e:
            logger.debug("Link creation failed: %s", e)
            return 0

    async def _fetch_content(self, entry_id: int, tier: str) -> str | None:
        """Fetch content for an entry by ID and tier."""
        model_map = {
            "journal": JournalEntryModel,
            "ltm": LTMModel,
            "entity": EntityMemoryModel,
        }
        model = model_map.get(tier)
        if not model:
            return None

        try:
            async with self._db.session() as session:
                result = await session.execute(
                    select(model.content).where(model.id == entry_id)
                )
                return result.scalar_one_or_none()
        except Exception:
            return None
