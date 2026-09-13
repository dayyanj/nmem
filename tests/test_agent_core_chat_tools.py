"""Tests for agent_core.chat_tools — the tool-calling chat turn (pieces 1/2/3).

Pure unit tests over fakes (no Postgres, no real backend). Skipped where the optional
nmem-act package isn't importable, since the chat tool loop reuses its registry + gate.
Covers the paths a prior codex review flagged as untested: tool execution + result
feed-back, the forced-answer fallbacks (empty completion / unsupported backend), the
autonomy gate (read-only vs approval-required), and streaming progress/delta events.
"""
import pytest

pytest.importorskip("nmem_act")

from types import SimpleNamespace  # noqa: E402

from nmem.agent_core import chat_tools  # noqa: E402
from nmem.agent_core.backend import ToolCall  # noqa: E402
from nmem_act.autonomy import AutonomyGate, AutonomyLevel  # noqa: E402
from nmem_act.registry import Action, ActionResult  # noqa: E402
from nmem_act.types import CapabilityClass  # noqa: E402


class _Res:
    """A fake ChatResult from backend.chat_with_tools."""
    def __init__(self, content="", tool_calls=None):
        self.content, self.tool_calls, self.finish_reason = content, tool_calls or [], None


class _Backend:
    openai_tool_history = True

    def __init__(self, script):
        self._script, self._i, self.plain_calls = list(script), 0, 0

    async def chat_with_tools(self, messages, tools, *, tool_choice="auto", **kw):
        r = self._script[self._i]
        self._i += 1
        return r

    async def chat(self, messages, **kw):
        self.plain_calls += 1
        return "forced answer"


class _SearchRes:
    def __init__(self):
        self.tier, self.score, self.title, self.key = "policy", 0.9, "Outreach pause", None
        self.content = "Outreach is paused pending review."


class _Mem:
    async def search(self, agent_id, query, *, top_k=6, bump_access=True):
        assert bump_access is False        # a read-only look-up must not perturb salience
        return [_SearchRes()]


def _rt(backend, *, mem=None, tools=None, gate=None):
    return SimpleNamespace(
        mem=mem, agent_id="m", backend=backend, tools=tools, gate=gate,
        _persona=SimpleNamespace(agent_id="m", capabilities="", objectives=[], world_entities=""))


def test_memory_search_is_an_always_on_readonly_builtin():
    reg, gate = chat_tools.build_chat_registry(_rt(None, mem=_Mem()))
    assert "memory_search" in reg.names()
    assert reg.get("memory_search").capability_class == CapabilityClass.READ_ONLY


@pytest.mark.asyncio
async def test_blocking_loop_calls_tool_then_answers_grounded():
    b = _Backend([_Res("", [ToolCall(id="c1", name="memory_search", arguments={"query": "outreach"})]),
                  _Res("Outreach is paused.")])
    messages = [{"role": "user", "content": "is outreach paused?"}]
    reply = await chat_tools.run_chat_tools(_rt(b, mem=_Mem()), messages, temperature=0.4, max_tokens=200)
    assert "paused" in reply.lower()
    tool_msgs = [m for m in messages if m.get("role") == "tool"]
    assert tool_msgs and "Outreach is paused" in tool_msgs[0]["content"]   # result fed back


@pytest.mark.asyncio
async def test_empty_completion_forces_a_reply():
    """Model returns neither content nor a tool call (e.g. thinking-budget burn) → force one
    tool-less answer rather than returning an empty turn."""
    b = _Backend([_Res("", [])])
    reply = await chat_tools.run_chat_tools(_rt(b, mem=_Mem()), [{"role": "user", "content": "hi"}])
    assert reply == "forced answer" and b.plain_calls == 1


@pytest.mark.asyncio
async def test_unsupported_backend_falls_back_to_plain_chat():
    class _NoToolHistory:
        openai_tool_history = False

        def __init__(self):
            self.cwt = 0

        async def chat_with_tools(self, *a, **k):
            self.cwt += 1
            return _Res("should not be used")

        async def chat(self, *a, **k):
            return "plain only"

    nb = _NoToolHistory()
    reply = await chat_tools.run_chat_tools(_rt(nb, mem=_Mem()), [{"role": "user", "content": "hi"}])
    assert reply == "plain only" and nb.cwt == 0    # tool loop never engaged


@pytest.mark.asyncio
async def test_gate_blocks_write_tool_under_default_readonly():
    _, gate = chat_tools.build_chat_registry(_rt(None, mem=_Mem()))   # default read_only

    async def _send(_):
        return ActionResult(success=True, outcome="SENT")

    write = Action(name="send", handler=_send, capability_class=CapabilityClass.HIGH_RISK, description="x")
    res = await chat_tools._dispatch(gate, write, "send", {})
    assert "read_only" in res.get("error", "")


@pytest.mark.asyncio
async def test_gate_refuses_approval_required_tool():
    """A tiered-allowlisted high-risk tool needs an approver; the chat path wires none → refuse."""
    gate = AutonomyGate(level=AutonomyLevel.TIERED, allow={"deploy"})

    async def _deploy(_):
        return ActionResult(success=True, outcome="DEPLOYED")

    hi = Action(name="deploy", handler=_deploy, capability_class=CapabilityClass.HIGH_RISK, description="x")
    res = await chat_tools._dispatch(gate, hi, "deploy", {})
    assert "approval" in res.get("error", "")


@pytest.mark.asyncio
async def test_configured_gate_deny_binds_the_builtin_tool():
    """An operator's tiered deny of memory_search must bind on the chat path too."""
    gate = AutonomyGate(level=AutonomyLevel.TIERED, deny={"memory_search"})
    reg, used_gate = chat_tools.build_chat_registry(_rt(None, mem=_Mem(), gate=gate))
    res = await chat_tools._dispatch(used_gate, reg.get("memory_search"), "memory_search", {"query": "x"})
    assert "denylisted" in res.get("error", "")


@pytest.mark.asyncio
async def test_attached_verifier_is_enforced():
    """A handler that self-reports success is overridden to failure by an attached verifier —
    chat must not be a softer door than the /act runner."""
    async def _handler(_):
        return ActionResult(success=True, outcome="did a thing")

    class _Verdict:
        verified, reason = False, "insubstantial finding"

    class _Verifier:
        async def verify(self, objective, finding, status):
            return _Verdict()

    action = Action(name="research", handler=_handler, capability_class=CapabilityClass.READ_ONLY,
                    description="x", verifier=_Verifier())
    res = await chat_tools._dispatch(AutonomyGate(), action, "research", {"objective": "find X"})
    assert res.get("ok") is False and "insubstantial finding" in res.get("error", "")


@pytest.mark.asyncio
async def test_composite_llm_tool_call_deny_disables_the_chat_loop():
    """Denying the composite `llm_tool_call` (as on /act) disables the chat tool loop wholesale —
    chat must not be a softer door: it falls back to a plain reply, never touching a tool."""
    gate = AutonomyGate(level=AutonomyLevel.TIERED, deny={"llm_tool_call"})
    b = _Backend([_Res("", [ToolCall(id="c1", name="memory_search", arguments={"query": "x"})])])
    rt = _rt(b, mem=_Mem(), gate=gate)
    reply = await chat_tools.run_chat_tools(rt, [{"role": "user", "content": "hi"}])
    assert reply == "forced answer" and b._i == 0    # chat_with_tools never engaged


@pytest.mark.asyncio
async def test_blocking_driver_recovers_when_no_answer_produced():
    """A preamble streamed, then the forced tool-less answer came back empty → don't return the
    preamble as the reply; recover with an apology (parity with converse_stream)."""
    class _EmptyForced:
        openai_tool_history = True

        def __init__(self):
            self._script = [_Res("let me check.", [ToolCall(id="c1", name="memory_search", arguments={"query": "x"})]),
                            _Res("", [])]
            self._i = 0

        async def chat_with_tools(self, messages, tools, *, tool_choice="auto", **kw):
            r = self._script[self._i]
            self._i += 1
            return r

        async def chat(self, messages, **kw):
            return ""                                    # forced answer + recovery both empty

    reply = await chat_tools.run_chat_tools(_rt(_EmptyForced(), mem=_Mem()),
                                            [{"role": "user", "content": "hi"}])
    assert "couldn't put an answer together" in reply    # not just the "let me check." preamble


@pytest.mark.asyncio
async def test_converse_stream_respects_flag_off(monkeypatch):
    """With the rollout flag off, converse_stream streams a plain reply and never touches tools."""
    monkeypatch.delenv("NMEM_CHAT_TOOLS_ENABLED", raising=False)
    from nmem.agent_core import chat as chatmod

    class _B:
        openai_tool_history = True

        async def chat(self, messages, **kw):
            return "plain grounded reply"

        async def chat_with_tools(self, *a, **k):
            raise AssertionError("tools must not run when the flag is off")

    class _M:
        async def search(self, *a, **k):
            return []

    events = [e async for e in chatmod.converse_stream(_rt(_B(), mem=_M()), "hi", continuity=False)]
    assert {"delta": "plain grounded reply"} in events and events[-1] == {"done": True}


def test_tool_result_keeps_error_when_observations_are_huge():
    huge = {"ok": False, "error": "not permitted right now: denylisted",
            "outcome": "blocked", "observations": {"blob": "x" * 10000}}
    content = chat_tools._tool_result_content(huge)
    assert len(content) <= chat_tools._MAX_RESULT_CHARS
    # the failure signal survives truncation; some observation data is retained (not all dropped)
    assert "not permitted" in content and '"ok": false' in content
    assert "observations_prefix" in content


@pytest.mark.asyncio
async def test_memory_search_prefers_matching_passage():
    class _PassageRes:
        tier, score, title, key = "ltm", 0.8, "long note", None
        content = "PREFIX " + ("filler " * 100) + "THE RELEVANT BIT"
        passage = "THE RELEVANT BIT"

    class _PMem:
        async def search(self, agent_id, query, *, top_k=6, bump_access=True):
            return [_PassageRes()]

    action = chat_tools.memory_search_action(_PMem(), "m")
    res = await action.handler({"query": "relevant"})
    assert res.observations["results"][0]["content"] == "THE RELEVANT BIT"


@pytest.mark.asyncio
async def test_streaming_emits_progress_then_delta():
    b = _Backend([_Res("", [ToolCall(id="c1", name="memory_search", arguments={"query": "x"})]),
                  _Res("Outreach is paused.")])
    reg, gate = chat_tools.build_chat_registry(_rt(b, mem=_Mem()))
    events = [e async for e in chat_tools.stream_tool_loop(
        _rt(b, mem=_Mem()), [{"role": "user", "content": "paused?"}],
        registry=reg, gate=gate, temperature=0.4, max_tokens=200)]
    assert ("progress", {"tool": "memory_search", "phase": "start"}) in events
    # the concluding reply is tagged 'answer' (a preamble would be 'delta')
    assert any(kind == "answer" and "paused" in data.lower() for kind, data in events)


def test_huge_result_retains_observation_prefix_not_just_status():
    huge = {"outcome": "HTTP 200", "observations": {"records": [{"i": i} for i in range(400)]}}
    content = chat_tools._tool_result_content(huge)
    assert len(content) <= chat_tools._MAX_RESULT_CHARS
    assert "HTTP 200" in content and "observations_prefix" in content   # data retained, not dropped
