"""Tests for deep-recall / focus expansion (Altitude 1).

Focus expansion lives in the SHARED renderer LTMTier.build_prompt — the path
PromptBuilder uses, i.e. agent_core chat (Michelle) and the memory_context MCP
tool alike. It resolves the top-k most-relevant entries to a query-relevant
passage of their verbatim raw_content, reallocating WITHIN the section budget
(depth for focus, breadth traded from the tail) — never inflating the section.
Off by default.

Also covers the frozen-mutation fix in extract_passages_for_results.
"""

from __future__ import annotations

import pytest

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


def _cfg(focus: bool, *, max_chars: int = 100000, top_k: int = 2) -> NmemConfig:
    return NmemConfig(
        database_url=TEST_DB_URL,
        embedding={"provider": "noop", "dimensions": 384},
        llm={"provider": "noop"},
        consolidation={"enabled": False},
        prompt={
            "focus_expansion": focus,
            "focus_expansion_top_k": top_k,
            "focus_expansion_max_chars": max_chars,
        },
    )


async def _fresh_system(cfg: NmemConfig) -> MemorySystem:
    system = MemorySystem(cfg)
    await system.initialize()
    await reset_db(system)
    system.ltm._llm = _SummaryLLM()
    system.journal._llm = _SummaryLLM()
    return system


async def _seed_ltm(system: MemorySystem, key: str = "quantum_pumpkins") -> None:
    await system.ltm.save(
        agent_id="a", category="fact", key=key,
        content=LONG_ORIGINAL, importance=8, grounding="confirmed",
    )


# ── default ──────────────────────────────────────────────────────────────────


def test_focus_expansion_on_by_default():
    """Focus expansion ships ON as of the default flip; guards against silent
    revert. (Set NMEM_PROMPT__FOCUS_EXPANSION=false to opt out.)"""
    assert NmemConfig().prompt.focus_expansion is True


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
    system = await _fresh_system(_cfg(focus=False))
    try:
        await _seed_ltm(system)
        results = await system.search("a", "pumpkins", top_k=10, bump_access=False)
        ltm_hits = [r for r in results if r.tier == "ltm"]
        assert ltm_hits, "expected the seeded LTM entry to be found"
        r = ltm_hits[0]
        assert r.content == SUMMARY                  # compact summary stored
        assert r.raw_content == LONG_ORIGINAL        # original carried through
    finally:
        await reset_db(system)
        await system.close()


# ── focus expansion behaviour (on the shared build_prompt path) ──────────────


@pytest.mark.asyncio
async def test_focus_expansion_injects_depth_beyond_summary():
    system = await _fresh_system(_cfg(focus=True))
    try:
        await _seed_ltm(system)
        section = await system.ltm.build_prompt("a", query="pumpkins", max_chars=4000)
        # The verbatim marker (present only in raw_content, not the summary)
        # made it into the LTM section.
        assert MARKER in section
        # And it flows through the PromptBuilder path Michelle uses.
        injection = (await system.prompt.build(agent_id="a", query="pumpkins")).full_injection
        assert MARKER in injection
    finally:
        await reset_db(system)
        await system.close()


@pytest.mark.asyncio
async def test_focus_expansion_off_is_flat():
    system = await _fresh_system(_cfg(focus=False))
    try:
        await _seed_ltm(system)
        section = await system.ltm.build_prompt("a", query="pumpkins", max_chars=4000)
        # Legacy behaviour: only the compact summary, never the verbatim marker.
        assert MARKER not in section
        assert SUMMARY in section
    finally:
        await reset_db(system)
        await system.close()


@pytest.mark.asyncio
async def test_focus_expansion_oversized_cap_degrades_to_summary_not_empty():
    """With a pathological max_chars >= the section budget, a focus entry that
    can't fit must degrade to the compact summary — never be dropped."""
    system = await _fresh_system(_cfg(focus=True, max_chars=100000))
    try:
        await _seed_ltm(system)
        section = await system.ltm.build_prompt("a", query="pumpkins", max_chars=500)
        assert "quantum_pumpkins" in section   # entry still rendered
        assert SUMMARY in section               # degraded to summary, not empty
        assert MARKER not in section            # deep body didn't fit
    finally:
        await reset_db(system)
        await system.close()


@pytest.mark.asyncio
async def test_focus_expansion_respects_section_budget():
    """Expansion reallocates within max_chars; it must not exceed it."""
    system = await _fresh_system(_cfg(focus=True))
    try:
        for i in range(6):
            await _seed_ltm(system, key=f"k{i}")
        max_chars = 1500
        section = await system.ltm.build_prompt("a", query="pumpkins", max_chars=max_chars)
        assert len(section) <= max_chars
    finally:
        await reset_db(system)
        await system.close()
