"""
Test fixtures for nmem.

Requires PostgreSQL + pgvector (docker compose on port 5433). SQLite was dropped
in 0.9.2. Override the DSN with NMEM_TEST_DSN.
"""

from __future__ import annotations

import os

import pytest
import pytest_asyncio
from sqlalchemy import text

from nmem import MemorySystem, NmemConfig

_PG_URL = "postgresql+asyncpg://nmem:nmem@localhost:5433/nmem"


TEST_DB_URL = os.environ.get("NMEM_TEST_DSN", _PG_URL)

# All nmem tables, cleared between tests. Postgres is a single shared DB (unlike
# the old per-test SQLite :memory:), so every fixture that creates its own
# MemorySystem must reset_db() to stay isolated.
CLEANUP_TABLES = [
    "nmem_working_memory",
    "nmem_journal_entries",
    "nmem_long_term_memory",
    "nmem_shared_knowledge",
    "nmem_entity_memory",
    "nmem_policy_memory",
    "nmem_memory_conflicts",
    "nmem_curiosity_signals",
    "nmem_commitments",
    "nmem_delegations",
    "nmem_performance_scores",
    "nmem_scheduled_followups",
    "nmem_knowledge_links",
    "nmem_skills",
    "nmem_context_recipes",
    "nmem_recipe_tombstones",
]


async def reset_db(system: MemorySystem) -> None:
    """Truncate all nmem tables — call before yielding a fresh MemorySystem so
    tests are isolated on the shared Postgres DB."""
    async with system._db.session() as session:
        for table in CLEANUP_TABLES:
            try:
                await session.execute(text(f"DELETE FROM {table}"))
            except Exception:
                pass  # table may not exist yet


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

    await reset_db(system)   # clean BEFORE test (stale data from crashed runs)
    yield system  # type: ignore[misc]
    await reset_db(system)   # clean AFTER test too
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
