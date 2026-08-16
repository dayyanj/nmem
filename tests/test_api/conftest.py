"""
Shared fixtures for API tests — Postgres + noop providers, no external deps.
"""

from __future__ import annotations

import os
from typing import AsyncIterator

import pytest
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient

from nmem import NmemConfig
from nmem.api.main import create_app

from tests.conftest import TEST_DB_URL, reset_db


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    """Create an httpx AsyncClient wired to a fresh API app + Postgres DB."""
    os.environ["NMEM_DATABASE_URL"] = TEST_DB_URL
    os.environ["NMEM_EMBEDDING__PROVIDER"] = "noop"
    os.environ["NMEM_LLM__PROVIDER"] = "noop"

    config = NmemConfig(
        database_url=TEST_DB_URL,
    )
    app = create_app(config=config)

    async with LifespanManager(app):
        # Postgres is a single shared DB — reset before each API test.
        await reset_db(app.state.mem)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            yield ac
