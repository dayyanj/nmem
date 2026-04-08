"""Tests for Tier 1: Working Memory."""

from __future__ import annotations

import pytest

from nmem import MemorySystem


@pytest.mark.asyncio
async def test_set_and_get(mem: MemorySystem) -> None:
    """Set a slot and retrieve it."""
    await mem.working.set("session1", "agent1", "current_task", "Process refund")
    slots = await mem.working.get("session1", "agent1")
    assert len(slots) == 1
    assert slots[0].slot == "current_task"
    assert slots[0].content == "Process refund"


@pytest.mark.asyncio
async def test_upsert(mem: MemorySystem) -> None:
    """Setting the same slot updates it."""
    await mem.working.set("s1", "a1", "task", "v1")
    await mem.working.set("s1", "a1", "task", "v2")
    slots = await mem.working.get("s1", "a1")
    assert len(slots) == 1
    assert slots[0].content == "v2"


@pytest.mark.asyncio
async def test_clear_specific_slot(mem: MemorySystem) -> None:
    """Clear a specific slot."""
    await mem.working.set("s1", "a1", "slot_a", "val_a")
    await mem.working.set("s1", "a1", "slot_b", "val_b")
    cleared = await mem.working.clear("s1", "a1", slot="slot_a")
    assert cleared == 1
    remaining = await mem.working.get("s1", "a1")
    assert len(remaining) == 1
    assert remaining[0].slot == "slot_b"


@pytest.mark.asyncio
async def test_clear_all(mem: MemorySystem) -> None:
    """Clear all slots for a session/agent."""
    await mem.working.set("s1", "a1", "slot_a", "val_a")
    await mem.working.set("s1", "a1", "slot_b", "val_b")
    cleared = await mem.working.clear("s1", "a1")
    assert cleared == 2
    remaining = await mem.working.get("s1", "a1")
    assert len(remaining) == 0


@pytest.mark.asyncio
async def test_priority_ordering(mem: MemorySystem) -> None:
    """Slots are returned ordered by priority (1=highest first)."""
    await mem.working.set("s1", "a1", "low", "low priority", priority=9)
    await mem.working.set("s1", "a1", "high", "high priority", priority=1)
    await mem.working.set("s1", "a1", "mid", "mid priority", priority=5)
    slots = await mem.working.get("s1", "a1")
    assert [s.slot for s in slots] == ["high", "mid", "low"]


@pytest.mark.asyncio
async def test_build_prompt(mem: MemorySystem) -> None:
    """Build prompt returns formatted slots."""
    await mem.working.set("s1", "a1", "task", "Process refund #123")
    prompt = await mem.working.build_prompt("s1", "a1")
    assert "task" in prompt
    assert "Process refund #123" in prompt


@pytest.mark.asyncio
async def test_session_isolation(mem: MemorySystem) -> None:
    """Different sessions don't see each other's slots."""
    await mem.working.set("s1", "a1", "task", "Session 1 task")
    await mem.working.set("s2", "a1", "task", "Session 2 task")
    s1 = await mem.working.get("s1", "a1")
    s2 = await mem.working.get("s2", "a1")
    assert s1[0].content == "Session 1 task"
    assert s2[0].content == "Session 2 task"
