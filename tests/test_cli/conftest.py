"""Shared fixtures for CLI tests — Postgres + noop providers, no external deps."""

import asyncio
import os
import pytest
from typer.testing import CliRunner

from tests.conftest import TEST_DB_URL, reset_db


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def cli_env(tmp_path, monkeypatch):
    """Set environment for CLI tests: Postgres + noop providers, clean DB.

    Postgres is a single shared DB, so reset it before each CLI test to
    stay isolated from other tests.
    """
    monkeypatch.setenv("NMEM_DATABASE_URL", TEST_DB_URL)
    monkeypatch.setenv("NMEM_EMBEDDING__PROVIDER", "noop")
    monkeypatch.setenv("NMEM_LLM__PROVIDER", "noop")
    # Change to tmp_path so any file creation doesn't pollute project
    monkeypatch.chdir(tmp_path)

    async def _reset() -> None:
        from nmem import MemorySystem, NmemConfig

        config = NmemConfig(
            database_url=TEST_DB_URL,
            embedding={"provider": "noop", "dimensions": 384},
            llm={"provider": "noop"},
            consolidation={"enabled": False},
        )
        system = MemorySystem(config)
        await system.initialize()
        await reset_db(system)
        await system.close()

    asyncio.run(_reset())
    return tmp_path
