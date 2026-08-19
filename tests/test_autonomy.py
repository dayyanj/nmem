"""
Autonomy — nmem decides when to memorize (skill capture) and when to retrieve
(proactive surface), off the write path and without storming.

Covers: the opt-in gate registers no handler when off; heuristic skill capture
from qualifying journal entries; proactive `memory.surfaced` emission; the
source="autonomy" tag on self-initiated searches (storm guard); background
execution never delaying journal.add; and the request_surface reverse channel.
"""
from __future__ import annotations

import asyncio
import time

import pytest
import pytest_asyncio

from nmem import MemorySystem, NmemConfig

from tests.conftest import TEST_DB_URL, reset_db


def _make_config(**autonomy):
    auto = {"enabled": True, "cooldown_seconds": 0,
            "surface_recognition_threshold": 0.0, "novelty_threshold": 1.1}
    auto.update(autonomy)
    return NmemConfig(
        database_url=TEST_DB_URL,
        embedding={"provider": "noop", "dimensions": 384},
        llm={"provider": "noop"},
        consolidation={"enabled": False},
        skills={"enabled": True},
        autonomy=auto,
    )


async def _build(**autonomy) -> MemorySystem:
    system = MemorySystem(_make_config(**autonomy))
    await system.initialize()
    await reset_db(system)
    return system


async def _settle(mem, timeout: float = 3.0) -> None:
    """Wait until all backgrounded autonomy work has drained."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not mem.autonomy._inflight and not mem.autonomy._tasks:
            return
        await asyncio.sleep(0.02)


@pytest_asyncio.fixture
async def au() -> MemorySystem:
    system = await _build(auto_capture_skills=True, proactive_retrieve=True)
    yield system  # type: ignore[misc]
    await reset_db(system)
    await system.close()


# ── opt-in gate ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_disabled_registers_no_handler(mem):
    assert mem.autonomy._enabled is False
    assert mem._event_handlers.get("journal.added", []) == []


# ── autonomous memorize (capture) ──────────────────────────────

@pytest.mark.asyncio
async def test_captures_skill_from_qualifying_entry(au):
    await au.journal.add(
        agent_id="a1", entry_type="decision",
        title="Chose blue-green deploy",
        content="rolled out via blue green behind a feature flag, zero downtime",
        importance=6)
    await _settle(au)
    hits = await au.skills.find("rolled out via blue green behind a feature flag")
    assert len(hits) == 1


@pytest.mark.asyncio
async def test_ignores_non_qualifying_entry_type(au):
    await au.journal.add(
        agent_id="a1", entry_type="note",
        title="random note", content="just some passing observation xyzzy",
        importance=3)
    await _settle(au)
    hits = await au.skills.find("just some passing observation xyzzy")
    assert hits == []


# ── autonomous retrieve (proactive surface) ────────────────────

@pytest.mark.asyncio
async def test_proactive_surface_emits_memory_surfaced(au):
    # seed something to be recalled
    await au.ltm.save(agent_id="a1", category="fact",
                      key="stripe_refund", record_type="fact", grounding="confirmed",
                      content="stripe refunds must reconcile against the original charge id",
                      importance=8)
    events: list = []
    au.on("memory.surfaced")(lambda d: events.append(d))

    await au.journal.add(
        agent_id="a1", entry_type="note",
        title="thinking about refunds",
        content="stripe refunds must reconcile against the original charge id",
        importance=4)
    await _settle(au)

    assert events, "expected a memory.surfaced event"
    ev = events[0]
    assert ev["source"] == "autonomy" and ev["agent_id"] == "a1"
    assert ev["results"], "expected at least one surfaced result"


@pytest.mark.asyncio
async def test_proactive_search_is_tagged_autonomy(au):
    """Storm guard: self-initiated searches carry source='autonomy' so a
    cognitive backend can ignore them."""
    seen: list = []
    au.on("search.executed")(lambda d: seen.append(d.get("source")))
    await au.journal.add(
        agent_id="a1", entry_type="note", title="t",
        content="some content to trigger a proactive search abcdef", importance=3)
    await _settle(au)
    assert "autonomy" in seen
    # and it must be bounded — one trigger doesn't storm into many searches
    assert seen.count("autonomy") <= 2


@pytest.mark.asyncio
async def test_cooldown_bounds_search_frequency():
    """Cooldown bounds proactive-search FREQUENCY, not just successful offers —
    a burst of same-agent writes runs at most one proactive search per window."""
    m = await _build(proactive_retrieve=True, auto_capture_skills=False,
                     cooldown_seconds=60)
    try:
        seen: list = []
        m.on("search.executed")(lambda d: seen.append(d.get("source")))
        for i in range(3):
            await m.journal.add(agent_id="a1", entry_type="note", title=f"t{i}",
                                content=f"distinct content number {i} zzz", importance=3)
            await _settle(m)
        assert seen.count("autonomy") == 1
    finally:
        await reset_db(m)
        await m.close()


@pytest.mark.asyncio
async def test_autonomy_never_delays_journal_write(au, monkeypatch):
    """A deliberately slow autonomy pass must not sit in the journal.add path."""
    async def _slow(agent_id, entry_id):
        await asyncio.sleep(2.0)
    monkeypatch.setattr(au.autonomy, "_process", _slow)

    start = time.monotonic()
    await au.journal.add(agent_id="a1", entry_type="decision",
                         title="x", content="y", importance=5)
    elapsed = time.monotonic() - start
    assert elapsed < 0.5, f"journal.add blocked on autonomy ({elapsed:.2f}s)"


# ── request_surface reverse channel ────────────────────────────

@pytest.mark.asyncio
async def test_request_surface_returns_false_when_nothing(au):
    assert await au.request_surface("totally unseen query wibble", "a1") is False


@pytest.mark.asyncio
async def test_request_surface_returns_true_when_offered(au):
    await au.ltm.save(agent_id="a1", category="fact", key="k",
                      record_type="fact", grounding="confirmed",
                      content="deploy canary weighting gradually then promote",
                      importance=7)
    ok = await au.request_surface("deploy canary weighting gradually then promote", "a1")
    assert ok is True


@pytest.mark.asyncio
async def test_request_surface_noop_when_disabled(mem):
    assert await mem.request_surface("anything", "a1") is False
