"""Tests for nmem search command."""

import asyncio
import json

from nmem.cli.main import app


def test_search_empty_db(runner, cli_env):
    # Init first, then search
    runner.invoke(app, ["init", "--sqlite"])
    result = runner.invoke(app, ["search", "anything"])
    assert result.exit_code == 0
    assert "no results" in result.output.lower()


def test_search_finds_results(runner, cli_env):
    """Pre-populate via Python API, then CLI search finds entries."""
    from nmem import MemorySystem, NmemConfig
    import os

    # Init DB
    runner.invoke(app, ["init", "--sqlite"])

    # Insert data via Python API
    async def _populate():
        config = NmemConfig(
            database_url=os.environ["NMEM_DATABASE_URL"],
            embedding={"provider": "noop"},
            llm={"provider": "noop"},
        )
        mem = MemorySystem(config)
        await mem.initialize()
        await mem.ltm.save(
            "test-agent", "fact", "test_key",
            "The deployment process requires running migrations first",
            importance=8,
        )
        await mem.close()

    asyncio.run(_populate())

    result = runner.invoke(app, ["search", "deployment migrations", "--agent-id", "test-agent"])
    assert result.exit_code == 0
    assert "deployment" in result.output.lower() or "migration" in result.output.lower()


def test_search_json_output(runner, cli_env):
    """--json flag outputs valid JSON."""
    runner.invoke(app, ["init", "--sqlite"])
    result = runner.invoke(app, ["search", "anything", "--json"])
    assert result.exit_code == 0
    parsed = json.loads(result.output)
    assert isinstance(parsed, list)
