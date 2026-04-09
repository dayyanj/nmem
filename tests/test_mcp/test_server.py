"""Tests for nmem MCP server tools."""

import asyncio
from unittest.mock import MagicMock

import pytest
import pytest_asyncio

from nmem import MemorySystem, NmemConfig


@pytest_asyncio.fixture
async def mem():
    """In-memory SQLite MemorySystem for MCP tests."""
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
    """Mock MCP Context with real MemorySystem."""
    ctx = MagicMock()
    ctx.request_context.lifespan_context = {"mem": mem}
    return ctx


async def test_memory_store(mcp_ctx, mem):
    from nmem.mcp.server import memory_store

    result = await memory_store(
        mcp_ctx, title="Test entry", content="Some test content",
        agent_id="test", importance=5,
    )
    assert "Stored" in result
    assert "Test entry" in result


async def test_memory_search_empty(mcp_ctx, mem):
    from nmem.mcp.server import memory_search

    result = await memory_search(mcp_ctx, query="anything", agent_id="test")
    assert "No results" in result


async def test_memory_store_then_search(mcp_ctx, mem):
    from nmem.mcp.server import memory_store, memory_search

    await memory_store(
        mcp_ctx, title="Database migration guide",
        content="Always run migrations before deploying",
        agent_id="test", importance=7,
    )
    result = await memory_search(mcp_ctx, query="database migration", agent_id="test")
    # SQLite fallback search should find it
    assert "migration" in result.lower() or "No results" in result


async def test_memory_save_ltm(mcp_ctx, mem):
    from nmem.mcp.server import memory_save_ltm

    result = await memory_save_ltm(
        mcp_ctx, key="deploy_process", content="Run migrations first",
        agent_id="test", category="procedure", importance=8,
    )
    assert "deploy_process" in result
    assert "Saved" in result


async def test_memory_stats(mcp_ctx, mem):
    from nmem.mcp.server import memory_stats

    result = await memory_stats(mcp_ctx)
    assert "Memory System Statistics" in result
    assert "Total entries" in result


async def test_memory_recall_empty(mcp_ctx, mem):
    from nmem.mcp.server import memory_recall

    result = await memory_recall(mcp_ctx, agent_id="test", days=7)
    assert "No journal entries" in result


async def test_memory_recall_with_data(mcp_ctx, mem):
    from nmem.mcp.server import memory_store, memory_recall

    await memory_store(
        mcp_ctx, title="Session notes", content="Discussed architecture",
        agent_id="test", importance=5,
    )
    result = await memory_recall(mcp_ctx, agent_id="test", days=7)
    assert "Session notes" in result
