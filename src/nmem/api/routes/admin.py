"""
Admin endpoints — manual consolidation triggers.

Import endpoints are deferred to Phase C (they need multipart form handling
and async background processing to avoid blocking the request).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from nmem import MemorySystem
from nmem.api.main import get_mem
from nmem.api.schemas import ConsolidationStatsResponse, to_dict

router = APIRouter(prefix="/v1/admin", tags=["admin"])


@router.post("/consolidate", response_model=ConsolidationStatsResponse)
async def trigger_full_consolidation(
    mem: MemorySystem = Depends(get_mem),
) -> dict:
    """Run a full consolidation cycle manually.

    Performs: journal decay, promotion (journal→LTM, LTM→shared),
    deduplication, confidence decay, knowledge link building, curiosity decay.
    """
    stats = await mem.consolidation.run_full_cycle()
    return to_dict(stats)


@router.post("/consolidate/nightly", response_model=ConsolidationStatsResponse)
async def trigger_nightly_synthesis(
    mem: MemorySystem = Depends(get_mem),
) -> dict:
    """Run nightly synthesis manually.

    Analyzes the last 24h of journal entries for cross-cutting patterns
    and saves them as shared knowledge. Uses the LLM for pattern extraction.
    """
    stats = await mem.consolidation.run_nightly_synthesis()
    return to_dict(stats)
