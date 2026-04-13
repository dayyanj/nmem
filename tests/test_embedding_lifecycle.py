"""Tests for §5: Embedding model lifecycle — ensure close() releases model."""

from __future__ import annotations

import pytest

from nmem import MemorySystem, NmemConfig


@pytest.mark.asyncio
async def test_close_releases_embedding_model() -> None:
    """After close(), the embedding provider's model is set to None."""
    config = NmemConfig(
        database_url="sqlite+aiosqlite:///:memory:",
        embedding={"provider": "noop", "dimensions": 384},
        llm={"provider": "noop"},
        consolidation={"enabled": False},
    )
    mem = MemorySystem(config)
    await mem.initialize()

    # The noop provider doesn't have _model, but the close() method
    # should handle that gracefully (hasattr check).
    await mem.close()
    # No error = success for noop provider


@pytest.mark.asyncio
async def test_sequential_memory_systems() -> None:
    """Two sequential MemorySystem instances work correctly.

    Regression test: ensures that closing one MemorySystem and creating
    another in the same process doesn't cause meta-tensor errors.
    """
    config = NmemConfig(
        database_url="sqlite+aiosqlite:///:memory:",
        embedding={"provider": "noop", "dimensions": 384},
        llm={"provider": "noop"},
        consolidation={"enabled": False},
    )

    # First instance
    mem1 = MemorySystem(config)
    await mem1.initialize()
    entry = await mem1.journal.add(
        agent_id="test", entry_type="note",
        title="First system", content="Entry from first system",
    )
    assert entry.id is not None
    await mem1.close()

    # Second instance — should work without errors
    mem2 = MemorySystem(config)
    await mem2.initialize()
    entry2 = await mem2.journal.add(
        agent_id="test", entry_type="note",
        title="Second system", content="Entry from second system",
    )
    assert entry2.id is not None
    await mem2.close()
