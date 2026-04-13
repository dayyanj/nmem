"""Tests for §2: Import lifecycle — created_at/expires_at overrides."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from nmem import MemorySystem


@pytest.mark.asyncio
async def test_journal_add_with_created_at(mem: MemorySystem) -> None:
    """Journal entry with created_at override uses the provided timestamp."""
    past = datetime(2025, 1, 15, 12, 0, 0)
    entry = await mem.journal.add(
        agent_id="agent1",
        entry_type="imported",
        title="Historical entry",
        content="Something that happened in the past",
        importance=5,
        created_at=past,
    )
    assert entry.created_at == past


@pytest.mark.asyncio
async def test_journal_add_with_created_at_computes_expiry(mem: MemorySystem) -> None:
    """When created_at is set, expires_at is computed from created_at, not NOW()."""
    # 60 days ago — should have already expired (default_expiry_days=30)
    past = datetime.utcnow() - timedelta(days=60)
    entry = await mem.journal.add(
        agent_id="agent1",
        entry_type="imported",
        title="Old entry",
        content="This should already be expired",
        importance=3,
        created_at=past,
    )
    # expires_at should be created_at + 30 days = 30 days ago
    assert entry.expires_at is not None
    assert entry.expires_at < datetime.utcnow()
    expected_expiry = past + timedelta(days=30)
    # Allow 1 second tolerance
    assert abs((entry.expires_at - expected_expiry).total_seconds()) < 1


@pytest.mark.asyncio
async def test_journal_add_with_explicit_expires_at(mem: MemorySystem) -> None:
    """Explicit expires_at overrides the created_at-based calculation."""
    past = datetime(2025, 1, 1, 0, 0, 0)
    custom_expiry = datetime(2026, 12, 31, 23, 59, 59)
    entry = await mem.journal.add(
        agent_id="agent1",
        entry_type="imported",
        title="Custom expiry entry",
        content="This has a specific expiry date",
        importance=5,
        created_at=past,
        expires_at=custom_expiry,
    )
    assert entry.expires_at == custom_expiry


@pytest.mark.asyncio
async def test_journal_add_without_overrides_uses_now(mem: MemorySystem) -> None:
    """Without created_at/expires_at, behavior is unchanged (uses NOW())."""
    before = datetime.utcnow()
    entry = await mem.journal.add(
        agent_id="agent1",
        entry_type="note",
        title="Normal entry",
        content="Created normally",
        importance=5,
    )
    after = datetime.utcnow()
    assert entry.created_at is not None
    assert before <= entry.created_at <= after
    assert entry.expires_at is not None
    assert entry.expires_at > datetime.utcnow()


@pytest.mark.asyncio
async def test_ltm_save_with_created_at(mem: MemorySystem) -> None:
    """LTM entry with created_at override uses the provided timestamp."""
    past = datetime(2025, 6, 1, 10, 0, 0)
    entry = await mem.ltm.save(
        agent_id="agent1",
        category="fact",
        key="historical_fact",
        content="Something from the past",
        importance=5,
        created_at=past,
    )
    assert entry.created_at == past


@pytest.mark.asyncio
async def test_ltm_save_batch_with_created_at(mem: MemorySystem) -> None:
    """Batch save with created_at overrides."""
    past1 = datetime(2025, 1, 1, 0, 0, 0)
    past2 = datetime(2025, 6, 1, 0, 0, 0)
    entries = await mem.ltm.save_batch([
        {
            "agent_id": "agent1",
            "category": "fact",
            "key": "batch_fact_1",
            "content": "First historical fact",
            "importance": 5,
            "created_at": past1,
        },
        {
            "agent_id": "agent1",
            "category": "fact",
            "key": "batch_fact_2",
            "content": "Second historical fact",
            "importance": 6,
            "created_at": past2,
        },
    ])
    assert len(entries) == 2
    assert entries[0].created_at == past1
    assert entries[1].created_at == past2
