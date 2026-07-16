"""
Acceptance tests for nmem v0.7.0 Stage A — MCP prerequisites.

Two small changes in nmem that unblock nmem-sym 0.7.0's MCP
integration:

  A.1  DatabaseManager exposes a public `url` property so downstream
       integrations (nmem-sym's MCP wiring) can derive their own DSN
       without reading the private `_url`.

  A.2  The MCP lifespan calls `mem.start_consolidation()` so
       consolidation hooks — nightly synthesis, hourly full-cycle
       steps — actually fire. Prior to this, the MCP lifespan
       omitted the start call and any consumer registering
       consolidation hooks got registered-but-never-called hooks.
       Env-gated via NMEM_START_CONSOLIDATION so short-lived dev
       contexts can opt out.
"""
from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ──────────────────────────────────────────────────────────────
# A.1 — DatabaseManager.url public property
# ──────────────────────────────────────────────────────────────


class TestDatabaseManagerUrl:

    def test_url_property_returns_constructor_arg(self):
        from nmem.db.session import DatabaseManager
        dm = DatabaseManager("sqlite+aiosqlite:///:memory:")
        assert dm.url == "sqlite+aiosqlite:///:memory:"

    def test_url_property_matches_private_url(self):
        """The public accessor must return exactly what the internal
        engine was constructed with — no post-processing that would
        break DSN derivation."""
        from nmem.db.session import DatabaseManager
        dm = DatabaseManager("postgresql+asyncpg://user:pass@host/db")
        assert dm.url == dm._url

    def test_url_strippable_to_asyncpg_dsn(self):
        """The nmem-sym MCP wiring pattern (strip +asyncpg to get a
        raw asyncpg DSN) works against the public url."""
        from nmem.db.session import DatabaseManager
        dm = DatabaseManager("postgresql+asyncpg://user:pass@host/db")
        asyncpg_dsn = dm.url.replace("+asyncpg", "")
        assert asyncpg_dsn == "postgresql://user:pass@host/db"

    def test_url_unchanged_for_sqlite(self):
        """SQLite URLs don't have +asyncpg; replace should be a no-op."""
        from nmem.db.session import DatabaseManager
        dm = DatabaseManager("sqlite+aiosqlite:///:memory:")
        # nmem-sym's translation is a no-op on non-asyncpg URLs
        asyncpg_dsn = dm.url.replace("+asyncpg", "")
        assert asyncpg_dsn == "sqlite+aiosqlite:///:memory:"


# ──────────────────────────────────────────────────────────────
# A.2 — MCP lifespan starts consolidation
# ──────────────────────────────────────────────────────────────


class TestMcpLifespanConsolidation:

    @pytest.mark.asyncio
    async def test_lifespan_starts_consolidation_by_default(self, monkeypatch):
        """Default behaviour — env var unset → consolidation starts."""
        monkeypatch.delenv("NMEM_START_CONSOLIDATION", raising=False)

        from nmem.mcp import server

        # Mock MemorySystem so we don't need a real DB
        fake_mem = MagicMock()
        fake_mem.initialize = AsyncMock()
        fake_mem.start_consolidation = MagicMock(return_value=MagicMock())
        fake_mem.stop_consolidation = MagicMock()
        fake_mem.close = AsyncMock()

        with patch.object(server, "MemorySystem", return_value=fake_mem, create=True):
            # MemorySystem is imported inside lifespan; patch the module the
            # import resolves to.
            with patch("nmem.MemorySystem", return_value=fake_mem):
                # Enter the lifespan
                async with server.lifespan(MagicMock()) as ctx:
                    assert ctx["mem"] is fake_mem
                    fake_mem.start_consolidation.assert_called_once()

        # And stop_consolidation must be called on exit
        fake_mem.stop_consolidation.assert_called_once()

    @pytest.mark.asyncio
    async def test_lifespan_skips_consolidation_when_env_zero(self, monkeypatch):
        """NMEM_START_CONSOLIDATION=0 → consolidation loop not started."""
        monkeypatch.setenv("NMEM_START_CONSOLIDATION", "0")

        from nmem.mcp import server

        fake_mem = MagicMock()
        fake_mem.initialize = AsyncMock()
        fake_mem.start_consolidation = MagicMock()
        fake_mem.stop_consolidation = MagicMock()
        fake_mem.close = AsyncMock()

        with patch("nmem.MemorySystem", return_value=fake_mem):
            async with server.lifespan(MagicMock()) as ctx:
                assert ctx["mem"] is fake_mem
                fake_mem.start_consolidation.assert_not_called()

        # And stop_consolidation must NOT be called since we never
        # started the loop
        fake_mem.stop_consolidation.assert_not_called()

    @pytest.mark.asyncio
    async def test_lifespan_survives_start_consolidation_failure(self, monkeypatch):
        """If start_consolidation raises, the MCP server continues
        without it — degraded but usable."""
        monkeypatch.setenv("NMEM_START_CONSOLIDATION", "1")

        from nmem.mcp import server

        fake_mem = MagicMock()
        fake_mem.initialize = AsyncMock()
        fake_mem.start_consolidation = MagicMock(
            side_effect=RuntimeError("consolidation subsystem down")
        )
        fake_mem.stop_consolidation = MagicMock()
        fake_mem.close = AsyncMock()

        with patch("nmem.MemorySystem", return_value=fake_mem):
            # Must not raise — MCP continues without the loop
            async with server.lifespan(MagicMock()) as ctx:
                assert ctx["mem"] is fake_mem

        # And stop_consolidation is not called (we never got a task)
        fake_mem.stop_consolidation.assert_not_called()

    @pytest.mark.asyncio
    async def test_lifespan_cleans_up_on_shutdown(self, monkeypatch):
        """Even if consumers registered hooks and the loop ran, exit
        must call stop_consolidation before mem.close so the task
        gets a chance to shut down cleanly."""
        monkeypatch.setenv("NMEM_START_CONSOLIDATION", "1")

        from nmem.mcp import server

        fake_mem = MagicMock()
        fake_mem.initialize = AsyncMock()
        fake_mem.start_consolidation = MagicMock(return_value=MagicMock())
        call_order: list[str] = []
        fake_mem.stop_consolidation = MagicMock(
            side_effect=lambda: call_order.append("stop")
        )
        async def fake_close():
            call_order.append("close")
        fake_mem.close = fake_close

        with patch("nmem.MemorySystem", return_value=fake_mem):
            async with server.lifespan(MagicMock()):
                pass

        assert call_order == ["stop", "close"], (
            "stop_consolidation must be called BEFORE mem.close so the "
            "background task has a chance to shut down cleanly. Reversing "
            "this order can cause 'connection closed' errors from the "
            "consolidator's in-flight queries."
        )
