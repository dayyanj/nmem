"""Tests for memory_write_entity MCP tool."""

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


async def test_write_entity_defaults(mcp_ctx, mem):
    from nmem.mcp.server import memory_write_entity

    result = await memory_write_entity(
        mcp_ctx,
        entity_type="bug",
        entity_id="BUG-101",
        entity_name="Login crash",
        content="The login page crashes on empty password",
        agent_id="test",
    )
    assert "Saved entity record" in result
    assert "bug/Login crash" in result
    assert "type=evidence" in result
    assert "grounding=inferred" in result


async def test_write_entity_explicit_types(mcp_ctx, mem):
    from nmem.mcp.server import memory_write_entity

    result = await memory_write_entity(
        mcp_ctx,
        entity_type="person",
        entity_id="user:alice",
        entity_name="Alice",
        content="Alice is the lead developer",
        agent_id="test",
        record_type="judgment",
        grounding="confirmed",
        confidence=0.95,
    )
    assert "type=judgment" in result
    assert "grounding=confirmed" in result
    assert "confidence=0.95" in result


async def test_write_entity_invalid_record_type(mcp_ctx, mem):
    from nmem.mcp.server import memory_write_entity

    result = await memory_write_entity(
        mcp_ctx,
        entity_type="bug",
        entity_id="BUG-102",
        entity_name="Test",
        content="Test content",
        agent_id="test",
        record_type="invalid_type",
    )
    assert "Error" in result
    assert "record_type must be one of" in result


async def test_write_entity_invalid_grounding(mcp_ctx, mem):
    from nmem.mcp.server import memory_write_entity

    result = await memory_write_entity(
        mcp_ctx,
        entity_type="bug",
        entity_id="BUG-103",
        entity_name="Test",
        content="Test content",
        agent_id="test",
        grounding="made_up",
    )
    assert "Error" in result
    assert "grounding must be one of" in result


async def test_write_entity_tags_roundtrip(mcp_ctx, mem):
    from nmem.mcp.server import memory_write_entity

    result = await memory_write_entity(
        mcp_ctx,
        entity_type="product",
        entity_id="SKU-999",
        entity_name="Widget",
        content="A premium widget",
        agent_id="test",
        tags=["premium", "featured"],
    )
    assert "Saved entity record" in result

    # Verify tags persisted via search
    records = await mem.entity.get("product", "SKU-999")
    assert len(records) >= 1
    assert records[0].tags == ["premium", "featured"]


async def test_write_entity_confidence_clamped(mcp_ctx, mem):
    from nmem.mcp.server import memory_write_entity

    result = await memory_write_entity(
        mcp_ctx,
        entity_type="test",
        entity_id="T-1",
        entity_name="Clamp test",
        content="Test content",
        agent_id="test",
        confidence=5.0,
    )
    assert "confidence=1.00" in result
