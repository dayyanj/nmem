"""Tests for nmem stats command."""

import asyncio
import os

from nmem.cli.main import app


def test_stats_empty(runner, cli_env):
    runner.invoke(app, ["init", "--sqlite"])
    result = runner.invoke(app, ["stats"])
    assert result.exit_code == 0
    assert "Memory Tier Statistics" in result.output
    assert "Total entries" in result.output


def test_stats_with_data(runner, cli_env):
    """Counts reflect actual data."""
    from nmem import MemorySystem, NmemConfig

    runner.invoke(app, ["init", "--sqlite"])

    async def _populate():
        config = NmemConfig(
            database_url=os.environ["NMEM_DATABASE_URL"],
            embedding={"provider": "noop"},
            llm={"provider": "noop"},
        )
        mem = MemorySystem(config)
        await mem.initialize()
        await mem.ltm.save("agent-a", "fact", "k1", "content 1", importance=5)
        await mem.ltm.save("agent-a", "fact", "k2", "content 2", importance=5)
        await mem.close()

    asyncio.run(_populate())

    result = runner.invoke(app, ["stats"])
    assert result.exit_code == 0
    # Should show 2 LTM entries
    assert "2" in result.output


def test_stats_per_agent_breakdown(runner, cli_env):
    """Per-agent table appears when data exists."""
    from nmem import MemorySystem, NmemConfig

    runner.invoke(app, ["init", "--sqlite"])

    async def _populate():
        config = NmemConfig(
            database_url=os.environ["NMEM_DATABASE_URL"],
            embedding={"provider": "noop"},
            llm={"provider": "noop"},
        )
        mem = MemorySystem(config)
        await mem.initialize()
        await mem.ltm.save("agent-alpha", "fact", "k1", "content", importance=5)
        await mem.ltm.save("agent-beta", "fact", "k2", "content", importance=5)
        await mem.close()

    asyncio.run(_populate())

    result = runner.invoke(app, ["stats"])
    assert result.exit_code == 0
    assert "Per-Agent" in result.output
    assert "agent-alpha" in result.output
    assert "agent-beta" in result.output
