"""
Cross-tier unified search engine.

Searches across all memory tiers in parallel, merges and ranks results.
Implements the hybrid search algorithm (60/40 vector/FTS weighting).
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from nmem.types import SearchResult

if TYPE_CHECKING:
    from nmem.tiers.journal import JournalTier
    from nmem.tiers.ltm import LTMTier
    from nmem.tiers.shared import SharedTier
    from nmem.tiers.entity import EntityTier

logger = logging.getLogger(__name__)

# Default tiers to search
DEFAULT_TIERS = ("journal", "ltm", "shared", "entity")


async def cross_tier_search(
    agent_id: str,
    query: str,
    *,
    journal: JournalTier,
    ltm: LTMTier,
    shared: SharedTier,
    entity: EntityTier,
    tiers: tuple[str, ...] | None = None,
    top_k: int = 10,
) -> list[SearchResult]:
    """Search across multiple memory tiers in parallel.

    Args:
        agent_id: Agent identifier.
        query: Search query text.
        journal: Journal tier instance.
        ltm: LTM tier instance.
        shared: Shared tier instance.
        entity: Entity tier instance.
        tiers: Which tiers to search (default: all).
        top_k: Maximum total results.

    Returns:
        List of SearchResult objects, ranked by score.
    """
    tiers = tiers or DEFAULT_TIERS
    tasks = []

    if "journal" in tiers:
        tasks.append(_search_journal(agent_id, query, journal))
    if "ltm" in tiers:
        tasks.append(_search_ltm(agent_id, query, ltm))
    if "shared" in tiers:
        tasks.append(_search_shared(query, shared))
    if "entity" in tiers:
        tasks.append(_search_entity(query, entity))

    all_results: list[SearchResult] = []
    for coro_result in await asyncio.gather(*tasks, return_exceptions=True):
        if isinstance(coro_result, Exception):
            logger.warning("Tier search failed: %s", coro_result)
            continue
        all_results.extend(coro_result)

    # Sort by score descending, take top_k
    all_results.sort(key=lambda r: r.score, reverse=True)
    return all_results[:top_k]


async def _search_journal(
    agent_id: str, query: str, tier: JournalTier
) -> list[SearchResult]:
    entries = await tier.search(agent_id, query, top_k=5)
    return [
        SearchResult(
            tier="journal",
            id=e.id,
            score=e.importance / 10.0,  # Normalize to 0-1
            content=e.content,
            title=e.title,
            agent_id=e.agent_id,
            metadata={"entry_type": e.entry_type, "record_type": e.record_type},
        )
        for e in entries
    ]


async def _search_ltm(
    agent_id: str, query: str, tier: LTMTier
) -> list[SearchResult]:
    entries = await tier.search(agent_id, query, top_k=5)
    return [
        SearchResult(
            tier="ltm",
            id=e.id,
            score=(e.importance / 10.0) * e.confidence,
            content=e.content,
            key=e.key,
            agent_id=e.agent_id,
            metadata={"category": e.category, "confidence": e.confidence},
        )
        for e in entries
    ]


async def _search_shared(query: str, tier: SharedTier) -> list[SearchResult]:
    entries = await tier.search(query, top_k=5)
    return [
        SearchResult(
            tier="shared",
            id=e.id,
            score=e.importance / 10.0,
            content=e.content,
            key=e.key,
            metadata={"category": e.category, "confirmed": e.confirmed},
        )
        for e in entries
    ]


async def _search_entity(query: str, tier: EntityTier) -> list[SearchResult]:
    records = await tier.search(query, top_k=5)
    return [
        SearchResult(
            tier="entity",
            id=r.id,
            score=r.confidence,
            content=r.content,
            metadata={
                "entity_type": r.entity_type,
                "entity_id": r.entity_id,
                "record_type": r.record_type,
            },
        )
        for r in records
    ]
