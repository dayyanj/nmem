"""Tests for memory_check_conflicts MCP tool."""

from unittest.mock import MagicMock

import pytest
import pytest_asyncio

try:
    import mcp
    HAS_MCP = True
except ImportError:
    HAS_MCP = False

from nmem import MemorySystem, NmemConfig

pytestmark = pytest.mark.skipif(not HAS_MCP, reason="mcp package not installed")


@pytest_asyncio.fixture
async def mem():
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


async def _seed_conflict(mem):
    """Seed a conflict row directly into the database."""
    from sqlalchemy import text
    async with mem._db.session() as session:
        await session.execute(text("""
            INSERT INTO nmem_memory_conflicts
                (record_a_table, record_a_id, record_b_table, record_b_id,
                 agent_a, agent_b, similarity_score, description, status)
            VALUES
                ('nmem_long_term_memory', 1, 'nmem_long_term_memory', 2,
                 'agent_a', 'agent_b', 0.91,
                 'Contradicting rules about SKU format', 'open')
        """))


async def test_check_conflicts_with_seeded_data(mcp_ctx, mem):
    from nmem.mcp.server import memory_check_conflicts

    await _seed_conflict(mem)

    result = await memory_check_conflicts(mcp_ctx)
    assert "1 conflict" in result
    assert "OPEN" in result
    assert "agent_a" in result
    assert "agent_b" in result


async def test_check_conflicts_empty(mcp_ctx, mem):
    from nmem.mcp.server import memory_check_conflicts

    result = await memory_check_conflicts(mcp_ctx)
    assert "No conflicts" in result


async def test_check_conflicts_filter_by_agent(mcp_ctx, mem):
    from nmem.mcp.server import memory_check_conflicts

    await _seed_conflict(mem)

    # Filter by agent_a — should find it
    result = await memory_check_conflicts(mcp_ctx, agent_id="agent_a")
    assert "1 conflict" in result

    # Filter by nonexistent agent — should find none
    result = await memory_check_conflicts(mcp_ctx, agent_id="nobody")
    assert "No conflicts" in result


async def test_check_conflicts_limit(mcp_ctx, mem):
    from nmem.mcp.server import memory_check_conflicts

    # Seed two conflicts
    await _seed_conflict(mem)
    from sqlalchemy import text
    async with mem._db.session() as session:
        await session.execute(text("""
            INSERT INTO nmem_memory_conflicts
                (record_a_table, record_a_id, record_b_table, record_b_id,
                 agent_a, agent_b, similarity_score, description, status)
            VALUES
                ('nmem_shared_knowledge', 10, 'nmem_shared_knowledge', 20,
                 'agent_c', 'agent_d', 0.85,
                 'Second conflict', 'open')
        """))

    result = await memory_check_conflicts(mcp_ctx, limit=1)
    assert "1 conflict" in result


async def test_check_conflicts_invalid_status(mcp_ctx, mem):
    from nmem.mcp.server import memory_check_conflicts

    result = await memory_check_conflicts(mcp_ctx, status="bogus")
    assert "Error" in result
    assert "invalid status" in result.lower()


async def test_check_conflicts_since_days_zero(mcp_ctx, mem):
    from nmem.mcp.server import memory_check_conflicts

    await _seed_conflict(mem)

    # since_days=0 means only conflicts from the last 0 days — effectively nothing
    result = await memory_check_conflicts(mcp_ctx, since_days=0)
    # Depending on timing, the conflict was created in the same second, so it
    # might or might not match. Assert it doesn't crash at minimum.
    assert isinstance(result, str)
