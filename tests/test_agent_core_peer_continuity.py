"""Continuity graduated into the peer + comms seams.

PeerExchange (a reasoning turn) reads the living wake snapshot and advances the checkpoint;
CommsLoop (a proactive action) records an action checkpoint on delivery. Pure unit tests over
fakes — no Redis bus, no Postgres — exercising the seam wiring + fail-open contract.
"""
from __future__ import annotations

import pytest

from nmem.agent_core.peer import PeerExchange


class _Wake:
    def __init__(self, content):
        self.content = content


class _Journal:
    def __init__(self):
        self.added = []

    async def add(self, **kw):
        self.added.append(kw)


class FakeMem:
    def __init__(self, wake_content="### Picking up from\n- Last interaction: prior round"):
        self._wake_content = wake_content
        self.journal = _Journal()
        self.wake_calls = []
        self.checkpoints = []

    async def wake(self, agent_id, *, query=None, max_tokens=1200):
        self.wake_calls.append({"agent_id": agent_id, "query": query})
        return _Wake(self._wake_content)

    async def save_continuity_checkpoint(self, agent_id, *, last_interaction_summary=None,
                                         last_action=None, **kw):
        self.checkpoints.append({"last_interaction_summary": last_interaction_summary,
                                 "last_action": last_action})


def _meta(kind="challenge", channel="dm:agent-a:agent-b", sender="agent-b"):
    return {"from": sender, "channel": channel, "kind": kind, "msg_id": "m1"}


# ── _accepts_continuity arity detection ─────────────────────────────────────────


def test_accepts_continuity_detects_arity():
    async def two(sender, text): ...
    async def three(sender, text, continuity=""): ...
    async def varargs(*a): ...
    assert PeerExchange._accepts_continuity(two) is False
    assert PeerExchange._accepts_continuity(three) is True
    assert PeerExchange._accepts_continuity(varargs) is True
    assert PeerExchange._accepts_continuity(object()) is False   # not introspectable → safe False


# ── PeerExchange._handle: read continuity + write checkpoint ────────────────────


@pytest.mark.asyncio
async def test_peer_turn_injects_continuity_and_checkpoints():
    seen = {}

    async def on_challenge(sender, text, continuity=""):
        seen["continuity"] = continuity
        return "my considered reply"

    mem = FakeMem()
    px = PeerExchange({}, mem=mem, agent_id="agent-a", on_challenge=on_challenge)
    await px._handle(_meta(), b"do you agree that X?")

    # read: the wake snapshot reached the handler, keyed on the challenge text
    assert "Picking up from" in seen["continuity"]
    assert mem.wake_calls and mem.wake_calls[0]["query"] == "do you agree that X?"
    # write: the peer turn advanced the checkpoint
    assert len(mem.checkpoints) == 1
    assert "do you agree that X?" in mem.checkpoints[0]["last_interaction_summary"]
    assert "my considered reply" in mem.checkpoints[0]["last_interaction_summary"]


@pytest.mark.asyncio
async def test_peer_two_arg_handler_unchanged_and_still_checkpoints():
    """A legacy 2-arg handler must keep working (no continuity arg passed), but the turn is
    still checkpointed — the write path doesn't depend on the handler's arity."""
    async def on_challenge(sender, text):
        return "legacy reply"

    mem = FakeMem()
    px = PeerExchange({}, mem=mem, agent_id="agent-a", on_challenge=on_challenge)
    await px._handle(_meta(), b"a claim")

    assert mem.wake_calls == []              # 2-arg handler → continuity not fetched/passed
    assert len(mem.checkpoints) == 1         # ...but the turn is still recorded
    assert "legacy reply" in mem.checkpoints[0]["last_interaction_summary"]


@pytest.mark.asyncio
async def test_peer_continuity_can_be_disabled():
    async def on_challenge(sender, text, continuity=""):
        return "reply"

    mem = FakeMem()
    px = PeerExchange({}, mem=mem, agent_id="agent-a", on_challenge=on_challenge,
                      continuity=False)
    await px._handle(_meta(), b"a claim")
    assert mem.wake_calls == []
    assert mem.checkpoints == []


@pytest.mark.asyncio
async def test_peer_ephemeral_channel_does_not_checkpoint():
    """Ephemeral (test/dev) channels respond but are not persisted — and must not pollute
    the durable checkpoint either."""
    async def on_challenge(sender, text, continuity=""):
        return "reply"

    mem = FakeMem()
    px = PeerExchange({}, mem=mem, agent_id="agent-a", on_challenge=on_challenge)
    await px._handle(_meta(channel="test:scratch"), b"a claim")
    assert mem.checkpoints == []             # ephemeral → no durable write
    assert mem.journal.added == []           # ...and nothing journalled
    # ...and NO query-driven wake — wake(query=) runs search() whose entity auto-journaling
    # would persist activity, breaking the ephemeral no-persistence guarantee (codex P2).
    assert mem.wake_calls == []


# ── CommsLoop: action checkpoint on delivery ────────────────────────────────────


@pytest.mark.asyncio
async def test_comms_delivery_records_action_checkpoint():
    from nmem.agent_core.comms import CommsLoop, Utterance

    class _Sink:
        async def deliver(self, utt):
            return True

    mem = FakeMem()

    loop = CommsLoop(bridge=None, mem=mem, backend=None, sink=_Sink(), agent_id="agent-a")

    # stub the pending-utterance selection + delivered-mark (DB-backed) out
    async def _pending():
        return {"id": 7, "source": "drive:novelty", "action_type": "communicate",
                "text": "I noticed the schema drift you mentioned is now resolved",
                "valence": 0.2, "relevance": 0.9, "addressee": "agent-b"}

    async def _mark(_id):
        return None

    loop._select_pending = _pending
    loop._mark_delivered = _mark

    class _Intent:
        action = "communicate"

    await loop.on_intent(_Intent())

    assert len(mem.checkpoints) == 1
    la = mem.checkpoints[0]["last_action"]
    assert "reached out to agent-b" in la
    assert "schema drift" in la
