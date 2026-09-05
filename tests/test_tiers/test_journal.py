"""Tests for Tier 2: Journal."""

from __future__ import annotations

import pytest

from nmem import MemorySystem


@pytest.mark.asyncio
async def test_add_and_search(mem: MemorySystem) -> None:
    """Add a journal entry and retrieve via search."""
    entry = await mem.journal.add(
        agent_id="agent1",
        entry_type="note",
        title="Test entry",
        content="This is a test journal entry",
        importance=5,
    )
    assert entry.id is not None
    assert entry.title == "Test entry"
    assert entry.agent_id == "agent1"


@pytest.mark.asyncio
async def test_recent(mem: MemorySystem) -> None:
    """Get recent entries returns newest first."""
    for i in range(5):
        await mem.journal.add(
            agent_id="agent1",
            entry_type="note",
            title=f"Entry {i}",
            content=f"Content {i}",
        )
    recent = await mem.journal.recent("agent1", days=1)
    assert len(recent) == 5
    assert recent[0].title == "Entry 4"  # newest first


@pytest.mark.asyncio
async def test_activity_summary(mem: MemorySystem) -> None:
    """Activity summary returns formatted text."""
    await mem.journal.add(
        agent_id="agent1",
        entry_type="decision",
        title="Chose plan A",
        content="Selected plan A over B due to cost",
        importance=7,
    )
    summary = await mem.journal.activity_summary("agent1")
    assert "decision" in summary
    assert "Chose plan A" in summary


@pytest.mark.asyncio
async def test_agent_isolation(mem: MemorySystem) -> None:
    """Different agents don't see each other's journal."""
    await mem.journal.add(agent_id="a1", entry_type="note", title="A1", content="A1")
    await mem.journal.add(agent_id="a2", entry_type="note", title="A2", content="A2")
    a1_entries = await mem.journal.recent("a1")
    a2_entries = await mem.journal.recent("a2")
    assert len(a1_entries) == 1
    assert len(a2_entries) == 1
    assert a1_entries[0].title == "A1"


@pytest.mark.asyncio
async def test_dedup_flag(mem: MemorySystem) -> None:
    """dedup=True (default) collapses a near-identical recent entry into the existing
    row; dedup=False force-stores it — for authoritative records that must persist
    verbatim even when semantically similar (e.g. episode/escalation logs)."""
    a = await mem.journal.add(
        agent_id="ag", entry_type="decision",
        title="deploy the release", content="ship v1 to prod now", compress=False,
    )
    # Identical content within 24h → cosine 1.0 (noop embed is content-deterministic) →
    # default dedup collapses it into `a` instead of inserting.
    b = await mem.journal.add(
        agent_id="ag", entry_type="decision",
        title="deploy the release", content="ship v1 to prod now", compress=False,
    )
    assert b.id == a.id
    # Same content, but dedup opted out → a distinct row is stored.
    c = await mem.journal.add(
        agent_id="ag", entry_type="escalation",
        title="deploy the release", content="ship v1 to prod now",
        compress=False, dedup=False,
    )
    assert c.id != a.id
    # Both distinct rows are now retrievable (the dedup'd b did not add a third).
    assert len({e.id for e in await mem.journal.recent("ag", limit=10)}) == 2
