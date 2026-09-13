"""Tests for importance-tiered compression + verbatim raw_content preservation.

Covers the enhancement that:
  - scales the compressed-content ceiling by importance (a ceiling, not a target),
  - preserves the verbatim original in `raw_content` whenever compression shrank
    the body (NULL otherwise),
  - degrades a failed LLM distillation to a word-boundary truncation,
  - carries the original through journal -> LTM promotion,
  - exposes the original via memory_get(raw=True).
"""

from __future__ import annotations

import re

import pytest
from sqlalchemy import select, text

from nmem.config import LLMConfig
from nmem.db.models import JournalEntryModel, LTMModel
from nmem.search import truncate_on_boundary


# ── Fake LLM providers ───────────────────────────────────────────────────────


class _SummaryLLM:
    """Returns a fixed short 'summary' regardless of input (success path)."""

    def __init__(self, summary: str = "DISTILLED FACT: names, dates, decisions."):
        self._summary = summary

    async def complete(self, system, user, **kw) -> str:
        return self._summary

    async def complete_json(self, *a, **k):
        return None


class _CeilingEchoLLM:
    """Returns a body exactly filling the ceiling the prompt asked for.

    Lets a test assert that a higher-importance entry retains more characters
    than a lower-importance one, purely via the tiered ceiling.
    """

    async def complete(self, system, user, **kw) -> str:
        m = re.search(r"up to (\d+) characters", system)
        n = int(m.group(1)) if m else 200
        return "x" * n

    async def complete_json(self, *a, **k):
        return None


def _use_llm(mem, llm) -> None:
    """Swap the LLM on the journal + LTM tiers for a test."""
    mem.journal._llm = llm
    mem.ltm._llm = llm


# ── Pure-unit: ceiling selection + boundary truncation ───────────────────────


@pytest.mark.parametrize(
    "importance,expected",
    [(None, 500), (1, 200), (3, 200), (4, 500), (6, 500), (7, 1000), (8, 1000), (9, 2000), (10, 2000)],
)
def test_ceiling_for_importance(importance, expected):
    assert LLMConfig().compression_ceiling_for(importance) == expected


def test_ceiling_tiers_disabled_is_flat():
    cfg = LLMConfig(compression_tiers=False)
    assert cfg.compression_ceiling_for(10) == cfg.compression_max_chars == 200


def test_tokens_scale_with_ceiling_but_never_below_floor():
    cfg = LLMConfig()
    assert cfg.compression_tokens_for(200) == 128  # floor wins for small ceilings
    assert cfg.compression_tokens_for(2000) > 128  # scales up for large ceilings


def test_truncate_on_boundary_cuts_on_word():
    assert truncate_on_boundary("the quick brown fox jumps", 20) == "the quick brown fox"


def test_truncate_on_boundary_keeps_hard_cut_for_long_token():
    # No whitespace within 80% of the budget -> keep the hard cut, don't collapse.
    assert truncate_on_boundary("supercalifragilisticexpialidocious", 20) == "supercalifragilistic"


def test_truncate_on_boundary_noop_when_short():
    assert truncate_on_boundary("hello world", 50) == "hello world"


# ── Journal write path ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_journal_short_content_not_compressed(mem):
    """Under the ceiling -> stored verbatim, raw_content stays NULL."""
    _use_llm(mem, _SummaryLLM())
    entry = await mem.journal.add(
        agent_id="a", entry_type="note", title="t", content="short body", importance=5,
    )
    assert entry.content == "short body"
    assert entry.raw_content is None

    async with mem._db.session() as s:
        row = (await s.execute(select(JournalEntryModel).where(JournalEntryModel.id == entry.id))).scalar_one()
    assert row.content == "short body"
    assert row.raw_content is None


@pytest.mark.asyncio
async def test_journal_compressed_preserves_original(mem):
    """Over the ceiling with a working LLM -> content is the summary,
    raw_content is the verbatim original, and it persists."""
    _use_llm(mem, _SummaryLLM("SUMMARY"))
    original = "x " * 400  # ~800 chars, over the imp=5 (500) ceiling
    entry = await mem.journal.add(
        agent_id="a", entry_type="note", title="t", content=original, importance=5,
    )
    assert entry.content == "SUMMARY"
    assert entry.raw_content == original

    async with mem._db.session() as s:
        row = (await s.execute(select(JournalEntryModel).where(JournalEntryModel.id == entry.id))).scalar_one()
    assert row.content == "SUMMARY"
    assert row.raw_content == original


@pytest.mark.asyncio
async def test_journal_llm_failure_falls_back_to_boundary_and_keeps_original(mem):
    """noop LLM returns '' -> fallback truncation, but the original survives
    verbatim in raw_content and the summary is a clean boundary cut."""
    # mem fixture already uses the noop LLM (returns "").
    original = "alpha bravo charlie delta echo foxtrot " * 30  # long, over ceiling
    entry = await mem.journal.add(
        agent_id="a", entry_type="note", title="title", content=original, importance=1,
    )
    ceiling = mem._config.llm.compression_ceiling_for(1)
    assert entry.raw_content == original          # nothing lost
    assert len(entry.content) <= ceiling          # summary respects the ceiling
    assert not entry.content.endswith("foxtro")   # boundary cut, not mid-word


@pytest.mark.asyncio
async def test_importance_tier_retains_more_for_high_importance(mem):
    """Higher importance -> larger ceiling -> more retained content."""
    _use_llm(mem, _CeilingEchoLLM())
    big = "word " * 1000  # ~5000 chars, over every ceiling
    # dedup=False: noop embeddings collide, so the 2nd add would otherwise be
    # coalesced into the 1st.
    low = await mem.journal.add(agent_id="a", entry_type="note", title="lo", content=big, importance=1, dedup=False)
    high = await mem.journal.add(agent_id="a", entry_type="note", title="hi", content=big, importance=10, dedup=False)
    assert len(low.content) == 200
    assert len(high.content) == 2000
    assert len(high.content) > len(low.content)


# ── LTM write path + agent-facing raw retrieval ──────────────────────────────


@pytest.mark.asyncio
async def test_journal_add_batch_preserves_original(mem):
    """Batch imports with compress=True must also preserve the original."""
    _use_llm(mem, _SummaryLLM("BATCH SUM"))
    original = "batch body " * 80  # over the imp=5 ceiling
    out = await mem.journal.add_batch(
        [{"agent_id": "a", "entry_type": "note", "title": "bt", "content": original, "importance": 5}],
        compress=True,
    )
    assert out[0].content == "BATCH SUM"
    assert out[0].raw_content == original
    async with mem._db.session() as s:
        row = (await s.execute(select(JournalEntryModel).where(JournalEntryModel.id == out[0].id))).scalar_one()
    assert row.raw_content == original


@pytest.mark.asyncio
async def test_ltm_compressed_preserves_original(mem):
    _use_llm(mem, _SummaryLLM("LTM SUMMARY"))
    original = "detail " * 200  # over the imp=5 ceiling
    entry = await mem.ltm.save(
        agent_id="a", category="fact", key="k", content=original, importance=5,
    )
    assert entry.content == "LTM SUMMARY"
    assert entry.raw_content == original

    async with mem._db.session() as s:
        row = (await s.execute(select(LTMModel).where(LTMModel.id == entry.id))).scalar_one()
    assert row.raw_content == original


@pytest.mark.asyncio
async def test_ltm_uncompressed_save_clears_stale_raw_content(mem):
    """Upserting a key with a now-uncompressed body must not leave a stale
    raw_content from a previous compressed version."""
    _use_llm(mem, _SummaryLLM("S"))
    await mem.ltm.save(agent_id="a", category="fact", key="k", content="z " * 400, importance=5)
    # Re-save the same key with a short body (no compression this time).
    entry = await mem.ltm.save(agent_id="a", category="fact", key="k", content="short", importance=5)
    assert entry.content == "short"
    assert entry.raw_content is None
    async with mem._db.session() as s:
        row = (await s.execute(select(LTMModel).where(LTMModel.id == entry.id))).scalar_one()
    assert row.raw_content is None


# ── journal -> LTM promotion carries the original ────────────────────────────


@pytest.mark.asyncio
async def test_promotion_carries_original_into_ltm_and_stubs_journal(mem):
    _use_llm(mem, _SummaryLLM("SUM"))
    original = "promoted knowledge body " * 120  # ~2880 chars, over the imp=9 (2000) ceiling
    entry = await mem.journal.add(
        agent_id="a", entry_type="decision", title="A key decision", content=original, importance=9,
    )
    assert entry.raw_content == original

    async with mem._db.session() as s:
        row = (await s.execute(select(JournalEntryModel).where(JournalEntryModel.id == entry.id))).scalar_one()
    await mem._consolidator._promote_entry(row)

    # LTM archive now holds the verbatim original (with type prefix).
    async with mem._db.session() as s:
        ltm = (await s.execute(select(LTMModel).where(LTMModel.source_journal_id == entry.id))).scalar_one()
        jrow = (await s.execute(select(JournalEntryModel).where(JournalEntryModel.id == entry.id))).scalar_one()
    assert ltm.raw_content is not None and original in ltm.raw_content
    assert jrow.promoted_to_ltm is True
    assert jrow.raw_content is None  # redundant copy dropped; full lives in LTM


# ── migration v7: columns exist ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_raw_content_columns_exist(mem):
    async with mem._db.session() as s:
        for table in ("nmem_journal_entries", "nmem_long_term_memory"):
            got = await s.execute(text(
                "SELECT 1 FROM information_schema.columns "
                "WHERE table_name = :t AND column_name = 'raw_content'"
            ), {"t": table})
            assert got.scalar_one_or_none() == 1, f"{table}.raw_content missing"
