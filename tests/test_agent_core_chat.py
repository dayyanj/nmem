"""Tests for agent_core.chat — the generic grounded-conversation turn, and specifically
the continuity seam it consumes: injecting the living wake snapshot every turn (the read)
and advancing the checkpoint after every turn (the write).

These are pure unit tests over fakes — no Postgres, no backend — exercising that converse()
wires continuity in/out correctly and fail-opens when the pieces are absent or broken.
"""
from dataclasses import dataclass

import pytest

from nmem.agent_core import chat


@dataclass
class _Persona:
    agent_id: str = "michelle"
    capabilities: str = ""
    objectives: tuple = ()
    world_entities: str = ""


class _WakeResult:
    def __init__(self, content):
        self.content = content


class FakeMem:
    """Records wake() calls and checkpoint writes; build_context's prompt.build is stubbed
    to empty so the test isolates the continuity seam."""

    def __init__(self, *, wake_content="### Picking up from\n- Last action: shipped the seam",
                 wake_raises=False, has_checkpoint=True):
        self._wake_content = wake_content
        self._wake_raises = wake_raises
        self.wake_calls = []
        self.checkpoints = []
        self._has_checkpoint = has_checkpoint

    # build_context path — stubbed to return nothing so tests see only continuity.
    class _Prompt:
        async def build(self, **kw):
            class _Ctx:
                full_injection = ""
            return _Ctx()

    prompt = _Prompt()

    async def wake(self, agent_id, *, query=None, max_tokens=1200):
        self.wake_calls.append({"agent_id": agent_id, "query": query, "max_tokens": max_tokens})
        if self._wake_raises:
            raise RuntimeError("provider exploded")
        return _WakeResult(self._wake_content)

    async def save_continuity_checkpoint(self, agent_id, *, last_interaction_summary=None,
                                         last_action=None, interrupted_work=None,
                                         expected_next_action=None):
        if not self._has_checkpoint:
            raise AttributeError("save_continuity_checkpoint")
        self.checkpoints.append({
            "agent_id": agent_id,
            "last_interaction_summary": last_interaction_summary,
            "last_action": last_action,
        })


class FakeBackend:
    def __init__(self, reply="the reply"):
        self.reply = reply
        self.last_messages = None

    async def chat(self, messages, *, temperature=0.4, max_tokens=700):
        self.last_messages = messages
        return self.reply


class FakeRuntime:
    def __init__(self, mem, backend, persona=None):
        self.mem = mem
        self.backend = backend
        self._persona = persona or _Persona()

    @property
    def agent_id(self):
        return self._persona.agent_id


# ── continuity_block ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_continuity_block_renders_wake_content():
    mem = FakeMem(wake_content="### Picking up from\n- Last action: X")
    block = await chat.continuity_block(mem, "michelle", query="hi", max_tokens=900)
    assert "Picking up from" in block
    assert mem.wake_calls == [{"agent_id": "michelle", "query": "hi", "max_tokens": 900}]


@pytest.mark.asyncio
async def test_continuity_block_fail_open_on_wake_error():
    mem = FakeMem(wake_raises=True)
    assert await chat.continuity_block(mem, "michelle") == ""


@pytest.mark.asyncio
async def test_continuity_block_noop_without_wake():
    assert await chat.continuity_block(object(), "michelle") == ""
    assert await chat.continuity_block(None, "michelle") == ""


# ── record_turn_checkpoint ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_record_turn_checkpoint_writes_summary():
    mem = FakeMem()
    await chat.record_turn_checkpoint(mem, "michelle", "what's the plan?", "ship it")
    assert len(mem.checkpoints) == 1
    ck = mem.checkpoints[0]
    assert ck["agent_id"] == "michelle"
    assert "what's the plan?" in ck["last_interaction_summary"]
    assert "ship it" in ck["last_interaction_summary"]
    # No filler last_action — it would compete for the render lane ahead of the summary.
    assert ck["last_action"] is None


def test_turn_checkpoint_summary_survives_wake_render_at_default_budget():
    """Round-trip (codex P2): a max-length turn summary must actually appear in the rendered
    wake snapshot at the default continuity budget, not be dropped by the immediate lane's
    budget. Reproduces the 240+240 regression: with a filler last_action + a 502-char summary
    the summary was displaced; the compact, action-free summary survives whole."""
    from datetime import datetime, timezone

    from nmem.continuity import assemble_continuity

    msg, reply = "m" * 400, "r" * 400          # both well over the 140 cap
    summary = f"Asked: {msg.strip()[:140]} — I answered: {reply.strip()[:140]}"
    # codex P2 (deeper): a STALE long last_action from an earlier (partial-upsert) writer must
    # not displace the freshly-written interaction summary. Both must render a preview — fair
    # sharing computed from the ACTUAL body budget (header/separators deducted), not the soft
    # ceiling, so neither 300-char field overflows and drops the other.
    checkpoint = {"last_interaction_summary": summary, "last_action": "x" * 300}

    result = assemble_continuity(
        agent_id="michelle", now=datetime.now(timezone.utc), identity=None,
        recent=[], commitments=[], curiosity=[], policies=[], relevant=[], sym=None,
        max_tokens=1200, k_open_loops=7, checkpoint=checkpoint,   # the converse() default budget
    )
    assert "Picking up from" in result.content
    # The fresh summary renders (its distinctive head survives the fair-share truncation)...
    assert "Asked: " + "m" * 100 in result.content
    # ...alongside a bounded preview of the preserved (stale) action — neither is dropped.
    assert "Last action: " + "x" * 100 in result.content
    assert result.has_checkpoint


def test_short_field_does_not_truncate_a_long_summary_that_fits():
    """codex P2 (water-fill): a SHORT preserved field must not force a needless cut of a
    long interaction summary when the whole checkpoint fits the lane. The summary — incl.
    the 'I answered:' half, the saved reply — must render in full."""
    from datetime import datetime, timezone

    from nmem.continuity import assemble_continuity

    summary = f"Asked: {'q' * 140} — I answered: {'a' * 140}"   # ~300 chars, fits the lane
    checkpoint = {"last_interaction_summary": summary, "last_action": "ran a sandbox probe"}

    result = assemble_continuity(
        agent_id="michelle", now=datetime.now(timezone.utc), identity=None,
        recent=[], commitments=[], curiosity=[], policies=[], relevant=[], sym=None,
        max_tokens=1200, k_open_loops=7, checkpoint=checkpoint,
    )
    # The reply half survives uncut — the short last_action donated its surplus.
    assert "I answered: " + "a" * 140 in result.content
    assert "ran a sandbox probe" in result.content


def test_tiny_budget_still_renders_priority_checkpoint_field():
    """codex P2 (small-budget): when fair shares fall below a meaningful preview, the lane
    must not blank — it falls back to the highest-priority field (the interaction summary,
    rendered first) as a bounded preview, rather than dropping the whole checkpoint."""
    from datetime import datetime, timezone

    from nmem.continuity import assemble_continuity

    checkpoint = {"last_interaction_summary": "A" * 300, "last_action": "B" * 300}
    result = assemble_continuity(
        agent_id="michelle", now=datetime.now(timezone.utc), identity=None,
        recent=[], commitments=[], curiosity=[], policies=[], relevant=[], sym=None,
        max_tokens=140, k_open_loops=7, checkpoint=checkpoint,   # a deliberately tight budget
    )
    assert result.has_checkpoint
    assert "Picking up from" in result.content
    assert "Last interaction: A" in result.content   # priority field survived as a preview


def test_tight_budget_keeps_higher_priority_field_over_stale_lower_one():
    """codex P2 (priority inversion): at a tight budget a long STALE lower-priority field
    (last_action) must not be rendered in place of a higher-priority one (interrupted_work).
    Priority-order fill keeps the higher-priority field and drops the stale long one."""
    from datetime import datetime, timezone

    from nmem.continuity import assemble_continuity

    # interrupted_work ranks above last_action in the "Picking up from" order.
    checkpoint = {"interrupted_work": "review the continuity changes", "last_action": "B" * 300}
    result = assemble_continuity(
        agent_id="michelle", now=datetime.now(timezone.utc), identity=None,
        recent=[], commitments=[], curiosity=[], policies=[], relevant=[], sym=None,
        max_tokens=153, k_open_loops=7, checkpoint=checkpoint,   # codex's reported tight budget
    )
    assert result.has_checkpoint
    # Higher-priority field kept (as a bounded preview); the stale long action did not win.
    assert "Was in the middle of: review the continu" in result.content
    assert "B" * 100 not in result.content


@pytest.mark.asyncio
async def test_record_turn_checkpoint_fail_open_without_support():
    # no save_continuity_checkpoint method → silent no-op, never raises
    await chat.record_turn_checkpoint(object(), "michelle", "q", "a")


# ── converse: reads continuity in, writes checkpoint out ────────────────────────


@pytest.mark.asyncio
async def test_converse_injects_continuity_and_records_checkpoint():
    mem = FakeMem(wake_content="### Picking up from\n- Last action: shipped the seam")
    backend = FakeBackend(reply="here is my answer")
    rt = FakeRuntime(mem, backend)

    reply = await rt_converse(rt, "Morning.")

    assert reply == "here is my answer"
    # read: the wake snapshot is in the system message, under the Continuity header
    system = backend.last_messages[0]["content"]
    assert "# Continuity" in system
    assert "shipped the seam" in system
    # write: a per-turn checkpoint was advanced
    assert len(mem.checkpoints) == 1
    assert "Morning." in mem.checkpoints[0]["last_interaction_summary"]


@pytest.mark.asyncio
async def test_converse_continuity_disabled_skips_both():
    mem = FakeMem()
    backend = FakeBackend()
    rt = FakeRuntime(mem, backend)

    await rt_converse(rt, "hi", continuity=False)

    assert mem.wake_calls == []          # no read
    assert mem.checkpoints == []         # no write
    assert "# Continuity" not in backend.last_messages[0]["content"]


@pytest.mark.asyncio
async def test_converse_survives_broken_continuity():
    mem = FakeMem(wake_raises=True, has_checkpoint=False)
    backend = FakeBackend(reply="still answered")
    rt = FakeRuntime(mem, backend)

    # wake raises AND checkpoint write raises — the turn must still produce a reply
    reply = await rt_converse(rt, "hello")
    assert reply == "still answered"
    assert "# Continuity" not in backend.last_messages[0]["content"]


async def rt_converse(runtime, message, **kw):
    """converse() takes the runtime positionally; thin wrapper for readability."""
    return await chat.converse(runtime, message, **kw)
