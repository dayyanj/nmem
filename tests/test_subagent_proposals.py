"""
Self-engineering 2B — sub-agent proposals.

From highly-reliable skills, nmem distills rich, PROPOSE-ONLY sub-agent specs.
nmem never executes them. Covers the bounded proposal loop, the acceptance gate,
recipe-body snapshot (not FK), dedup, resolve feedback, and opt-in/off guarantees.
Uses a fake single-turn LLM.
"""
from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy import text

from nmem import MemorySystem, NmemConfig

from tests.conftest import TEST_DB_URL, reset_db


def _patch_llm(mem, payload, calls: list | None = None):
    async def _cj(system, user, **kw):
        if calls is not None:
            calls.append(1)
        return payload
    mem._llm.complete_json = _cj


async def _reliable_skill(mem, what, name="s"):
    info = await mem.skills.record(what, worked=True, name=name)
    for _ in range(4):
        await mem.skills.reinforce(info.id, success=True)   # trial→5, success→5 (1.0)
    return info


_SPEC = {"name": "refund-triage", "system_prompt": "Triage refund requests: verify "
         "the original charge, check for duplicates, then act.",
         "trigger_conditions": "a customer disputes a charge",
         "suggested_tools": ["stripe", "order_lookup"]}


async def _build(**se) -> MemorySystem:
    cfg = {"enabled": True, "propose_subagents": True}
    cfg.update(se)
    system = MemorySystem(NmemConfig(
        database_url=TEST_DB_URL,
        embedding={"provider": "noop", "dimensions": 384},
        llm={"provider": "noop"},
        consolidation={"enabled": False},
        skills={"enabled": True},
        self_engineering=cfg,
    ))
    await system.initialize()
    await reset_db(system)
    return system


@pytest_asyncio.fixture
async def se() -> MemorySystem:
    system = await _build()
    yield system  # type: ignore[misc]
    await reset_db(system)
    await system.close()


async def _count(mem, status="proposed") -> int:
    async with mem._db.session() as s:
        return (await s.execute(text(
            "SELECT COUNT(*) FROM nmem_subagent_proposals WHERE status = :st"),
            {"st": status})).scalar()


@pytest.mark.asyncio
async def test_proposes_rich_spec(se):
    await _reliable_skill(se, "triage and resolve refund disputes")
    _patch_llm(se, _SPEC)
    events = []
    se.on("subagent.proposed")(lambda d: events.append(d))
    n = await se.self_engineering.propose_subagents()
    assert n == 1
    (p,) = await se.self_engineering.list_proposals("proposed")
    assert p["name"] == "refund-triage"
    assert p["system_prompt"] and p["suggested_tools"] == ["stripe", "order_lookup"]
    assert p["reliability_evidence"]["reliability"] == 1.0
    assert events and events[0]["name"] == "refund-triage"


@pytest.mark.asyncio
async def test_snapshots_recipe_body_not_fk(se):
    skill = await _reliable_skill(se, "roll back a bad deploy fast")
    # give the skill a linked active recipe (same source signature)
    sig = se.self_engineering._signature([skill.id])
    from nmem.db.models import ContextRecipeModel
    async with se._db.session() as s:
        s.add(ContextRecipeModel(name="r", situation="rolling back",
                                 body="RECIPE BODY TEXT", source_signature=sig,
                                 status="active"))
        await s.flush()
    _patch_llm(se, _SPEC)
    await se.self_engineering.propose_subagents()
    (p,) = await se.self_engineering.list_proposals("proposed")
    assert p["context_recipe"] == "RECIPE BODY TEXT"     # snapshot copied in


@pytest.mark.asyncio
async def test_respects_call_cap(se):
    se._config.self_engineering.max_llm_calls_per_run = 1
    await _reliable_skill(se, "alpha reliable distinct one", name="a")
    await _reliable_skill(se, "beta reliable distinct two", name="b")
    calls = []
    _patch_llm(se, _SPEC, calls)
    await se.self_engineering.propose_subagents()
    assert len(calls) == 1
    assert await _count(se) == 1


@pytest.mark.asyncio
async def test_acceptance_gate_bad_tools_rejected(se):
    await _reliable_skill(se, "reliable approach for bad tools test")
    _patch_llm(se, {"name": "n", "system_prompt": "do the thing",
                    "suggested_tools": "not-a-list"})
    rejected = []
    se.on("subagent.rejected")(lambda d: rejected.append(d))
    n = await se.self_engineering.propose_subagents()
    assert n == 0
    assert rejected and rejected[0]["reason"] == "bad_tools"


@pytest.mark.asyncio
async def test_gate_rejects_nonstr_trigger_and_bad_tools(se):
    await _reliable_skill(se, "reliable approach one gate", name="g1")
    _patch_llm(se, {"name": "n", "system_prompt": "do it", "trigger_conditions": 123})
    assert await se.self_engineering.propose_subagents() == 0   # non-str trigger


@pytest.mark.asyncio
async def test_gate_rejects_garbage_tool_strings(se):
    await _reliable_skill(se, "reliable approach two gate", name="g2")
    _patch_llm(se, {"name": "n", "system_prompt": "do it",
                    "suggested_tools": ["", "x" * 200]})
    rejected = []
    se.on("subagent.rejected")(lambda d: rejected.append(d))
    assert await se.self_engineering.propose_subagents() == 0
    assert rejected and rejected[0]["reason"] == "bad_tools"


@pytest.mark.asyncio
async def test_live_proposal_uniqueness_enforced(se):
    """The partial-unique index blocks a second live proposal for a cluster even
    if the read-before-write dedup is bypassed (concurrent-run backstop)."""
    from nmem.db.models import SubagentProposalModel
    from sqlalchemy.exc import IntegrityError
    sig = "dupsig123"
    async with se._db.session() as s:
        s.add(SubagentProposalModel(name="a", system_prompt="p",
                                    source_signature=sig, status="proposed"))
        await s.flush()
    with pytest.raises(IntegrityError):
        async with se._db.session() as s:
            s.add(SubagentProposalModel(name="b", system_prompt="p",
                                        source_signature=sig, status="proposed"))
            await s.flush()


@pytest.mark.asyncio
async def test_dedup_no_second_proposal(se):
    await _reliable_skill(se, "a repeatable reliable behavior")
    _patch_llm(se, _SPEC)
    await se.self_engineering.propose_subagents()
    await se.self_engineering.propose_subagents()   # signature already has a proposal
    assert await _count(se) == 1


@pytest.mark.asyncio
async def test_resolve_feedback(se):
    await _reliable_skill(se, "resolvable reliable behavior")
    _patch_llm(se, _SPEC)
    await se.self_engineering.propose_subagents()
    (p,) = await se.self_engineering.list_proposals("proposed")
    assert await se.self_engineering.resolve_proposal(p["id"], accepted=True) is True
    assert await _count(se, "accepted") == 1
    assert await se.self_engineering.resolve_proposal(999999, accepted=False) is False


@pytest.mark.asyncio
async def test_noop_llm_and_disabled():
    # noop LLM → no proposal
    on = await _build()
    try:
        await _reliable_skill(on, "reliable but noop llm")
        assert await on.self_engineering.propose_subagents() == 0
    finally:
        await reset_db(on); await on.close()
    # propose_subagents flag off → inert even with a fake LLM
    off = await _build(propose_subagents=False)
    try:
        await _reliable_skill(off, "reliable but proposals off")
        _patch_llm(off, _SPEC)
        assert await off.self_engineering.propose_subagents() == 0
        assert await off.self_engineering.list_proposals() == []
    finally:
        await reset_db(off); await off.close()
