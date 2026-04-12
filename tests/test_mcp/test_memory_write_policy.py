"""Tests for memory_write_policy MCP tool."""

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


async def test_write_policy_happy_path(mcp_ctx, mem):
    from nmem.mcp.server import memory_write_policy

    result = await memory_write_policy(
        mcp_ctx,
        scope="global",
        category="approval",
        key="refund_limit",
        content="Agents may process refunds up to $500 without manager approval",
        agent_id="default",
    )
    assert "Saved policy" in result
    assert "global/approval" in result
    assert "refund_limit" in result
    assert "v1" in result


async def test_write_policy_upsert_increments_version(mcp_ctx, mem):
    from nmem.mcp.server import memory_write_policy

    await memory_write_policy(
        mcp_ctx,
        scope="global",
        category="approval",
        key="refund_limit",
        content="Original policy text",
        agent_id="default",
    )

    result = await memory_write_policy(
        mcp_ctx,
        scope="global",
        category="approval",
        key="refund_limit",
        content="Updated policy text",
        agent_id="default",
    )
    assert "v2" in result


async def test_write_policy_different_scopes_distinct(mcp_ctx, mem):
    from nmem.mcp.server import memory_write_policy

    r1 = await memory_write_policy(
        mcp_ctx,
        scope="global",
        category="autonomy",
        key="max_spend",
        content="Global spend limit $1000",
        agent_id="default",
    )
    r2 = await memory_write_policy(
        mcp_ctx,
        scope="agent:sales",
        category="autonomy",
        key="max_spend",
        content="Sales agent spend limit $200",
        agent_id="default",
    )
    # Both should succeed as v1 — different scopes
    assert "v1" in r1
    assert "v1" in r2
    assert "global" in r1
    assert "agent:sales" in r2


async def test_write_policy_empty_scope_error(mcp_ctx, mem):
    from nmem.mcp.server import memory_write_policy

    result = await memory_write_policy(
        mcp_ctx,
        scope="",
        category="test",
        key="test_key",
        content="Test content",
        agent_id="default",
    )
    assert "Error" in result
    assert "scope and key are required" in result


async def test_write_policy_empty_key_error(mcp_ctx, mem):
    from nmem.mcp.server import memory_write_policy

    result = await memory_write_policy(
        mcp_ctx,
        scope="global",
        category="test",
        key="",
        content="Test content",
        agent_id="default",
    )
    assert "Error" in result


async def test_write_policy_permission_denied(mcp_ctx, mem):
    """Agent not in writers or proposers should get a permission error."""
    from nmem.mcp.server import memory_write_policy

    result = await memory_write_policy(
        mcp_ctx,
        scope="global",
        category="test",
        key="test_key",
        content="Test",
        agent_id="unauthorized_agent_xyz",
    )
    assert "Error" in result
    assert "cannot write or propose" in result.lower() or "permission" in result.lower()
