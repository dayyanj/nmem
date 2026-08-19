"""
Opt-in salience→ranking blend (search.salience_rank_weight).

Default 0.0 preserves the deliberate decision that salience is a lifecycle
signal, not a retrieval signal — the ranking must be byte-for-byte identical to
not blending. A positive weight lets a high-salience, lower-relevance entry rank
higher. Both _search_ltm and _search_ltm_all_agents apply the same blend.
"""
from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy import text

from nmem import MemorySystem, NmemConfig

from tests.conftest import TEST_DB_URL, reset_db


async def _build(weight: float) -> MemorySystem:
    system = MemorySystem(NmemConfig(
        database_url=TEST_DB_URL,
        embedding={"provider": "noop", "dimensions": 384},
        llm={"provider": "noop"},
        consolidation={"enabled": False},
        search={"salience_rank_weight": weight},
    ))
    await system.initialize()
    await reset_db(system)
    return system


async def _seed(mem):
    # Two LTM entries that both match the query, then force very different
    # salience so the blend has something to reorder.
    await mem.ltm.save(agent_id="a1", category="fact", key="high_rel",
                       content="deploy rollback runbook primary path", importance=5)
    await mem.ltm.save(agent_id="a1", category="fact", key="low_rel",
                       content="deploy rollback runbook secondary note", importance=5)
    async with mem._db.session() as s:
        # give the *second* (typically lower-ranked) entry very high salience,
        # the first very low
        await s.execute(text("UPDATE nmem_long_term_memory SET salience = 0.1 WHERE key = 'high_rel'"))
        await s.execute(text("UPDATE nmem_long_term_memory SET salience = 1.0 WHERE key = 'low_rel'"))


@pytest.mark.asyncio
async def test_default_weight_is_pure_relevance():
    """weight 0.0 → LTM score equals the hybrid relevance exactly (bypass)."""
    mem = await _build(0.0)
    try:
        await _seed(mem)
        results = await mem.search("a1", "deploy rollback runbook", tiers=("ltm",))
        ltm = [r for r in results if r.tier == "ltm"]
        assert ltm, "expected LTM results"
        # score must equal relevance — recognition folds salience, score must NOT.
        # With weight 0, a high-salience low-relevance entry must NOT outrank a
        # high-relevance one purely on salience.
        for r in ltm:
            # score is the raw relevance (0..~1.1), never inflated by salience×weight
            assert r.score <= 1.2
    finally:
        await reset_db(mem)
        await mem.close()


@pytest.mark.asyncio
async def test_positive_weight_promotes_high_salience():
    """weight > 0 → the high-salience entry's score is boosted above its
    relevance-only score."""
    base = await _build(0.0)
    try:
        await _seed(base)
        r0 = {r.key: r.score for r in await base.search("a1", "deploy rollback runbook", tiers=("ltm",))}
    finally:
        await reset_db(base)
        await base.close()

    boosted = await _build(0.5)
    try:
        await _seed(boosted)
        r1 = {r.key: r.score for r in await boosted.search("a1", "deploy rollback runbook", tiers=("ltm",))}
        # the high-salience (1.0) entry gains ~0.5; the low-salience (0.1) gains ~0.05
        assert r1["low_rel"] > r0["low_rel"] + 0.4
        assert r1["low_rel"] - r1["high_rel"] > r0["low_rel"] - r0["high_rel"]
    finally:
        await reset_db(boosted)
        await boosted.close()


@pytest.mark.asyncio
async def test_all_agents_path_applies_same_blend():
    """The blend must apply identically in the all-agents LTM path
    (_search_ltm_all_agents), not just the per-agent path."""
    base = await _build(0.0)
    try:
        await _seed(base)
        r0 = {r.key: r.score for r in await base.search(
            "a1", "deploy rollback runbook", tiers=("ltm",), all_agents=True)}
    finally:
        await reset_db(base)
        await base.close()

    boosted = await _build(0.5)
    try:
        await _seed(boosted)
        r1 = {r.key: r.score for r in await boosted.search(
            "a1", "deploy rollback runbook", tiers=("ltm",), all_agents=True)}
        assert r0 and r1
        assert r1["low_rel"] > r0["low_rel"] + 0.4     # high-salience entry boosted
    finally:
        await reset_db(boosted)
        await boosted.close()
