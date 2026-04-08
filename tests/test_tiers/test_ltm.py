"""Tests for Tier 3: Long-Term Memory."""

from __future__ import annotations

import pytest

from nmem import MemorySystem


@pytest.mark.asyncio
async def test_save_and_get(mem: MemorySystem) -> None:
    """Save an LTM entry and retrieve by key."""
    entry = await mem.ltm.save(
        agent_id="agent1",
        category="procedure",
        key="login_steps",
        content="Step 1: Open browser. Step 2: Navigate to site.",
        importance=8,
    )
    assert entry.key == "login_steps"
    assert entry.version == 1

    retrieved = await mem.ltm.get("agent1", "login_steps")
    assert retrieved is not None
    assert retrieved.content == entry.content


@pytest.mark.asyncio
async def test_upsert_increments_version(mem: MemorySystem) -> None:
    """Saving to same key increments version."""
    await mem.ltm.save("agent1", "fact", "pricing", "Plan A: $10/mo", importance=5)
    updated = await mem.ltm.save("agent1", "fact", "pricing", "Plan A: $12/mo", importance=6)
    assert updated.version == 2
    assert updated.content == "Plan A: $12/mo"
    assert updated.importance == 6  # max(5, 6)


@pytest.mark.asyncio
async def test_list_keys(mem: MemorySystem) -> None:
    """List all keys for an agent."""
    await mem.ltm.save("agent1", "fact", "key_a", "Content A")
    await mem.ltm.save("agent1", "procedure", "key_b", "Content B")
    keys = await mem.ltm.list_keys("agent1")
    assert set(keys) == {"key_a", "key_b"}


@pytest.mark.asyncio
async def test_list_keys_by_category(mem: MemorySystem) -> None:
    """List keys filtered by category."""
    await mem.ltm.save("agent1", "fact", "fact_1", "F1")
    await mem.ltm.save("agent1", "procedure", "proc_1", "P1")
    fact_keys = await mem.ltm.list_keys("agent1", category="fact")
    assert fact_keys == ["fact_1"]


@pytest.mark.asyncio
async def test_delete(mem: MemorySystem) -> None:
    """Delete an LTM entry."""
    await mem.ltm.save("agent1", "fact", "temp", "Temporary")
    deleted = await mem.ltm.delete("agent1", "temp")
    assert deleted is True
    assert await mem.ltm.get("agent1", "temp") is None


@pytest.mark.asyncio
async def test_delete_nonexistent(mem: MemorySystem) -> None:
    """Deleting nonexistent key returns False."""
    deleted = await mem.ltm.delete("agent1", "does_not_exist")
    assert deleted is False
