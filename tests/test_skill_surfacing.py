"""
Skill surfacing into assembled context — the opt-in "## Relevant Skills"
section in PromptBuilder.build() and mem.briefing(). Off by default.
"""
from __future__ import annotations

import pytest
import pytest_asyncio

from nmem import MemorySystem, NmemConfig

from tests.conftest import TEST_DB_URL, reset_db


async def _build(**skills) -> MemorySystem:
    s = {"enabled": True}
    s.update(skills)
    system = MemorySystem(NmemConfig(
        database_url=TEST_DB_URL,
        embedding={"provider": "noop", "dimensions": 384},
        llm={"provider": "noop"},
        consolidation={"enabled": False},
        skills=s,
    ))
    await system.initialize()
    await reset_db(system)
    return system


@pytest_asyncio.fixture
async def sk_prompt() -> MemorySystem:
    system = await _build(include_in_prompt=True, include_in_briefing=True)
    yield system  # type: ignore[misc]
    await reset_db(system)
    await system.close()


@pytest.mark.asyncio
async def test_prompt_includes_skills_when_enabled(sk_prompt):
    await sk_prompt.skills.record(
        "shard the write path by tenant id", outcome="removed lock contention",
        worked=True, name="tenant-sharding", agent_id="a1")
    ctx = await sk_prompt.prompt.build("a1", query="shard the write path by tenant id")
    assert ctx.skills != ""
    assert "tenant-sharding" in ctx.skills
    assert "## Relevant Skills" in ctx.full_injection


@pytest.mark.asyncio
async def test_prompt_omits_skills_when_flag_off(mem):
    # default mem: skills disabled → no skills section, no crash
    ctx = await mem.prompt.build("a1", query="anything at all")
    assert ctx.skills == ""
    assert "## Relevant Skills" not in ctx.full_injection


@pytest.mark.asyncio
async def test_briefing_includes_skills_when_enabled(sk_prompt):
    await sk_prompt.skills.record(
        "debounce the webhook handler", outcome="stopped duplicate processing",
        worked=True, name="webhook-debounce", agent_id="a1")
    b = await sk_prompt.briefing("a1", query="debounce the webhook handler",
                                 max_tokens=2000)
    assert "Relevant Skills" in b.content
    assert "webhook-debounce" in b.content


@pytest.mark.asyncio
async def test_briefing_omits_skills_when_flag_off(mem):
    b = await mem.briefing("a1", query="anything", max_tokens=2000)
    assert "Relevant Skills" not in b.content
