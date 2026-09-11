"""build_experiential_sink — the act→learn sink, and its continuity write.

Pursuing a goal is an action; on completion the sink records it as the agent's last action
so a wake snapshot reflects what it last DID autonomously. Pure unit tests over fakes.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from nmem.agent_core.actuation import build_experiential_sink


@dataclass
class _Proposal:
    id: str = "pursue:5"
    action_type: str = "sandbox_seek"
    source: str = "drive:novelty"


@dataclass
class _Outcome:
    observations: dict = field(default_factory=dict)
    actual_outcome: str = ""


class _Bridge:
    def __init__(self):
        self.outcomes = []
        self.discharges = []

    async def record_action_outcome(self, **kw):
        self.outcomes.append(kw)

    async def discharge_drive(self, name, *, outcome_strength=1.0):
        self.discharges.append((name, outcome_strength))


class _Journal:
    def __init__(self):
        self.added = []

    async def add(self, **kw):
        self.added.append(kw)


class _Mem:
    def __init__(self):
        self.journal = _Journal()
        self.checkpoints = []

    async def save_continuity_checkpoint(self, agent_id, *, last_action=None, **kw):
        self.checkpoints.append({"agent_id": agent_id, "last_action": last_action})


@pytest.mark.asyncio
async def test_verified_pursuit_records_action_checkpoint():
    bridge, mem = _Bridge(), _Mem()
    sink = build_experiential_sink(bridge, mem, "agent-a")
    await sink(_Proposal(), _Outcome(
        observations={"verified": True, "goal_id": 5, "objective": "map the Acme API",
                      "status": "done"},
        actual_outcome="found the OpenAPI spec and catalogued 40 endpoints"))
    assert len(mem.checkpoints) == 1
    la = mem.checkpoints[0]["last_action"]
    assert la.startswith("pursued: map the Acme API")
    assert "OpenAPI" in la


@pytest.mark.asyncio
async def test_unverified_pursuit_records_attempt():
    bridge, mem = _Bridge(), _Mem()
    sink = build_experiential_sink(bridge, mem, "agent-a")
    await sink(_Proposal(), _Outcome(
        observations={"verified": False, "goal_id": 6, "objective": "find X", "status": "max_steps"},
        actual_outcome=""))
    assert mem.checkpoints[0]["last_action"] == "attempted: find X"


@pytest.mark.asyncio
async def test_infra_outcome_records_nothing():
    """An infra no-op (actuator never ran) must not pollute the checkpoint — the goal is
    retried, nothing was actually done."""
    bridge, mem = _Bridge(), _Mem()
    sink = build_experiential_sink(bridge, mem, "agent-a")
    await sink(_Proposal(), _Outcome(observations={"infra": True}))
    assert mem.checkpoints == []
    assert mem.journal.added == []
