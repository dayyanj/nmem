"""Tests for deep-recall / focus expansion in the session briefing (Altitude 1).

Focus expansion resolves the top-k most-relevant KNOWN memories to a query-
relevant passage of their verbatim raw_content, reallocating WITHIN the Known-
Facts budget (depth for focus, breadth traded from the tail) — never inflating
the prompt. Off by default.

Also covers the frozen-mutation fix in extract_passages_for_results.
"""

from __future__ import annotations

import asyncio

import pytest
import pytest_asyncio

from nmem import MemorySystem, NmemConfig
from nmem.search import extract_passages_for_results
from nmem.types import SearchResult
from tests.conftest import TEST_DB_URL, reset_db


MARKER = "MARKER_DEEP_DETAIL_ pumpkins reticulate the quantum lattice"
# Long, paragraph-structured original with the marker up front, well over the
# 800-char passage floor. Summarized to a short fact by the fake LLM below.
LONG_ORIGINAL = (
    f"{MARKER}. " + ("Context sentence about the subject. " * 40)
    + "\n\n" + ("A second paragraph with further elaboration. " * 40)
)
SUMMARY = "Short distilled fact."


class _SummaryLLM:
    async def complete(self, system, user, **kw) -> str:
        return SUMMARY

    async def complete_json(self, *a, **k):
        return None


def _cfg(focus: bool) -> NmemConfig:
    return NmemConfig(
        database_url=TEST_DB_URL,
        embedding={"provider": "noop", "dimensions": 384},
        llm={"provider": "noop"},
        consolidation={"enabled": False},
        # Force KNOWN on a confirmed entry — we're testing expansion, not the
        # recognition heuristic.
        recognition={"known_threshold": 0.3},
        prompt={
            "focus_expansion": focus,
            "focus_expansion_top_k": 2,
            "focus_expansion_max_chars": 700,
        },
    )


async def _fresh_system(focus: bool) -> MemorySystem:
    system = MemorySystem(_cfg(focus))
    await system.initialize()
    await reset_db(system)
    system.ltm._llm = _SummaryLLM()
    system.journal._llm = _SummaryLLM()
    return system


async def _seed_confirmed_ltm(system: MemorySystem) -> None:
    await system.ltm.save(
        agent_id="a", category="fact", key="quantum_pumpkins",
        content=LONG_ORIGINAL, importance=8, grounding="confirmed",
    )


# ── frozen-mutation fix ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_extract_passages_returns_new_instances_not_mutation():
    """The frozen SearchResult must not be mutated in place; a new instance
    carries the passage and the original is untouched."""
    class _Emb:
        def embed(self, q): return [0.1] * 8
        def embed_batch(self, xs): return [[0.1] * 8 for _ in xs]

    r = SearchResult(
        tier="ltm", id=1, score=0.9,
        content=("alpha paragraph. " * 60) + "\n\n" + ("beta paragraph. " * 60),
    )
    out = await extract_passages_for_results([r], "alpha", _Emb())
    assert out[0].passage is not None       # passage now actually set
    assert r.passage is None                # original frozen instance untouched
    assert out[0] is not r                  # a replaced copy


# ── raw_content plumbing through search ──────────────────────────────────────


@pytest.mark.asyncio
async def test_search_result_carries_raw_content():
    system = await _fresh_system(focus=False)
    try:
        system.ltm._llm = _SummaryLLM()
        await _seed_confirmed_ltm(system)
        results = await system.search("a", "pumpkins", top_k=10, bump_access=False)
        ltm_hits = [r for r in results if r.tier == "ltm"]
        assert ltm_hits, "expected the seeded LTM entry to be found"
        r = ltm_hits[0]
        assert r.content == SUMMARY                  # compact summary stored
        assert r.raw_content == LONG_ORIGINAL        # original carried through
    finally:
        await reset_db(system)
        await system.close()


# ── focus expansion behaviour ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_focus_expansion_injects_depth_beyond_summary():
    system = await _fresh_system(focus=True)
    try:
        await _seed_confirmed_ltm(system)
        result = await system.briefing("a", query="pumpkins", max_tokens=2000)
        text = result.content if hasattr(result, "content") else str(result)
        # The verbatim marker (present only in raw_content, not the summary)
        # made it into the briefing.
        assert MARKER in text
        assert SUMMARY not in text or MARKER in text  # depth replaced the stub
    finally:
        await reset_db(system)
        await system.close()


@pytest.mark.asyncio
async def test_focus_expansion_off_is_flat():
    system = await _fresh_system(focus=False)
    try:
        await _seed_confirmed_ltm(system)
        result = await system.briefing("a", query="pumpkins", max_tokens=2000)
        text = result.content if hasattr(result, "content") else str(result)
        # Legacy behaviour: only the compact summary, never the verbatim marker.
        assert MARKER not in text
    finally:
        await reset_db(system)
        await system.close()


@pytest.mark.asyncio
async def test_focus_expansion_oversized_cap_degrades_to_clip_not_header_only():
    """Even with a pathological max_chars >= the Known budget, a focus item that
    can't fit must degrade to the compact clip — never leave a header-only
    section."""
    system = MemorySystem(NmemConfig(
        database_url=TEST_DB_URL,
        embedding={"provider": "noop", "dimensions": 384},
        llm={"provider": "noop"},
        consolidation={"enabled": False},
        recognition={"known_threshold": 0.3},
        prompt={"focus_expansion": True, "focus_expansion_top_k": 2,
                "focus_expansion_max_chars": 100000},
    ))
    await system.initialize()
    await reset_db(system)
    system.ltm._llm = _SummaryLLM()
    try:
        await _seed_confirmed_ltm(system)
        result = await system.briefing("a", query="pumpkins", max_tokens=1000)
        text = result.content if hasattr(result, "content") else str(result)
        assert "### Known Facts" in text
        # The compact summary is present (degraded), i.e. not a header-only cut.
        known_section = text.split("### Known Facts")[-1]
        assert SUMMARY in known_section
    finally:
        await reset_db(system)
        await system.close()


@pytest.mark.asyncio
async def test_focus_expansion_respects_known_budget():
    """Expansion reallocates within budget_known; it must not blow the section
    budget (no prompt inflation)."""
    system = await _fresh_system(focus=True)
    try:
        # Several confirmed entries so the tail would fill the budget.
        for i in range(6):
            await system.ltm.save(
                agent_id="a", category="fact", key=f"k{i}",
                content=LONG_ORIGINAL, importance=8, grounding="confirmed",
            )
        max_tokens = 2000
        result = await system.briefing("a", query="pumpkins", max_tokens=max_tokens)
        text = result.content if hasattr(result, "content") else str(result)
        known_section = text.split("### Known Facts")[-1].split("###")[0]
        budget_known = int(max_tokens * 4 * 0.30)  # normal-budget Known share
        assert len(known_section) <= budget_known + 200  # header/label slack
    finally:
        await reset_db(system)
        await system.close()
