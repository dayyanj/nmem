"""Tests for memory_mark_grounding MCP tool."""

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


async def _create_entity(mem, grounding="inferred"):
    """Helper: create an entity record and return its ID."""
    record = await mem.entity.save(
        entity_type="bug",
        entity_id="BUG-42",
        entity_name="Login crash",
        agent_id="test",
        content="The login page crashes on empty password",
        grounding=grounding,
    )
    return record.id


async def test_mark_grounding_inferred_to_confirmed(mcp_ctx, mem):
    from nmem.mcp.server import memory_mark_grounding

    record_id = await _create_entity(mem)

    result = await memory_mark_grounding(
        mcp_ctx,
        entity_record_id=record_id,
        grounding="confirmed",
        evidence_ref="doc://truth.md",
        agent_id="test",
    )
    assert f"#{record_id}" in result
    assert "confirmed" in result


async def test_mark_grounding_audit_trail(mcp_ctx, mem):
    from nmem.mcp.server import memory_mark_grounding

    record_id = await _create_entity(mem)

    await memory_mark_grounding(
        mcp_ctx,
        entity_record_id=record_id,
        grounding="confirmed",
        evidence_ref="https://example.com/proof",
        agent_id="test",
    )

    # Verify the evidence_refs has the audit entry
    records = await mem.entity.get("bug", "BUG-42")
    record = next(r for r in records if r.id == record_id)
    assert record.evidence_refs is not None
    assert len(record.evidence_refs) >= 1
    audit = record.evidence_refs[-1]
    assert audit["type"] == "grounding_transition"
    assert audit["from"] == "inferred"
    assert audit["to"] == "confirmed"
    assert audit["ref"] == "https://example.com/proof"


async def test_mark_grounding_invalid_value(mcp_ctx, mem):
    from nmem.mcp.server import memory_mark_grounding

    record_id = await _create_entity(mem)

    result = await memory_mark_grounding(
        mcp_ctx,
        entity_record_id=record_id,
        grounding="bogus",
        agent_id="test",
    )
    assert "Error" in result
    assert "grounding must be one of" in result


async def test_mark_grounding_not_found(mcp_ctx, mem):
    from nmem.mcp.server import memory_mark_grounding

    result = await memory_mark_grounding(
        mcp_ctx,
        entity_record_id=99999,
        grounding="confirmed",
        agent_id="test",
    )
    assert "Error" in result
    assert "not found" in result


async def test_mark_grounding_idempotent(mcp_ctx, mem):
    from nmem.mcp.server import memory_mark_grounding

    record_id = await _create_entity(mem, grounding="confirmed")

    result = await memory_mark_grounding(
        mcp_ctx,
        entity_record_id=record_id,
        grounding="confirmed",
        agent_id="test",
    )
    assert "no change" in result
