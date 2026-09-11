"""Stage 2 tests: durable narrative + checkpoint store, the per-agent narrative
writer (re-grounding), and their surfacing in wake()."""

from __future__ import annotations

import pytest

from nmem.continuity_store import (
    get_checkpoint,
    latest_narrative,
    save_checkpoint,
    write_narrative,
)
from nmem.types import ContinuityResult


# ── Store: narrative versioning + provenance ─────────────────────────────────


@pytest.mark.asyncio
async def test_narrative_is_append_versioned_with_provenance(mem):
    v1 = await write_narrative(
        mem._db, "agent-a", None,
        current_period="I have been building the continuity layer.",
        longer_trajectory="I began on memory tiers; now on continuity.",
        provenance=[1, 2, 3], token_len=12, full_reconstruction=True,
    )
    v2 = await write_narrative(
        mem._db, "agent-a", None,
        current_period="Lately I wired the narrative writer.",
        longer_trajectory=None, provenance=[4, 5], token_len=7,
        full_reconstruction=True,
    )
    assert (v1, v2) == (1, 2)  # append, monotonic

    latest = await latest_narrative(mem._db, "agent-a")
    assert latest["version"] == 2
    assert latest["current_period"].startswith("Lately")
    assert latest["provenance"] == [4, 5]  # drift auditable back to episodes


@pytest.mark.asyncio
async def test_narrative_is_agent_scoped(mem):
    await write_narrative(mem._db, "a", None, current_period="A story",
                          longer_trajectory=None, provenance=[], token_len=1,
                          full_reconstruction=True)
    assert await latest_narrative(mem._db, "b") is None


# ── Store: checkpoint upsert ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_checkpoint_upserts_one_row_and_preserves_fields(mem):
    await save_checkpoint(mem._db, "agent-a", None,
                          interrupted_work="refactoring the assembler")
    await save_checkpoint(mem._db, "agent-a", None,
                          last_action="ran the test suite")
    ck = await get_checkpoint(mem._db, "agent-a")
    # Second upsert updated last_action but preserved interrupted_work (partial).
    assert ck["interrupted_work"] == "refactoring the assembler"
    assert ck["last_action"] == "ran the test suite"


# ── Narrative writer: re-grounding (Gap 2) ───────────────────────────────────


class _FakeNarrativeLLM:
    """Returns a fixed JSON narrative so the writer path is exercised without a
    live model (the noop provider returns nothing → writer correctly skips).
    Captures the last context so tests can assert what grounding was supplied."""

    def __init__(self):
        self.last_user = None

    async def complete_json(self, system, user, **kw):
        self.last_user = user
        return {"current_period": "I investigated the wake snapshot and shipped Stage 1.",
                "longer_trajectory": "I have moved from design into implementation."}

    async def complete(self, *a, **k):
        return ""


@pytest.mark.asyncio
async def test_narrative_writer_grounds_in_episodes(mem):
    # Seed episodic memory for one agent.
    for i in range(4):
        await mem.journal.add(agent_id="agent-a", entry_type="session_summary",
                              title=f"Worked on wake snapshot part {i}",
                              content="details", importance=6)
    # Swap in a deterministic LLM for the consolidator.
    mem._consolidator._llm = _FakeNarrativeLLM()

    await mem._consolidator.run_narrative_synthesis()

    n = await latest_narrative(mem._db, "agent-a")
    assert n is not None
    assert "Stage 1" in n["current_period"]
    # Provenance points back to the seeded journal ids (re-grounded from source).
    assert len(n["provenance"]) >= 4
    assert n["token_len"] > 0


@pytest.mark.asyncio
async def test_narrative_grounds_in_episode_content_not_mutable_ltm(mem):
    # A promoted entry keeps a lossy stub; the LTM row it points at is mutable
    # (title-keyed upsert / dedup merge). The writer must ground in the episode's
    # OWN content (the stub), never the possibly-reassigned LTM content — correct
    # attribution over richness.
    from sqlalchemy import select

    from nmem.db.models import JournalEntryModel

    await mem.ltm.save(agent_id="agent-a", category="procedure", key="shared_title",
                       content="UNRELATED content that LTM was later reassigned to.",
                       importance=8)
    await mem.journal.add(agent_id="agent-a", entry_type="session_summary",
                          title="did deep work on the assembler",
                          content="the real account of my assembler work", importance=7)
    async with mem._db.session() as s:
        row = (await s.execute(select(JournalEntryModel).where(
            JournalEntryModel.agent_id == "agent-a"))).scalars().first()
        row.promoted_to_ltm = True
        row.content = "stub: did deep work on the assembler"
        row.pointers = [{"type": "ltm", "id": 999999, "key": "shared_title"}]
    for i in range(2):
        await mem.journal.add(agent_id="agent-a", entry_type="note",
                              title=f"note {i}", content="x", importance=5)

    fake = _FakeNarrativeLLM()
    mem._consolidator._llm = fake
    await mem._consolidator.run_narrative_synthesis()

    assert "stub: did deep work" in fake.last_user           # episode's own record
    assert "UNRELATED content" not in fake.last_user         # no misattribution


@pytest.mark.asyncio
async def test_narrative_writer_skips_on_empty_llm(mem):
    # Default fixture uses the noop LLM → no narrative should be written.
    await mem.journal.add(agent_id="agent-a", entry_type="session_summary",
                          title="did a thing", content="x", importance=6)
    await mem.journal.add(agent_id="agent-a", entry_type="session_summary",
                          title="did another", content="x", importance=6)
    await mem.journal.add(agent_id="agent-a", entry_type="session_summary",
                          title="did a third", content="x", importance=6)
    await mem._consolidator.run_narrative_synthesis()
    assert await latest_narrative(mem._db, "agent-a") is None


@pytest.mark.asyncio
async def test_narrative_writer_excludes_synthetic_writers(mem):
    for i in range(5):
        await mem.journal.add(agent_id="nmem-sym", entry_type="extraction",
                              title=f"extracted {i}", content="x", importance=6)
    mem._consolidator._llm = _FakeNarrativeLLM()
    await mem._consolidator.run_narrative_synthesis()
    # nmem-sym is a synthetic writer, not an agent whose life-story we narrate.
    assert await latest_narrative(mem._db, "nmem-sym") is None


# ── wake() surfaces narrative + checkpoint ───────────────────────────────────


def test_stale_narrative_is_marked_historical():
    from datetime import datetime, timedelta, timezone

    from nmem.continuity import assemble_continuity

    now = datetime(2026, 9, 9, 12, 0, 0, tzinfo=timezone.utc)
    base = dict(agent_id="m", now=now, identity=None, recent=[], commitments=[],
                curiosity=[], policies=[], relevant=[], sym=None, max_tokens=1500,
                k_open_loops=7)

    stale = assemble_continuity(**base, narrative={
        "current_period": "Lately I have been doing X.", "longer_trajectory": None,
        "grounded_at": now - timedelta(days=120)})
    assert "historical" in stale.content

    fresh = assemble_continuity(**base, narrative={
        "current_period": "Lately I have been doing X.", "longer_trajectory": None,
        "grounded_at": now - timedelta(days=2)})
    assert "as of 2026-09-07" in fresh.content and "historical" not in fresh.content


@pytest.mark.asyncio
async def test_wake_surfaces_narrative_and_checkpoint(mem):
    await write_narrative(mem._db, "agent-a", None,
                          current_period="I have been shipping the continuity layer.",
                          longer_trajectory="From design to implementation.",
                          provenance=[1], token_len=10, full_reconstruction=True)
    await save_checkpoint(mem._db, "agent-a", None,
                          interrupted_work="wiring narrative into wake",
                          expected_next_action="run codex")

    r = await mem.wake("agent-a")

    assert isinstance(r, ContinuityResult)
    assert r.has_narrative and r.has_checkpoint
    assert "shipping the continuity layer" in r.content
    assert "wiring narrative into wake" in r.content
    # Canonical order: narrative then immediate, both before other lanes.
    assert r.sections[:2] == ("narrative", "immediate")


@pytest.mark.asyncio
async def test_end_session_records_checkpoint_from_flushed_content(mem):
    # Session working memory → flushed to journal → checkpoint captures the real
    # work (the slot content), not the bookkeeping flush title.
    await mem.working.set("sess-1", "agent-a", "task",
                          "finishing the narrative writer and wiring wake")
    flushed = await mem.end_session("sess-1", "agent-a")
    assert flushed == 1
    ck = await mem.get_continuity_checkpoint("agent-a")
    assert ck is not None
    assert "narrative writer" in ck["last_interaction_summary"]
    assert "working memory" not in ck["last_interaction_summary"]  # not the title


@pytest.mark.asyncio
async def test_end_session_no_flush_no_checkpoint_clobber(mem):
    # An explicit summary must survive an end_session that flushes nothing.
    await mem.save_continuity_checkpoint("agent-a",
                                         last_interaction_summary="curated summary")
    await mem.end_session("sess-empty", "agent-a")  # no working-memory slots
    ck = await mem.get_continuity_checkpoint("agent-a")
    assert ck["last_interaction_summary"] == "curated summary"
