"""Tests for §4: Progressive disclosure search (compact mode + memory_get)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from nmem import MemorySystem


@pytest.mark.asyncio
async def test_search_compact_includes_ids(mem_with_data: MemorySystem) -> None:
    """Compact search results include entry IDs for follow-up fetching."""
    # mem_with_data has pre-populated entries across tiers
    results = await mem_with_data.search(agent_id="agent1", query="refund", top_k=5)
    # Results should have IDs
    for r in results:
        assert r.id is not None
        assert r.tier in ("journal", "ltm", "shared", "entity", "policy")


@pytest.mark.asyncio
async def test_mcp_memory_search_compact_mode() -> None:
    """MCP memory_search with compact=True returns shorter previews."""
    from nmem.mcp.server import memory_search
    from nmem.types import SearchResult

    # Mock context
    long_content = (
        "Themes use tokens.json as the source of truth. Run generate_tokens_css to create CSS "
        "from tokens. Then run update_theme_css to push the compiled CSS into the Theme model. "
        "Never manually edit tokens.css — it is generated and will be overwritten. "
        "THIS TAIL SHOULD NOT APPEAR IN COMPACT MODE."
    )
    mock_mem = MagicMock()
    mock_mem.search = AsyncMock(return_value=[
        SearchResult(
            tier="ltm", id=42, score=0.89,
            content=long_content,
            title="Theme Token Workflow", key="theme_tokens",
        ),
        SearchResult(
            tier="journal", id=107, score=0.72,
            content="The update server requires docker-compose.production.yml not the default one",
            title="Fixed deployment issue",
        ),
    ])

    ctx = MagicMock()
    ctx.request_context.lifespan_context = {"mem": mock_mem}

    # Compact mode
    result = await memory_search(ctx, query="theme tokens", compact=True)
    assert "ltm#42" in result
    assert "journal#107" in result
    assert "memory_get" in result  # Should mention follow-up tool
    # Content should be truncated to ~100 chars — the tail should not appear
    assert "THIS TAIL SHOULD NOT APPEAR" not in result

    # Non-compact mode (backward compat)
    result_full = await memory_search(ctx, query="theme tokens", compact=False)
    assert "tokens.json" in result_full
    assert "memory_get" not in result_full


@pytest.mark.asyncio
async def test_mcp_memory_get() -> None:
    """MCP memory_get fetches full content for specific IDs."""
    from nmem.mcp.server import memory_get

    # We need a mock that simulates the DB session
    mock_mem = MagicMock()
    mock_db = MagicMock()
    mock_mem._db = mock_db

    # Create mock DB rows
    mock_row = MagicMock()
    mock_row.id = 42
    mock_row.category = "procedure"
    mock_row.key = "deploy_process"
    mock_row.content = "Step 1: Build. Step 2: Test. Step 3: Deploy."
    mock_row.importance = 8
    mock_row.salience = 0.95
    mock_row.version = 3

    # Mock the session context manager
    mock_session = MagicMock()
    mock_result = MagicMock()
    mock_scalars = MagicMock()
    mock_scalars.all.return_value = [mock_row]
    mock_result.scalars.return_value = mock_scalars
    mock_session.execute = AsyncMock(return_value=mock_result)

    mock_session_ctx = MagicMock()
    mock_session_ctx.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session_ctx.__aexit__ = AsyncMock(return_value=None)
    mock_db.session.return_value = mock_session_ctx

    ctx = MagicMock()
    ctx.request_context.lifespan_context = {"mem": mock_mem}

    result = await memory_get(ctx, ids=[42], tier="ltm")
    assert "deploy_process" in result
    assert "Step 1: Build" in result
    assert "v3" in result


@pytest.mark.asyncio
async def test_mcp_memory_get_invalid_tier() -> None:
    """MCP memory_get with invalid tier returns error."""
    from nmem.mcp.server import memory_get

    ctx = MagicMock()
    ctx.request_context.lifespan_context = {"mem": MagicMock()}

    result = await memory_get(ctx, ids=[1], tier="invalid")
    assert "Error" in result
    assert "unknown tier" in result


@pytest.mark.asyncio
async def test_mcp_memory_get_empty_ids() -> None:
    """MCP memory_get with no IDs returns appropriate message."""
    from nmem.mcp.server import memory_get

    ctx = MagicMock()
    ctx.request_context.lifespan_context = {"mem": MagicMock()}

    result = await memory_get(ctx, ids=[], tier="ltm")
    assert "No IDs" in result
