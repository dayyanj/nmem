"""
Acceptance tests for nmem v0.7.0 Stage F — briefing integration with
nmem-sym.

Stage F's contract from nmem-sym docs/0.7.0-implementation-plan.md:

  memory_briefing MCP tool auto-includes a "Relevant grounded
  hypotheses" section pulled from the wired SymbolBridge's
  augment_search() when:

    1. nmem-sym is wired (sym_state.enabled=True)
    2. A query was provided (there's something to relate hypotheses to)
    3. NMEM_BRIEFING_INCLUDE_SYMBOLS is not "0" (default on per user Q4)
    4. augment_search returns non-empty context

  All other conditions → the briefing is unchanged shape, preserving
  backwards-compat for consumers that opted out or aren't wired.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest


def _briefing_result(content: str = "briefing content"):
    """A minimal BriefingResult-like object the memory_briefing tool
    expects to unpack."""
    result = MagicMock()
    result.content = content
    result.recognition_breakdown = {"KNOWN": 2, "FAMILIAR": 1, "UNCERTAIN": 0}
    result.facts_included = 3
    result.facts_available = 5
    result.token_estimate = 150
    return result


def _ctx(mem, sym_state):
    """Build a Context stub for the memory_briefing tool."""
    ctx = MagicMock()
    ctx.request_context.lifespan_context = {"mem": mem, "sym": sym_state}
    return ctx


class TestBriefingSymbolSection:

    @pytest.mark.asyncio
    async def test_no_query_no_symbols_section(self, monkeypatch):
        """Query-less briefing has nothing to relate hypotheses to —
        section not added."""
        monkeypatch.delenv("NMEM_BRIEFING_INCLUDE_SYMBOLS", raising=False)

        from nmem.mcp import server

        mem = MagicMock()
        mem.briefing = AsyncMock(return_value=_briefing_result("some content"))

        sym_state = MagicMock()
        sym_state.enabled = True
        sym_state.bridge.augment_search = AsyncMock(return_value="should not appear")

        # No query
        result = await server.memory_briefing(
            _ctx(mem, sym_state), query=None,
        )
        assert "Relevant grounded hypotheses" not in result
        sym_state.bridge.augment_search.assert_not_called()

    @pytest.mark.asyncio
    async def test_sym_disabled_no_symbols_section(self, monkeypatch):
        """When wire_into_mcp returned WiredState(enabled=False), the
        briefing doesn't try to augment."""
        monkeypatch.delenv("NMEM_BRIEFING_INCLUDE_SYMBOLS", raising=False)

        from nmem.mcp import server

        mem = MagicMock()
        mem.briefing = AsyncMock(return_value=_briefing_result())

        sym_state = MagicMock()
        sym_state.enabled = False
        sym_state.bridge = MagicMock()
        sym_state.bridge.augment_search = AsyncMock()

        result = await server.memory_briefing(
            _ctx(mem, sym_state), query="anything",
        )
        assert "Relevant grounded hypotheses" not in result
        sym_state.bridge.augment_search.assert_not_called()

    @pytest.mark.asyncio
    async def test_sym_none_no_symbols_section(self, monkeypatch):
        """MCP server running without nmem-sym wired → briefing works
        exactly as before, no section."""
        monkeypatch.delenv("NMEM_BRIEFING_INCLUDE_SYMBOLS", raising=False)

        from nmem.mcp import server

        mem = MagicMock()
        mem.briefing = AsyncMock(return_value=_briefing_result())

        result = await server.memory_briefing(
            _ctx(mem, None), query="anything",
        )
        assert "Relevant grounded hypotheses" not in result

    @pytest.mark.asyncio
    async def test_env_opt_out_no_symbols_section(self, monkeypatch):
        """NMEM_BRIEFING_INCLUDE_SYMBOLS=0 → section not added even
        when everything else is wired. Backwards-compat for consumers
        who need strict briefing shape."""
        monkeypatch.setenv("NMEM_BRIEFING_INCLUDE_SYMBOLS", "0")

        from nmem.mcp import server

        mem = MagicMock()
        mem.briefing = AsyncMock(return_value=_briefing_result())

        sym_state = MagicMock()
        sym_state.enabled = True
        sym_state.bridge.augment_search = AsyncMock(return_value="hypothesis text")

        result = await server.memory_briefing(
            _ctx(mem, sym_state), query="the query",
        )
        assert "Relevant grounded hypotheses" not in result
        sym_state.bridge.augment_search.assert_not_called()

    @pytest.mark.asyncio
    async def test_happy_path_appends_section(self, monkeypatch):
        """Wired + query + default env → section is appended."""
        monkeypatch.delenv("NMEM_BRIEFING_INCLUDE_SYMBOLS", raising=False)

        from nmem.mcp import server

        mem = MagicMock()
        mem.briefing = AsyncMock(return_value=_briefing_result("base briefing"))

        sym_state = MagicMock()
        sym_state.enabled = True
        sym_state.bridge.augment_search = AsyncMock(
            return_value="- Hypothesis 1: X causes Y\n- Hypothesis 2: A relates B",
        )

        result = await server.memory_briefing(
            _ctx(mem, sym_state), query="X and Y",
        )
        assert "Relevant grounded hypotheses" in result
        assert "Hypothesis 1: X causes Y" in result
        assert "Hypothesis 2: A relates B" in result
        # Base briefing content still present
        assert "base briefing" in result
        # augment_search called with the query
        sym_state.bridge.augment_search.assert_awaited_once_with("X and Y")

    @pytest.mark.asyncio
    async def test_empty_augment_result_no_section(self, monkeypatch):
        """When augment_search returns empty/whitespace, don't add a
        pointless empty section."""
        monkeypatch.delenv("NMEM_BRIEFING_INCLUDE_SYMBOLS", raising=False)

        from nmem.mcp import server

        mem = MagicMock()
        mem.briefing = AsyncMock(return_value=_briefing_result())

        sym_state = MagicMock()
        sym_state.enabled = True
        sym_state.bridge.augment_search = AsyncMock(return_value="   ")

        result = await server.memory_briefing(
            _ctx(mem, sym_state), query="q",
        )
        assert "Relevant grounded hypotheses" not in result

    @pytest.mark.asyncio
    async def test_augment_search_failure_swallowed(self, monkeypatch):
        """augment_search raising must NOT break the whole briefing —
        section omitted, rest of the response intact."""
        monkeypatch.delenv("NMEM_BRIEFING_INCLUDE_SYMBOLS", raising=False)

        from nmem.mcp import server

        mem = MagicMock()
        mem.briefing = AsyncMock(return_value=_briefing_result("base"))

        sym_state = MagicMock()
        sym_state.enabled = True
        sym_state.bridge.augment_search = AsyncMock(
            side_effect=RuntimeError("bridge dead"),
        )

        result = await server.memory_briefing(
            _ctx(mem, sym_state), query="q",
        )
        # Symbols section omitted
        assert "Relevant grounded hypotheses" not in result
        # Base briefing preserved
        assert "base" in result
