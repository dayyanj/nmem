"""
Knowledge link endpoints.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from nmem import MemorySystem
from nmem.api.main import get_mem
from nmem.api.schemas import KnowledgeLinkResponse, to_dict

router = APIRouter(prefix="/v1/links", tags=["links"])


@router.get("/{tier}/{entry_id}", response_model=list[KnowledgeLinkResponse])
async def get_linked_entries(
    tier: str,
    entry_id: int,
    link_types: str | None = Query(None, description="Comma-separated filter"),
    min_strength: float = Query(0.0, ge=0.0, le=1.0),
    mem: MemorySystem = Depends(get_mem),
) -> list[dict]:
    """Get entries linked to a specific memory entry via associative links.

    Unlike semantic search, knowledge links connect entries that share entities,
    tags, temporal proximity, or causal relationships.
    """
    types_filter = [t.strip() for t in link_types.split(",")] if link_types else None
    links = await mem.links.get_linked(
        entry_id=entry_id,
        tier=tier,
        link_types=types_filter,
        min_strength=min_strength,
    )
    return [to_dict(link) for link in links]
