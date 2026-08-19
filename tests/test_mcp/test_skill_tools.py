"""MCP tools for conscious skills + autonomy surfacing."""
from unittest.mock import MagicMock

import pytest
import pytest_asyncio

try:
    import mcp  # noqa: F401
    HAS_MCP = True
except ImportError:
    HAS_MCP = False

from nmem import MemorySystem, NmemConfig

from tests.conftest import TEST_DB_URL, reset_db

pytestmark = pytest.mark.skipif(not HAS_MCP, reason="mcp package not installed")


@pytest_asyncio.fixture
async def mem():
    config = NmemConfig(
        database_url=TEST_DB_URL,
        embedding={"provider": "noop", "dimensions": 384},
        llm={"provider": "noop"},
        consolidation={"enabled": False},
        skills={"enabled": True},
        autonomy={"enabled": True, "proactive_retrieve": True,
                  "surface_recognition_threshold": 0.0, "cooldown_seconds": 0,
                  "novelty_threshold": 1.1},
    )
    system = MemorySystem(config)
    await system.initialize()
    await reset_db(system)
    yield system
    await reset_db(system)
    await system.close()


@pytest.fixture
def ctx(mem):
    c = MagicMock()
    c.request_context.lifespan_context = {"mem": mem}
    return c


async def test_skill_record_and_find(ctx, mem):
    from nmem.mcp.server import memory_skill_record, memory_skill_find

    out = await memory_skill_record(
        ctx, what="retry with exponential backoff and jitter",
        outcome="stopped the thundering herd", worked=True, name="backoff-jitter")
    assert "Recorded skill" in out and "1/1" in out

    found = await memory_skill_find(ctx, query="retry with exponential backoff and jitter")
    assert "backoff-jitter" in found


async def test_skill_reinforce(ctx, mem):
    from nmem.mcp.server import memory_skill_record, memory_skill_reinforce

    await memory_skill_record(ctx, what="warm the cache on boot", worked=True)
    (info,) = await mem.skills.list("active")
    out = await memory_skill_reinforce(ctx, skill_id=info.id, success=False)
    assert "Reinforced" in out
    got = await mem.skills.get(info.id)
    assert got.trial_count == 2


async def test_skill_record_disabled_message(ctx, mem):
    from nmem.mcp.server import memory_skill_record
    mem._config.skills.enabled = False
    out = await memory_skill_record(ctx, what="anything")
    assert "disabled" in out.lower()


async def test_autonomy_surface_tool(ctx, mem):
    from nmem.mcp.server import memory_autonomy_surface

    await mem.ltm.save(agent_id="default", category="fact", key="k",
                       record_type="fact", grounding="confirmed",
                       content="rotate credentials on every deploy window",
                       importance=7)
    out = await memory_autonomy_surface(ctx, query="rotate credentials on every deploy window")
    assert "Surfaced" in out
