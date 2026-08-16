"""
Tests for MemorySystem.get_entry_tier — the journal-entry tier readback
API used by nmem-sym 0.6.0's journal-mediated hypothesis lifespan.

Four scenarios to verify:

  1. Entry sitting in journal (not yet promoted) → ('journal', entry)
  2. Entry promoted to LTM → ('ltm', entry) — pointer resolved
  3. Entry not found at all → ('archived', None) — no crash
  4. Entry exists but wrong agent_id → ('archived', None) — scoping respected

The 'shared' case requires the consolidation pipeline to promote LTM
→ shared which is a multi-step async process; verified via direct
DB update to flip the flag, which exercises the same code path.

These run against the shared Postgres test DB per conftest; the readback
is plain SQL.
"""
from __future__ import annotations

import pytest
from sqlalchemy import select, update

from nmem import MemorySystem
from nmem.db.models import JournalEntryModel, LTMModel


@pytest.mark.asyncio
async def test_journal_entry_returns_journal_tier(mem: MemorySystem):
    """A fresh journal entry that hasn't been promoted reads back as
    ('journal', entry). Default state for everything dual-write writes."""
    entry = await mem.journal.add(
        agent_id="agent1",
        entry_type="hypothesis",
        title="hypothesis stub",
        content="X may relate to Y via Z",
        importance=5,
    )
    tier, returned = await mem.get_entry_tier(
        agent_id="agent1", entry_id=entry.id,
    )
    assert tier == "journal"
    assert returned is not None
    assert returned.id == entry.id


@pytest.mark.asyncio
async def test_missing_entry_returns_archived(mem: MemorySystem):
    """An entry id that doesn't exist (or never did) returns
    ('archived', None) — never raises. This is what nmem-sym sees when
    a hypothesis's journal entry has decayed away entirely."""
    tier, returned = await mem.get_entry_tier(
        agent_id="agent1", entry_id=999_999_999,
    )
    assert tier == "archived"
    assert returned is None


@pytest.mark.asyncio
async def test_wrong_agent_id_returns_archived(mem: MemorySystem):
    """Cross-agent visibility is enforced: agent2 cannot read agent1's
    journal entry by id. Same return shape as not-found — callers
    don't get a distinct signal because there isn't one from their
    perspective."""
    entry = await mem.journal.add(
        agent_id="agent1",
        entry_type="hypothesis",
        title="agent1 hypothesis",
        content="content",
        importance=5,
    )
    tier, returned = await mem.get_entry_tier(
        agent_id="agent2", entry_id=entry.id,
    )
    assert tier == "archived"
    assert returned is None


@pytest.mark.asyncio
async def test_promoted_entry_returns_ltm_tier(mem: MemorySystem):
    """An entry that has been promoted to LTM reads back as ('ltm',
    ltm_entry). Verified by flipping promoted_to_ltm + populating
    pointers directly — exercises the same code path the consolidation
    pipeline writes."""
    j_entry = await mem.journal.add(
        agent_id="agent1",
        entry_type="hypothesis",
        title="promoted hypothesis",
        content="X causes Y because of Z",
        importance=9,
    )

    # Mint an LTM row that the journal entry will point at.
    await mem.ltm.save(
        agent_id="agent1",
        category="hypothesis",
        key=f"hyp_{j_entry.id}",
        content="X causes Y because of Z",
        importance=9,
    )

    async with mem._db.session() as session:
        ltm_id = (await session.execute(
            select(LTMModel.id).where(
                LTMModel.agent_id == "agent1",
                LTMModel.key == f"hyp_{j_entry.id}",
            )
        )).scalar_one()
        # Flip the journal row to promoted with a pointer to the LTM id
        await session.execute(
            update(JournalEntryModel)
            .where(JournalEntryModel.id == j_entry.id)
            .values(
                promoted_to_ltm=True,
                pointers=[{"type": "ltm", "id": ltm_id, "key": f"hyp_{j_entry.id}"}],
            )
        )
        await session.commit()

    tier, returned = await mem.get_entry_tier(
        agent_id="agent1", entry_id=j_entry.id,
    )
    assert tier == "ltm"
    assert returned is not None
    assert returned.id == ltm_id


@pytest.mark.asyncio
async def test_promoted_to_shared_returns_shared_tier(mem: MemorySystem):
    """The 'shared' return path — same as LTM resolution, but the LTM
    row has promoted_to_shared=True."""
    j_entry = await mem.journal.add(
        agent_id="agent1",
        entry_type="hypothesis",
        title="shared hypothesis",
        content="cross-agent insight worth sharing",
        importance=10,
    )

    await mem.ltm.save(
        agent_id="agent1",
        category="hypothesis",
        key=f"shared_hyp_{j_entry.id}",
        content="cross-agent insight worth sharing",
        importance=10,
    )

    async with mem._db.session() as session:
        ltm_id = (await session.execute(
            select(LTMModel.id).where(
                LTMModel.agent_id == "agent1",
                LTMModel.key == f"shared_hyp_{j_entry.id}",
            )
        )).scalar_one()
        # Promote to LTM + further to shared (consolidator's two-step
        # path, compressed for the test).
        await session.execute(
            update(JournalEntryModel)
            .where(JournalEntryModel.id == j_entry.id)
            .values(
                promoted_to_ltm=True,
                pointers=[{"type": "ltm", "id": ltm_id,
                           "key": f"shared_hyp_{j_entry.id}"}],
            )
        )
        await session.execute(
            update(LTMModel)
            .where(LTMModel.id == ltm_id)
            .values(promoted_to_shared=True)
        )
        await session.commit()

    tier, returned = await mem.get_entry_tier(
        agent_id="agent1", entry_id=j_entry.id,
    )
    assert tier == "shared"
    assert returned is not None
    assert returned.id == ltm_id


@pytest.mark.asyncio
async def test_dangling_ltm_pointer_returns_archived(mem: MemorySystem):
    """Defensive case — a journal entry marked promoted_to_ltm=True but
    whose pointer target has been deleted. Treat as archived rather than
    surfacing the stub as 'journal' (which would be misleading)."""
    j_entry = await mem.journal.add(
        agent_id="agent1",
        entry_type="hypothesis",
        title="dangling",
        content="...",
        importance=5,
    )

    async with mem._db.session() as session:
        await session.execute(
            update(JournalEntryModel)
            .where(JournalEntryModel.id == j_entry.id)
            .values(
                promoted_to_ltm=True,
                # Pointer to a non-existent LTM id
                pointers=[{"type": "ltm", "id": 99_999_999, "key": "missing"}],
            )
        )
        await session.commit()

    tier, returned = await mem.get_entry_tier(
        agent_id="agent1", entry_id=j_entry.id,
    )
    assert tier == "archived"
    assert returned is None


@pytest.mark.asyncio
async def test_promoted_without_pointer_returns_archived(mem: MemorySystem):
    """Another defensive case — promoted_to_ltm=True but pointers is
    NULL/empty. Shouldn't happen in practice but if it does, archived
    is the safer response than crashing or pretending it's still in
    journal."""
    j_entry = await mem.journal.add(
        agent_id="agent1",
        entry_type="hypothesis",
        title="no pointer",
        content="...",
        importance=5,
    )

    async with mem._db.session() as session:
        await session.execute(
            update(JournalEntryModel)
            .where(JournalEntryModel.id == j_entry.id)
            .values(promoted_to_ltm=True, pointers=None)
        )
        await session.commit()

    tier, returned = await mem.get_entry_tier(
        agent_id="agent1", entry_id=j_entry.id,
    )
    assert tier == "archived"
    assert returned is None
