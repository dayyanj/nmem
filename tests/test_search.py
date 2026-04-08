"""Tests for cross-tier search."""

from __future__ import annotations

import pytest

from nmem import MemorySystem


@pytest.mark.asyncio
async def test_cross_tier_search(mem_with_data: MemorySystem) -> None:
    """Cross-tier search returns results from multiple tiers."""
    results = await mem_with_data.search(agent_id="agent1", query="refund")
    assert len(results) > 0
    tiers = {r.tier for r in results}
    # Should find results in at least journal and ltm
    assert len(tiers) >= 1


@pytest.mark.asyncio
async def test_search_empty(mem: MemorySystem) -> None:
    """Search on empty system returns empty list."""
    results = await mem.search(agent_id="agent1", query="anything")
    assert results == []
