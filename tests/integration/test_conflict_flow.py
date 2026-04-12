"""Integration test: end-to-end conflict detection and surfacing.

Requires PostgreSQL (port 5433) for real conflict scanning. The conflict
scanner compares embedding vectors — noop embeddings (all-zeros) won't
trigger real conflict detection. This test seeds conflict rows directly
and validates the MCP surfacing path.
"""

import pytest
import pytest_asyncio

try:
    import mcp
    HAS_MCP = True
except ImportError:
    HAS_MCP = False

from unittest.mock import MagicMock

from nmem import MemorySystem, NmemConfig

pytestmark = [
    pytest.mark.skipif(not HAS_MCP, reason="mcp package not installed"),
]


@pytest_asyncio.fixture
async def mem():
    """In-memory SQLite MemorySystem for flow tests."""
    config = NmemConfig(
        database_url="sqlite+aiosqlite:///:memory:",
        embedding={"provider": "noop", "dimensions": 384},
        llm={"provider": "noop"},
    )
    system = MemorySystem(config)
    await system.initialize()
    yield system
    await system.close()


@pytest.fixture
def mcp_ctx(mem):
    ctx = MagicMock()
    ctx.request_context.lifespan_context = {"mem": mem}
    return ctx


async def test_conflict_surfacing_flow(mcp_ctx, mem):
    """Full flow: seed LTM entries -> seed conflict row -> surface via MCP."""
    from nmem.mcp.server import memory_save_ltm, memory_check_conflicts

    # Step 1: Agent A writes an LTM entry
    r1 = await memory_save_ltm(
        mcp_ctx, key="sku_format",
        content="SKU codes are digits-only after normalize",
        agent_id="agent_a", category="convention",
    )
    assert "sku_format" in r1

    # Step 2: Agent B writes a contradicting LTM entry
    r2 = await memory_save_ltm(
        mcp_ctx, key="sku_format_alt",
        content="SKU codes include ARK- prefix in storage",
        agent_id="agent_b", category="convention",
    )
    assert "sku_format_alt" in r2

    # Step 3: Seed a conflict row (scanner won't fire with noop embeddings,
    # so we insert directly to test the surfacing path)
    from sqlalchemy import text
    async with mem._db.session() as session:
        await session.execute(text("""
            INSERT INTO nmem_memory_conflicts
                (record_a_table, record_a_id, record_b_table, record_b_id,
                 agent_a, agent_b, similarity_score, description, status)
            VALUES
                ('nmem_long_term_memory', 1, 'nmem_long_term_memory', 2,
                 'agent_a', 'agent_b', 0.91,
                 'Contradicting rules about SKU format: digits-only vs ARK- prefix',
                 'open')
        """))

    # Step 4: Surface via MCP
    result = await memory_check_conflicts(mcp_ctx)
    assert "1 conflict" in result
    assert "OPEN" in result
    assert "agent_a" in result
    assert "agent_b" in result
    assert "SKU" in result

    # Step 5: Agent A re-writes with clarification (upsert)
    r3 = await memory_save_ltm(
        mcp_ctx, key="sku_format",
        content="SKU codes are digits-only, confirmed by RULES.md:ARK-C-03",
        agent_id="agent_a", category="convention",
    )
    assert "sku_format" in r3
    assert "v2" in r3

    # Step 6: The conflict row status is still 'open' (resolution happens at
    # consolidation time, not at write time). Assert current behavior.
    result2 = await memory_check_conflicts(mcp_ctx)
    assert "1 conflict" in result2


async def test_conflict_status_filter_flow(mcp_ctx, mem):
    """Test filtering by different conflict statuses."""
    from nmem.mcp.server import memory_check_conflicts
    from sqlalchemy import text

    # Seed conflicts with different statuses
    async with mem._db.session() as session:
        await session.execute(text("""
            INSERT INTO nmem_memory_conflicts
                (record_a_table, record_a_id, record_b_table, record_b_id,
                 agent_a, agent_b, similarity_score, description, status)
            VALUES
                ('nmem_long_term_memory', 10, 'nmem_long_term_memory', 20,
                 'a1', 'a2', 0.88, 'Open conflict', 'open'),
                ('nmem_long_term_memory', 30, 'nmem_long_term_memory', 40,
                 'a1', 'a2', 0.75, 'Resolved conflict', 'auto_resolved')
        """))

    # Only open
    result = await memory_check_conflicts(mcp_ctx, status="open")
    assert "1 conflict" in result
    assert "Open conflict" in result

    # Only auto_resolved
    result = await memory_check_conflicts(mcp_ctx, status="auto_resolved")
    assert "1 conflict" in result
    assert "Resolved conflict" in result

    # Both
    result = await memory_check_conflicts(mcp_ctx, status="open,auto_resolved")
    assert "2 conflict" in result
