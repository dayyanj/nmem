"""
Acceptance tests for nmem v0.7.0 Stage E — MCP symbolic wiring hook.

Stage E's contract: the nmem MCP lifespan conditionally imports
nmem_sym.mcp_integration.wire_into_mcp and calls it when
NMEM_SYMBOLIC_ENABLED=1.

Graceful degradation paths (all covered):
  - Flag unset → nmem_sym not imported at all, sym stays None
  - Flag set but nmem_sym not installed → ImportError caught, sym None
  - Flag set + wire_into_mcp raises → exception caught, sym None
  - Flag set + wire succeeds with enabled=False → info logged, sym stored
  - Flag set + wire succeeds with enabled=True → info logged with tool count

Teardown ordering: sym_state.close() runs BEFORE mem.close() so nmem-sym's
background tasks stop referencing memory before it disappears.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _mock_mem():
    fake = MagicMock()
    fake.initialize = AsyncMock()
    fake.start_consolidation = MagicMock()
    fake.stop_consolidation = MagicMock()
    fake.close = AsyncMock()
    return fake


class TestSymbolicHook:

    @pytest.mark.asyncio
    async def test_flag_off_skips_import(self, monkeypatch):
        """NMEM_SYMBOLIC_ENABLED unset → nmem_sym is never imported.
        Verified by asserting the wire_into_mcp import path is untouched."""
        monkeypatch.delenv("NMEM_SYMBOLIC_ENABLED", raising=False)
        monkeypatch.delenv("NMEM_START_CONSOLIDATION", raising=False)

        from nmem.mcp import server
        mem = _mock_mem()
        wire_calls: list = []

        # Patch the wire_into_mcp entry — if imported it would be
        # called, but the flag guard should prevent even the import
        fake_wire = AsyncMock(side_effect=lambda *a, **kw: wire_calls.append((a, kw)))

        with patch("nmem.MemorySystem", return_value=mem):
            # Even if nmem_sym is available, the flag guard prevents it
            with patch.dict("sys.modules", {
                "nmem_sym.mcp_integration": MagicMock(wire_into_mcp=fake_wire),
            }):
                async with server.lifespan(MagicMock()) as ctx:
                    assert ctx["sym"] is None
        assert wire_calls == [], "wire_into_mcp must not be called when flag is off"

    @pytest.mark.asyncio
    async def test_flag_on_calls_wire(self, monkeypatch):
        """NMEM_SYMBOLIC_ENABLED=1 → wire_into_mcp called, sym_state
        yielded in context."""
        monkeypatch.setenv("NMEM_SYMBOLIC_ENABLED", "1")
        monkeypatch.delenv("NMEM_START_CONSOLIDATION", raising=False)

        from nmem.mcp import server
        mem = _mock_mem()

        # Fake wired state
        fake_state = MagicMock()
        fake_state.enabled = True
        fake_state.tools_registered = ["memory_augment", "memory_hypothesize"]
        fake_state.close = AsyncMock()

        fake_wire = AsyncMock(return_value=fake_state)
        fake_module = MagicMock(wire_into_mcp=fake_wire)

        with patch("nmem.MemorySystem", return_value=mem), \
             patch.dict("sys.modules", {
                 "nmem_sym": MagicMock(),
                 "nmem_sym.mcp_integration": fake_module,
             }):
            async with server.lifespan(MagicMock()) as ctx:
                assert ctx["sym"] is fake_state
                fake_wire.assert_awaited_once()

        # sym_state.close was called in teardown
        fake_state.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_import_error_yields_none(self, monkeypatch):
        """nmem_sym not installed → ImportError caught, sym is None,
        MCP continues."""
        monkeypatch.setenv("NMEM_SYMBOLIC_ENABLED", "1")
        monkeypatch.delenv("NMEM_START_CONSOLIDATION", raising=False)

        from nmem.mcp import server
        mem = _mock_mem()

        # Simulate ImportError via a module that raises on attribute
        # access. When the lifespan does `from nmem_sym.mcp_integration
        # import wire_into_mcp`, Python attempts to grab the module
        # from sys.modules and read its wire_into_mcp attribute — if
        # we replace the entry with a module whose __getattr__ raises
        # ImportError, the from-import fails cleanly.
        import types
        fake_module = types.ModuleType("nmem_sym.mcp_integration")
        def raise_import_error(name):
            raise ImportError(f"cannot import name '{name}'")
        fake_module.__getattr__ = raise_import_error

        with patch("nmem.MemorySystem", return_value=mem), \
             patch.dict("sys.modules", {
                 "nmem_sym.mcp_integration": fake_module,
             }):
            # MCP server must continue despite the missing symbol
            async with server.lifespan(MagicMock()) as ctx:
                assert ctx["sym"] is None
                assert ctx["mem"] is mem

    @pytest.mark.asyncio
    async def test_wire_exception_yields_none(self, monkeypatch):
        """wire_into_mcp is supposed to never raise, but the belt-and-
        suspenders except in server.py catches any escape."""
        monkeypatch.setenv("NMEM_SYMBOLIC_ENABLED", "1")
        monkeypatch.delenv("NMEM_START_CONSOLIDATION", raising=False)

        from nmem.mcp import server
        mem = _mock_mem()

        fake_wire = AsyncMock(side_effect=RuntimeError("wire escaped its contract"))
        fake_module = MagicMock(wire_into_mcp=fake_wire)

        with patch("nmem.MemorySystem", return_value=mem), \
             patch.dict("sys.modules", {
                 "nmem_sym": MagicMock(),
                 "nmem_sym.mcp_integration": fake_module,
             }):
            # MUST NOT raise — MCP server must continue
            async with server.lifespan(MagicMock()) as ctx:
                assert ctx["sym"] is None
                assert ctx["mem"] is mem

    @pytest.mark.asyncio
    async def test_disabled_state_still_stored(self, monkeypatch):
        """When wire_into_mcp returns WiredState(enabled=False)
        (e.g. missing DSN), it's still yielded in the context so
        callers can introspect the disabled_reason. But close() is
        still called on teardown."""
        monkeypatch.setenv("NMEM_SYMBOLIC_ENABLED", "1")
        monkeypatch.delenv("NMEM_START_CONSOLIDATION", raising=False)

        from nmem.mcp import server
        mem = _mock_mem()

        fake_state = MagicMock()
        fake_state.enabled = False
        fake_state.disabled_reason = "Could not derive DSN"
        fake_state.tools_registered = []
        fake_state.close = AsyncMock()

        fake_wire = AsyncMock(return_value=fake_state)
        fake_module = MagicMock(wire_into_mcp=fake_wire)

        with patch("nmem.MemorySystem", return_value=mem), \
             patch.dict("sys.modules", {
                 "nmem_sym": MagicMock(),
                 "nmem_sym.mcp_integration": fake_module,
             }):
            async with server.lifespan(MagicMock()) as ctx:
                assert ctx["sym"] is fake_state
                assert ctx["sym"].enabled is False

        fake_state.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_teardown_order_sym_before_mem_close(self, monkeypatch):
        """sym_state.close must run BEFORE mem.close so nmem-sym's
        background tasks stop referencing memory before it closes.
        Reversing this order causes 'connection closed' errors from
        the drive tick loop mid-query."""
        monkeypatch.setenv("NMEM_SYMBOLIC_ENABLED", "1")
        monkeypatch.delenv("NMEM_START_CONSOLIDATION", raising=False)

        from nmem.mcp import server
        mem = _mock_mem()

        call_order: list[str] = []
        fake_state = MagicMock()
        fake_state.enabled = True
        fake_state.tools_registered = []

        async def sym_close():
            call_order.append("sym_close")
        fake_state.close = sym_close

        async def mem_close():
            call_order.append("mem_close")
        mem.close = mem_close

        fake_wire = AsyncMock(return_value=fake_state)
        fake_module = MagicMock(wire_into_mcp=fake_wire)

        with patch("nmem.MemorySystem", return_value=mem), \
             patch.dict("sys.modules", {
                 "nmem_sym": MagicMock(),
                 "nmem_sym.mcp_integration": fake_module,
             }):
            async with server.lifespan(MagicMock()):
                pass

        assert call_order == ["sym_close", "mem_close"], (
            "sym_state.close must run before mem.close so nmem-sym's "
            "background tasks stop referencing memory before it closes"
        )

    @pytest.mark.asyncio
    async def test_sym_teardown_error_swallowed(self, monkeypatch):
        """If sym_state.close raises, mem.close must still run."""
        monkeypatch.setenv("NMEM_SYMBOLIC_ENABLED", "1")
        monkeypatch.delenv("NMEM_START_CONSOLIDATION", raising=False)

        from nmem.mcp import server
        mem = _mock_mem()

        fake_state = MagicMock()
        fake_state.enabled = True
        fake_state.tools_registered = []
        fake_state.close = AsyncMock(side_effect=RuntimeError("sym close failed"))

        fake_wire = AsyncMock(return_value=fake_state)
        fake_module = MagicMock(wire_into_mcp=fake_wire)

        with patch("nmem.MemorySystem", return_value=mem), \
             patch.dict("sys.modules", {
                 "nmem_sym": MagicMock(),
                 "nmem_sym.mcp_integration": fake_module,
             }):
            async with server.lifespan(MagicMock()):
                pass

        # mem.close ran despite the sym close failure
        mem.close.assert_awaited()
