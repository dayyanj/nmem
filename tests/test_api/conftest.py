"""
Shared fixtures for API tests — SQLite + noop providers, no external deps.
"""

from __future__ import annotations

import os
import tempfile
from typing import AsyncIterator

import pytest
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient

from nmem import NmemConfig
from nmem.api.main import create_app


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    """Create an httpx AsyncClient wired to a fresh API app + SQLite DB."""
    # Use a unique temp SQLite DB per test
    db_path = tempfile.mktemp(suffix=".db")
    os.environ["NMEM_DATABASE_URL"] = f"sqlite+aiosqlite:///{db_path}"
    os.environ["NMEM_EMBEDDING__PROVIDER"] = "noop"
    os.environ["NMEM_LLM__PROVIDER"] = "noop"

    config = NmemConfig(
        database_url=f"sqlite+aiosqlite:///{db_path}",
    )
    app = create_app(config=config)

    async with LifespanManager(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            yield ac

    # Cleanup
    try:
        os.unlink(db_path)
    except FileNotFoundError:
        pass
