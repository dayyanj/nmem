"""
Test fixtures for nmem.

Uses PostgreSQL if available (docker compose on port 5433), falls back to SQLite.
"""

from __future__ import annotations

import os

import pytest
import pytest_asyncio
from sqlalchemy import text

from nmem import MemorySystem, NmemConfig

# Use PostgreSQL if available, otherwise SQLite
_PG_URL = "postgresql+asyncpg://nmem:nmem@localhost:5433/nmem"
_SQLITE_URL = "sqlite+aiosqlite:///:memory:"


def _detect_db_url() -> str:
    """Detect which database to use for tests."""
    # Explicit override
    env_url = os.environ.get("NMEM_TEST_DSN")
    if env_url:
        return env_url

    # Try PostgreSQL
    try:
        import asyncio
        import asyncpg

        async def _check():
            conn = await asyncpg.connect("postgresql://nmem:nmem@localhost:5433/nmem")
            await conn.close()
            return True

        if asyncio.get_event_loop().run_until_complete(_check()):
            return _PG_URL
    except Exception:
        pass

    return _SQLITE_URL


TEST_DB_URL = _detect_db_url()


@pytest_asyncio.fixture
async def mem() -> MemorySystem:
    """Create an initialized MemorySystem with noop providers."""
    config = NmemConfig(
        database_url=TEST_DB_URL,
        embedding={"provider": "noop", "dimensions": 384},
        llm={"provider": "noop"},
        consolidation={"enabled": False},
    )
    system = MemorySystem(config)
    await system.initialize()

    # Clean BEFORE test to handle stale data from crashed runs
    tables = [
        "nmem_working_memory",
        "nmem_journal_entries",
        "nmem_long_term_memory",
        "nmem_shared_knowledge",
        "nmem_entity_memory",
        "nmem_policy_memory",
        "nmem_memory_conflicts",
        "nmem_curiosity_signals",
        "nmem_delegations",
        "nmem_performance_scores",
        "nmem_scheduled_followups",
    ]
    async with system._db.session() as session:
        for table in tables:
            try:
                await session.execute(text(f"DELETE FROM {table}"))
            except Exception:
                pass  # Table may not exist in SQLite

    yield system  # type: ignore[misc]

    # Clean AFTER test too
    async with system._db.session() as session:
        for table in tables:
            try:
                await session.execute(text(f"DELETE FROM {table}"))
            except Exception:
                pass

    await system.close()


@pytest_asyncio.fixture
async def mem_with_data(mem: MemorySystem) -> MemorySystem:
    """MemorySystem pre-populated with sample data across tiers."""
    await mem.journal.add(
        agent_id="agent1",
        entry_type="session_summary",
        title="Helped customer with billing",
        content="Customer had duplicate charge. Issued refund via Stripe.",
        importance=6,
    )
    await mem.journal.add(
        agent_id="agent1",
        entry_type="lesson_learned",
        title="Always check for duplicates before refunding",
        content="Multiple refund requests can indicate fraud. Check order history first.",
        importance=8,
    )

    await mem.ltm.save(
        agent_id="agent1",
        category="procedure",
        key="refund_process",
        content="Step 1: Verify purchase. Step 2: Check for duplicates. Step 3: Issue refund.",
        importance=9,
    )

    await mem.shared.save(
        key="company_refund_policy",
        content="Refunds must be processed within 30 days of purchase.",
        category="policy",
        agent_id="system",
        importance=10,
    )

    await mem.entity.save(
        entity_type="customer",
        entity_id="cust_123",
        entity_name="Acme Corp",
        agent_id="agent1",
        content="Enterprise customer, 50+ seats, sensitive to pricing changes.",
        record_type="evidence",
        confidence=0.9,
    )

    return mem
