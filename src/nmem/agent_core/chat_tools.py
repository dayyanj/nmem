"""Give a *conversation* hands — the chat-path counterpart to the ``/act`` executor.

agent_core already has a drop-in, gated, learning tool loop for autonomous action
(``actors.assemble_registry`` → ``build_executor`` → nmem-act ``ToolCallingExecutor``,
wired to ``AgentRuntime._runner``). But that path is goal/verdict-shaped: its selector
prompts the model to conclude with a ``SUCCESS:``/``FAILURE:`` marker, which is wrong for a
conversation, where we want the agent's natural reply. This module is the small,
conversation-shaped loop that **reuses the same registry + autonomy gate + backend**, so
chat and pursuit honour ONE autonomy policy — a chat turn can look things up (the floor's
C4, "if you have a way to look it up, do so") without forking the gate or the learning loop.

Three pieces:
  * ``memory_search_action(mem, agent_id)`` — an always-on READ_ONLY built-in so every agent
    can search its own tiers even when the operator configured no ``actors:`` block. This
    lives in agent_core (not nmem-act's ``default_registry``) because it reads nmem tiers —
    nmem-act stays dependency-free.
  * ``build_chat_registry(runtime)`` — the effective chat toolset: ``memory_search`` +
    whatever the host dropped in (``runtime.tools``), with the runtime's autonomy gate.
  * ``stream_tool_loop(...)`` — one bounded, gated loop yielding ``('delta', str)`` /
    ``('progress', dict)`` events. ``run_chat_tools`` drains it to a string (the blocking
    ``converse`` path); ``chat.converse_stream`` iterates it (the streaming/voice path). The
    final round forces a tool-less answer, so it can never loop without replying.

Fail-open + flag-gated at the call site (``NMEM_CHAT_TOOLS_ENABLED``): with the flag off,
``converse``/``converse_stream`` never run any of this and are byte-identical to the tool-less
turn.

**Deliberate boundary — chat tool calls are NOT recorded through the experiential outcome sink**
(episode + procedure reward + finding memory) that ``actors.build_executor`` wires for the
``/act`` pursuit path. A conversational look-up is not a goal pursuit: recording every chat
``memory_search`` (or a host actor invoked mid-chat) as a pursuit episode would flood the
action-learning history with interactive noise and mis-attribute drive discharge. Chat tool
outcomes are gated + verified + gate-checked (safety is identical to ``/act``) but intentionally
do not feed the learning loop. If chat-initiated *actions* should later contribute to learning,
that is a follow-up (route through the shared sink with a chat-sourced episode kind), not a
silent default. See docs/proposals/nmem-agent-core-baseline-prompt-floor.md.
"""
from __future__ import annotations

import json
import logging
import os

log = logging.getLogger(__name__)

# Bounded so a turn can never loop forever; the final round force-answers (tools off).
CHAT_MAX_TOOL_ROUNDS = int(os.environ.get("NMEM_CHAT_TOOL_ROUNDS", "4") or "4")
_MAX_RESULT_CHARS = 4000
# The composite action_type the /act executor gates the whole tool loop under — matched here so
# one autonomy deny governs both the action loop and the chat loop.
_CHAT_LOOP_ACTION = "llm_tool_call"


def chat_tools_enabled() -> bool:
    """Whether the tool-calling chat turn is on (default OFF — a landing, per the
    graduation-promotion rule; flip to default-on once proven on michelle)."""
    return (os.environ.get("NMEM_CHAT_TOOLS_ENABLED") or "").strip().lower() not in \
        ("", "0", "false", "no", "off")


def memory_search_action(mem, agent_id: str):
    """A READ_ONLY nmem-act ``Action`` that searches the agent's own memory across all tiers.
    Always offered on a chat turn (C4's hands) regardless of the ``actors:`` config."""
    from nmem_act.registry import Action, ActionResult
    from nmem_act.types import CapabilityClass

    async def handler(params: dict) -> "ActionResult":
        query = str((params or {}).get("query") or "").strip()
        if not query:
            return ActionResult(success=False, outcome="memory_search needs a 'query'")
        try:
            top_k = int((params or {}).get("top_k") or 6)
        except (TypeError, ValueError):
            top_k = 6
        try:
            # bump_access=False: a read-only look-up should not perturb salience/recency.
            results = await mem.search(agent_id, query, top_k=top_k, bump_access=False)
        except Exception as e:  # noqa: BLE001 — a failed look-up must not sink the turn
            log.warning("[chat_tools] memory_search failed: %s", e, exc_info=True)
            return ActionResult(success=False, outcome=f"memory search failed: {e}")
        # Prefer the query-matching passage (search already located it) over a blind prefix, so a
        # long memory whose relevant text sits past char 400 isn't misrepresented — mirrors the MCP
        # search renderer. Fall back to the content head when there's no passage.
        items = [{"tier": r.tier, "score": round(r.score, 3),
                  "title": (r.title or r.key or ""),
                  "content": ((getattr(r, "passage", None) or r.content or "")[:400])}
                 for r in results]
        return ActionResult(
            success=True,
            outcome=(f"{len(items)} memories for '{query[:60]}'" if items
                     else f"no memories found for '{query[:60]}'"),
            observations={"results": items}, info_gain=float(bool(items)))

    return Action(
        name="memory_search", handler=handler, capability_class=CapabilityClass.READ_ONLY,
        description=("Search your own memory across all tiers (journal, long-term, shared, "
                     "entity, policy) for something you don't already have in front of you. "
                     "Use this before answering from guesswork."),
        parameters={"type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "What to look up."},
                        "top_k": {"type": "integer",
                                  "description": "Max results to return (default 6)."}},
                    "required": ["query"]})


def build_chat_registry(runtime):
    """The chat toolset + the gate to enforce on it. ``memory_search`` is always present;
    any host-dropped tools (``runtime.tools``, an ``ActionRegistry``) are exposed too and
    the gate decides what actually runs — so under the default ``read_only`` autonomy a
    chat turn can search/read but a write tool is blocked with a policy message, exactly as
    on the ``/act`` path. Returns ``(registry, gate)``."""
    from nmem_act.autonomy import AutonomyGate
    from nmem_act.registry import ActionRegistry

    reg = ActionRegistry()
    if getattr(runtime, "mem", None) is not None:
        reg.register(memory_search_action(runtime.mem, runtime.agent_id))
    host_reg = getattr(runtime, "tools", None)          # host-assembled actors: registry
    if host_reg is not None:
        for name in host_reg.names():
            action = host_reg.get(name)
            if action is not None:
                reg.register(action)
    gate = getattr(runtime, "gate", None) or AutonomyGate()   # default: read_only
    return reg, gate


def _supports_tool_loop(backend) -> bool:
    """Whether ``backend`` can round-trip a multi-round OpenAI tool history (assistant with
    ``tool_calls`` + ``role:tool`` results). Default False for an unknown custom backend —
    conservative: it falls back to a plain reply rather than sending it a history it may not
    parse. See ``backend.openai_tool_history``."""
    return bool(getattr(backend, "openai_tool_history", False))


def _action_to_openai(action) -> dict:
    return {"type": "function",
            "function": {"name": action.name, "description": action.description or "",
                         "parameters": action.parameters or {"type": "object", "properties": {}}}}


async def _dispatch(gate, action, name: str, arguments) -> dict:
    """Gate-check then execute one tool call. Never raises — returns a dict the model can
    read and recover from (an ``{'error': …}`` on block/failure)."""
    if action is None:
        return {"error": f"tool '{name}' is not available"}
    from nmem_act.types import ActionProposal
    decision = gate.check(ActionProposal(action_type=name),
                          capability_class=action.capability_class)
    if not decision.allowed:
        return {"error": f"not permitted right now: {decision.reason}"}
    # Fail CLOSED on approval: a tiered-allowlisted high-risk tool is permitted only WITH an
    # approver, and the chat path wires none. The /act ReferenceRunner blocks it too — chat
    # must not be a softer door. Never execute an approval-required tool here.
    if getattr(decision, "requires_approval", False):
        return {"error": f"'{name}' needs human approval before running — "
                         f"not available in a chat turn; say what you'd do instead"}
    args = arguments if isinstance(arguments, dict) else {}
    try:
        result = await action.handler(args)
    except Exception as e:  # noqa: BLE001 — a tool failure must not sink the reply
        log.warning("[chat_tools] tool %s failed: %s", name, e, exc_info=True)
        return {"error": str(e)[:200]}
    # Honor an attached outcome Verifier, exactly as the /act runner does — a handler must not
    # self-certify success in chat when it cannot on the action path (e.g. a research action that
    # returns an insubstantial finding). Fail CLOSED: any verifier error → not verified.
    verifier = getattr(action, "verifier", None)
    if verifier is not None:
        obs = result.observations or {}
        objective = args.get("objective") or obs.get("objective") or ""
        try:
            verdict = await verifier.verify(objective, result.outcome or "", obs.get("status", ""))
            verified, reason = bool(verdict.verified), verdict.reason
        except Exception as e:  # noqa: BLE001
            log.warning("[chat_tools] verifier raised for %s (→ not verified)", name, exc_info=True)
            verified, reason = False, f"verifier error: {e}"
    verify_reason = None
    if verifier is not None and not verified:
        result.success = False
        verify_reason = reason
    # Explicit, SHORT status the model (and truncation) can rely on: `ok` + a bounded `error`
    # reason — kept separate from the possibly-huge `outcome`/`observations` so a verifier
    # rejection can never be truncated away into apparent success.
    out: dict = {"ok": bool(result.success), "outcome": result.outcome}
    if result.observations:
        out["observations"] = result.observations
    if not result.success:
        out["error"] = str(verify_reason or result.outcome or "tool did not succeed")[:300]
    return out


def _tool_result_content(result: dict) -> str:
    """Serialize a tool result for the model within ``_MAX_RESULT_CHARS``. The common (fits) case
    returns the natural dict unchanged. When over budget, rebuild with the SHORT status FIRST
    (``ok`` + bounded ``error``) — which must never be truncated away, or a verifier-rejected
    result would read as success — then a bounded ``outcome`` and as much observation PREFIX as
    still fits. Never discards all retrieved data."""
    payload = json.dumps(result, default=str)
    if len(payload) <= _MAX_RESULT_CHARS:
        return payload
    out: dict = {}
    if result.get("ok") is not None:
        out["ok"] = result["ok"]
    if result.get("error") is not None:
        out["error"] = str(result["error"])[:300]           # short failure reason, always kept
    reserve = len(json.dumps(out, default=str))
    budget = max(0, _MAX_RESULT_CHARS - reserve - 96)        # room for outcome + obs wrappers
    outcome = str(result.get("outcome") or "")
    out["outcome"] = outcome[:min(len(outcome), max(200, budget // 2))]
    obs = result.get("observations")
    if obs is not None:
        room = _MAX_RESULT_CHARS - len(json.dumps(out, default=str)) - 48
        if room > 0:
            out["observations_prefix"] = json.dumps(obs, default=str)[:room]
    return json.dumps(out, default=str)[:_MAX_RESULT_CHARS]   # hard cap trims obs tail; ok/error kept


async def stream_tool_loop(runtime, messages: list[dict], *, registry, gate,
                           max_rounds: int = CHAT_MAX_TOOL_ROUNDS, **chat_kw):
    """One bounded, gated conversational tool loop over ``messages`` (mutated in place with
    the assistant/tool turns). Yields three event kinds:
      * ``('answer', str)``   — a CONCLUDING reply (the model finished, or the forced tool-less
                                round). A turn has really answered iff an ``answer`` is emitted.
      * ``('delta', str)``    — a mid-loop preamble ("let me check…") streamed alongside a tool
                                call; NOT a finished answer on its own.
      * ``('progress', {'tool','phase'})`` — a tool is being called (keep-alive).
    The final round forces a tool-less answer, so the loop always terminates.

    Reuses the agent's own backend: ``chat_with_tools`` for tool rounds, ``chat`` for the
    forced final answer (guaranteed text, no empty-``tools`` edge cases)."""
    tool_defs = [_action_to_openai(registry.get(n)) for n in registry.names()]
    # usage_sink is an eval-only kwarg on backend.chat and is NOT accepted by chat_with_tools;
    # the tool path is production-only, so drop it defensively before the tool-round calls.
    tool_kw = {k: v for k, v in chat_kw.items() if k != "usage_sink"}
    supported = _supports_tool_loop(runtime.backend)

    # Composite policy: the /act executor wraps the whole LLM tool loop in one `llm_tool_call`
    # proposal and gates THAT first — so an operator who denies `llm_tool_call` disables
    # LLM-driven tool loops wholesale. A chat turn IS such a loop; honor the same deny (else
    # chat would be a softer door than /act). Blocked → a plain, tool-less reply.
    if supported and tool_defs:
        from nmem_act.types import ActionProposal, CapabilityClass
        composite = gate.check(ActionProposal(action_type=_CHAT_LOOP_ACTION,
                                              capability_class=CapabilityClass.READ_ONLY))
        if not composite.allowed:
            log.debug("[chat_tools] chat tool loop disabled by policy: %s", composite.reason)
            supported = False

    for round_i in range(max_rounds + 1):
        # Forced tool-less answer: last round, no tools, or a backend that can't round-trip
        # tool history. Guaranteed to produce a reply (no empty-`tools` edge cases).
        if round_i == max_rounds or not tool_defs or not supported:
            text = await runtime.backend.chat(messages, **chat_kw)
            if text:
                yield ("answer", text)
            return
        res = await runtime.backend.chat_with_tools(messages, tool_defs,
                                                    tool_choice="auto", **tool_kw)
        content = res.content or ""
        valid = [tc for tc in (res.tool_calls or []) if tc.id and tc.name]
        if not valid:
            # The model concluded. If it actually said something, that's the answer. If it
            # produced NEITHER content NOR a tool call (e.g. Qwen burned the budget on thinking),
            # force one tool-less answer so the caller always gets a real reply.
            if content.strip():
                yield ("answer", content)
                return
            text = await runtime.backend.chat(messages, **chat_kw)
            if text:
                yield ("answer", text)
            return
        if content:
            yield ("delta", content)         # a short 'let me check…' preamble alongside a call
        messages.append({
            "role": "assistant", "content": res.content or None,
            "tool_calls": [{"id": tc.id, "type": "function",
                            "function": {"name": tc.name,
                                         "arguments": json.dumps(tc.arguments)}} for tc in valid]})
        for tc in valid:
            yield ("progress", {"tool": tc.name, "phase": "start"})
            result = await _dispatch(gate, registry.get(tc.name), tc.name, tc.arguments)
            # Observability: the tool loop is otherwise silent on success. One info line per
            # chat-initiated tool call makes it visible in the journal that a conversation
            # reached for a tool (and whether it landed) — not just when something breaks.
            log.info("[chat_tools] chat tool call: %s → %s", tc.name,
                     "error" if result.get("error") else "ok")
            if result.get("error"):
                yield ("progress", {"tool": tc.name, "phase": "error"})
            messages.append({"role": "tool", "tool_call_id": tc.id,
                             "content": _tool_result_content(result)})
        # loop → next round sees the tool results


async def run_chat_tools(runtime, messages: list[dict], **chat_kw) -> str:
    """Blocking driver for the tool loop — drains ``stream_tool_loop`` into a reply string.
    Falls back to a plain ``backend.chat`` when there are no tools to offer."""
    registry, gate = build_chat_registry(runtime)
    if len(registry) == 0:
        return await runtime.backend.chat(messages, **chat_kw)
    parts: list[str] = []
    answered = False
    async for kind, data in stream_tool_loop(runtime, messages, registry=registry,
                                             gate=gate, **chat_kw):
        if kind == "answer":
            answered = True
            parts.append(data)
        elif kind == "delta":                # a preamble — part of the reply, but NOT an answer
            parts.append(data)
    # Never return accumulated preambles as if they were an answer: if no concluding answer was
    # produced (forced tool-less round came back empty), recover with a plain reply or apology —
    # the same guarantee converse_stream makes.
    if not answered:
        try:
            fb = await runtime.backend.chat(messages, **chat_kw)
        except Exception:  # noqa: BLE001
            log.warning("[chat_tools] run_chat_tools recovery reply failed", exc_info=True)
            fb = ""
        if not fb:
            fb = "Sorry — I couldn't put an answer together just now. Could you say it again?"
        parts.append(fb)
    return "".join(parts).strip()
