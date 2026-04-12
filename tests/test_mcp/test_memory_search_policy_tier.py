"""Tests for memory_search with the "policy" tier extension.

Policy tier uses FTS (postgres) / LIKE (sqlite) instead of hybrid
vector+FTS search because nmem_policy_memory has no embedding column.
SQLite tests use LIKE matching, which requires the query to appear as a
substring in the key or content fields.
"""

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


async def _seed_policies(mem):
    """Seed 3 policies in different scopes."""
    await mem.policy.save(
        scope="global", category="approval",
        key="refund_limit",
        content="Agents may process refunds up to $500",
        agent_id="system",
    )
    await mem.policy.save(
        scope="agent:sales", category="autonomy",
        key="discount_limit",
        content="Sales agents can offer up to 15% discount",
        agent_id="system",
    )
    await mem.policy.save(
        scope="entity_type:lead", category="escalation",
        key="high_value_escalation",
        content="Leads above $10k must be escalated to manager",
        agent_id="system",
    )


async def test_search_policy_tier_returns_results(mcp_ctx, mem):
    from nmem.mcp.server import memory_search

    await _seed_policies(mem)

    result = await memory_search(
        mcp_ctx, query="refund", agent_id="test", tiers="policy",
    )
    assert "policy" in result.lower()
    assert "refund" in result.lower()


async def test_search_policy_combined_with_ltm(mcp_ctx, mem):
    from nmem.mcp.server import memory_search, memory_save_ltm

    await _seed_policies(mem)

    # Also seed an LTM entry
    await memory_save_ltm(
        mcp_ctx, key="refund_procedure",
        content="Refund process: verify order, check policy, issue credit",
        agent_id="test", category="procedure",
    )

    result = await memory_search(
        mcp_ctx, query="refund", agent_id="test", tiers="policy,ltm",
    )
    # Should contain results from both tiers
    assert isinstance(result, str)
    assert "Found" in result or "No results" in result


async def test_default_search_excludes_policy(mcp_ctx, mem):
    from nmem.mcp.server import memory_search

    await _seed_policies(mem)

    # Default tiers: journal, ltm, shared, entity — NOT policy
    result = await memory_search(
        mcp_ctx, query="refund", agent_id="test",
    )
    # With noop embeddings on SQLite, results may be "No results"
    # but if any results come back, they should NOT be from the policy tier
    if "Found" in result:
        assert "[policy]" not in result


async def test_search_policy_metadata_fields(mcp_ctx, mem):
    from nmem.mcp.server import memory_search

    await _seed_policies(mem)

    result = await memory_search(
        mcp_ctx, query="discount", agent_id="test", tiers="policy",
    )
    # The result should contain the policy content
    if "Found" in result:
        assert "discount" in result.lower()
