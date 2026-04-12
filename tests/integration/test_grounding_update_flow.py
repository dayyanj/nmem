"""Integration test: entity grounding lifecycle.

Tests the full flow of writing an entity record, transitioning its
grounding through inferred -> confirmed -> disputed, and verifying
the audit trail and search visibility at each step.
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


async def test_grounding_lifecycle_flow(mcp_ctx, mem):
    """Full lifecycle: write inferred -> confirm -> dispute."""
    from nmem.mcp.server import memory_write_entity, memory_mark_grounding

    # Step 1: Write entity with grounding="inferred"
    result = await memory_write_entity(
        mcp_ctx,
        entity_type="person",
        entity_id="user:bob",
        entity_name="Bob",
        content="Bob is the security lead",
        agent_id="test",
        grounding="inferred",
        confidence=0.6,
    )
    assert "Saved entity record" in result
    assert "grounding=inferred" in result

    # Extract record ID from the result string
    record_id_str = result.split("#")[1].split(":")[0]
    record_id = int(record_id_str)

    # Step 2: Confirm the grounding
    result = await memory_mark_grounding(
        mcp_ctx,
        entity_record_id=record_id,
        grounding="confirmed",
        evidence_ref="doc://org-chart-2026.pdf",
        agent_id="test",
    )
    assert f"#{record_id}" in result
    assert "confirmed" in result

    # Verify DB state
    records = await mem.entity.get("person", "user:bob")
    record = next(r for r in records if r.id == record_id)
    assert record.grounding == "confirmed"
    assert record.evidence_refs is not None
    assert len(record.evidence_refs) == 1
    assert record.evidence_refs[0]["type"] == "grounding_transition"
    assert record.evidence_refs[0]["from"] == "inferred"
    assert record.evidence_refs[0]["to"] == "confirmed"
    assert record.evidence_refs[0]["ref"] == "doc://org-chart-2026.pdf"

    # Step 3: Dispute the grounding
    result = await memory_mark_grounding(
        mcp_ctx,
        entity_record_id=record_id,
        grounding="disputed",
        evidence_ref="email://ops-2026-04-12",
        agent_id="test",
    )
    assert "disputed" in result

    # Step 4: Verify evidence_refs has 2 audit entries in chronological order
    records = await mem.entity.get("person", "user:bob")
    record = next(r for r in records if r.id == record_id)
    assert record.grounding == "disputed"
    assert len(record.evidence_refs) == 2
    assert record.evidence_refs[0]["to"] == "confirmed"
    assert record.evidence_refs[1]["to"] == "disputed"
    assert record.evidence_refs[1]["ref"] == "email://ops-2026-04-12"


async def test_disputed_entity_still_searchable(mcp_ctx, mem):
    """Disputed entities should remain visible in search (not deleted)."""
    from nmem.mcp.server import memory_write_entity, memory_mark_grounding

    result = await memory_write_entity(
        mcp_ctx,
        entity_type="fact",
        entity_id="FACT-1",
        entity_name="SKU rule",
        content="SKU codes are always uppercase",
        agent_id="test",
        grounding="inferred",
    )
    record_id = int(result.split("#")[1].split(":")[0])

    # Mark as disputed
    await memory_mark_grounding(
        mcp_ctx,
        entity_record_id=record_id,
        grounding="disputed",
        evidence_ref="contradicted by ARK-C-03",
        agent_id="test",
    )

    # Verify it's still retrievable
    records = await mem.entity.get("fact", "FACT-1")
    assert len(records) >= 1
    assert any(r.id == record_id and r.grounding == "disputed" for r in records)


async def test_grounding_update_with_no_prior_evidence_refs(mcp_ctx, mem):
    """Entity with no initial evidence_refs should still get an audit entry."""
    from nmem.mcp.server import memory_write_entity, memory_mark_grounding

    result = await memory_write_entity(
        mcp_ctx,
        entity_type="product",
        entity_id="PROD-1",
        entity_name="Widget",
        content="A premium widget for testing",
        agent_id="test",
    )
    record_id = int(result.split("#")[1].split(":")[0])

    await memory_mark_grounding(
        mcp_ctx,
        entity_record_id=record_id,
        grounding="source_material",
        evidence_ref="catalog://2026-spring",
        agent_id="test",
    )

    records = await mem.entity.get("product", "PROD-1")
    record = next(r for r in records if r.id == record_id)
    assert record.grounding == "source_material"
    assert record.evidence_refs is not None
    assert len(record.evidence_refs) == 1
    assert record.evidence_refs[0]["from"] == "inferred"
    assert record.evidence_refs[0]["to"] == "source_material"
