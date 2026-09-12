"""Grounded conversation for any nmem agent — the reusable core behind a chat UI.

The **G graduation** from the reference agent: turning "talk to the agent" into three small,
agent-agnostic pieces so a new agent (and the studio's chat page) gets a memory-grounded
conversation for free:

  * ``system_prompt(persona)``   — the agent's identity + standing objectives, from Persona
                                    DATA (not an agent's markdown files).
  * ``build_context(mem, id, q)``— the tiered memory block for a query. This is now a
                                    one-liner over ``mem.prompt.build(...).full_injection``
                                    (nmem already assembles policy/shared/LTM/skills/journal/
                                    recipes with headers), so an agent's hand-rolled version
                                    collapses into the library it always wrapped.
  * ``continuity_block(mem, id)``— the *living* wake snapshot ("where am I right now"):
                                    commitments, open loops, goals, narrative, and the
                                    "picking up from" checkpoint. Query-independent, so it
                                    answers the ``"Morning."`` reorientation case that
                                    query-driven ``build_context`` cannot.
  * ``converse(runtime, msg)``   — assemble [system + continuity + memory] + history + the
                                    user turn, call the agent's own backend, return the reply,
                                    then advance the continuity checkpoint (so the NEXT turn /
                                    a restart picks up where this one left off).

This is where the continuity layer is actually *consumed*: the runtime-wired provider feeds
``mem.wake()``, ``converse`` injects that snapshot every turn (the living read) and writes a
per-turn checkpoint (the living write). Both are fail-open and flag-gated (``continuity=``).

The reference agent's converse (service/identity.build_memory_context + build_system_prompt) is
the reference; this keeps only what is agent-generic (its core_profile/persona files and the
capability-context prefix stay with the host).
"""
from __future__ import annotations

import hashlib as _hashlib
import logging

# The continuity read/write helpers are shared across all turn-taking seams (chat, peer,
# comms) — re-exported here so existing ``chat.continuity_block`` / ``chat.record_turn_checkpoint``
# call sites keep working while the single implementation lives in agent_core.continuity.
from nmem.agent_core.continuity import continuity_block, record_turn_checkpoint  # noqa: F401

log = logging.getLogger(__name__)


def system_prompt(persona) -> str:
    """The agent's base system prompt, derived from its Persona (identity + objectives +
    the entities it reasons about). Deliberately plain: an agent's *voice* is a host concern
    (a host layers its own on top); this is the honest, grounded floor every agent shares."""
    lines = [f"You are {persona.agent_id}, an autonomous agent with a persistent memory."]
    if persona.capabilities:
        lines.append(persona.capabilities.strip())
    objectives = [stmt for _, stmt in (persona.objectives or []) if stmt]
    if objectives:
        lines.append("\nYour standing objectives:")
        lines += [f"- {s}" for s in objectives]
    if persona.world_entities:
        lines.append(f"\nYou reason about: {persona.world_entities}.")
    lines.append("\nGround your answers in the memory provided below. If you do not know "
                 "something or cannot verify it, say so plainly rather than inventing an answer.")
    return "\n".join(lines)


async def build_context(mem, agent_id: str, query: str, *, session_id: str | None = None) -> str:
    """The tiered-memory context block for `query` (policy/shared/LTM/skills/journal/working/
    recipes), rendered by nmem's own prompt assembler. Best-effort: an assembler hiccup returns
    an empty block rather than breaking the conversation."""
    if mem is None:
        return ""
    try:
        ctx = await mem.prompt.build(agent_id=agent_id, session_id=session_id, query=query)
        return ctx.full_injection
    except Exception as e:  # noqa: BLE001
        log.warning("[chat] memory context build failed: %s", e, exc_info=True)
        return ""


async def converse(runtime, message: str, *, history: list[dict] | None = None,
                   session_id: str | None = None, temperature: float = 0.4,
                   max_tokens: int = 700, continuity: bool = True,
                   continuity_tokens: int = 1200,
                   metacog_arm: str | None = None,
                   metacog_competence_override: dict | None = None,
                   metacog_audit: dict | None = None) -> str:
    """Hold one grounded turn: system prompt (persona) + the living continuity snapshot +
    retrieved memory for `message` + prior `history` ([{role,content},...]) + the user turn →
    the agent's backend → reply. After replying, advance the continuity checkpoint so the next
    turn / a restart resumes from here.

    Stateless w.r.t. conversation: the caller keeps `history` (the studio chat page does). The
    agent's memory tiers still ground every turn, so it 'remembers' across sessions what its
    cognition has consolidated — independent of the transient chat history.

    `continuity` (default on) gates both the wake-snapshot read and the checkpoint write; set
    it False for a pure query-grounded turn with no reorientation overhead.

    `metacog_arm` / `metacog_competence_override` / `metacog_audit` are EVAL-ONLY (the RCT
    rig, design §16.7) and default to None → this function is byte-identical to production.
    When `metacog_arm` is set the stack (iff it has eval enabled) applies that arm's lever
    recommendations for this turn; when `metacog_audit` (a mutable dict) is supplied it is
    filled with the request-linked audit — assigned arm, untransformed baseline signals,
    the applied directive/levers, and per-call backend usage — for the analysis, and kept
    OUT of the prompt and the reply."""
    persona = runtime._persona
    ctx = await build_context(runtime.mem, runtime.agent_id, message, session_id=session_id)
    cont = (await continuity_block(runtime.mem, runtime.agent_id, query=message,
                                   max_tokens=continuity_tokens)) if continuity else ""
    system = system_prompt(persona)
    # Continuity ("where you are now") precedes Memory ("what's relevant to this query"):
    # reorientation first, then query-specific recall.
    if cont.strip():
        system += "\n\n# Continuity — where you are right now\n" + cont
    if ctx.strip():
        system += "\n\n# Memory\n" + ctx
    # Working memory — the agent-level autonomous lane (current focus / last outcome). Lets a
    # chat turn reflect what the agent is *currently* working on even when the caller passes no
    # session_id (the session-scoped lane is already folded into `ctx` via build_context).
    # Gated + fail-open.
    try:
        if runtime.mem._config.working.enabled:
            from nmem.tiers.working import AUTONOMOUS_SESSION
            wm = await runtime.mem.working.build_prompt(AUTONOMOUS_SESSION, runtime.agent_id)
            if wm and wm.strip():
                system += "\n\n# Working memory — what I'm currently focused on\n" + wm.strip()
    except Exception:  # noqa: BLE001 — additive, never blocks a reply
        log.warning("[chat] working-memory inject failed (non-fatal)", exc_info=True)
    # Phase 1 — symbol surfacing on the chat turn (a soft surface, credited later by
    # affect/feedback). Writes a participation-ledger row keyed to a per-turn id so
    # representational self-improvement can learn from chat too. Byte-identical when the
    # nmem-sym surfacing ledger is off; fail-open — surfacing never blocks a reply.
    _surf_turn_id = None
    try:
        _bridge = getattr(runtime, "bridge", None)
        if _bridge is not None:
            from nmem_sym import config as sym_config
            if sym_config.settings.surfacing_ledger_enabled:
                from uuid import uuid4
                _surf_turn_id = uuid4().hex
                _surf = await _bridge.augment_search(
                    message, turn_id=_surf_turn_id, agent_id=runtime.agent_id)
                if _surf and _surf.strip():
                    system += ("\n\n# Graph hypotheses (speculative — weigh, don't assume)\n"
                               + _surf.strip())
    except Exception:  # noqa: BLE001 — surfacing is additive, never a blocker
        log.warning("[chat] symbol surfacing failed (non-fatal)", exc_info=True)
    messages = [{"role": "system", "content": system}]
    messages += list(history or [])
    messages.append({"role": "user", "content": message})
    # Metacognitive control (Level 4): fetch this turn's host-actuator recommendations
    # and apply them to the brain call. The stack (recommend_mode) decides whether anything
    # comes back ({} in off/canary); converse is a dumb applier and the backend's
    # _apply_family maps the abstract level to the model family's dialect. Fail-open.
    extra, audit = await _metacog_extra(runtime, message, arm=metacog_arm,
                                        competence_override=metacog_competence_override)
    # Production (metacog_audit is None): call the backend EXACTLY as before — no
    # usage_sink kwarg is passed at all, so a custom backend without that parameter is
    # unaffected and this stays byte-identical. Eval only: capture per-call usage.
    kw = {"temperature": temperature, "max_tokens": max_tokens, **extra}
    usage_sink = [] if metacog_audit is not None else None
    if metacog_audit is not None:
        kw["usage_sink"] = usage_sink
        # Populate the audit BEFORE the backend call. `usage_sink` is attached by
        # REFERENCE (the backend appends each attempt in place), so if the brain call
        # raises mid-turn — e.g. a Qwen retry timeout after the first attempt already
        # burned its token budget — the caller still keeps the arm metadata + whatever
        # cost was recorded. Populating only after the await would lose it (codex P2).
        if audit:
            metacog_audit.update(audit)
        metacog_audit["applied_extra"] = extra           # the levers actually applied
        metacog_audit["backend_calls"] = usage_sink      # realized cost, all attempts
        # Grounding fingerprint (eval-only): the rig compares this ACROSS arms of the same
        # task to check whether retrieval gave every arm byte-identical context. Retrieval
        # is query-driven (arm-independent) but rebuilt live per trial and LTM/journal reads
        # mutate access metadata (codex §17.6 P1-2), so 'same grounding' must be MEASURED,
        # not assumed — if these hashes diverge across arms, the rig must freeze/replay.
        _sys = next((m["content"] for m in messages if m.get("role") == "system"), "")
        metacog_audit["grounding_sha"] = _hashlib.sha256(_sys.encode()).hexdigest()[:16]
        metacog_audit["grounding_len"] = len(_sys)
    # Viz: light the prefrontal "reasoning" field for the duration of the brain call.
    # No-op + non-throwing when viz is disabled; never touches messages/kw/reply, so
    # this stays byte-identical to production for the eval rig.
    try:
        from nmem_sym.viz_events import viz_llm, REGION_PREFRONTAL
        _viz_cm = viz_llm("converse", REGION_PREFRONTAL, agent_id=runtime.agent_id)
    except Exception:  # noqa: BLE001
        from contextlib import nullcontext
        _viz_cm = nullcontext()
    async with _viz_cm:
        reply = await runtime.backend.chat(messages, **kw)
    if continuity:
        await record_turn_checkpoint(runtime.mem, runtime.agent_id, message, reply)
    # Working memory: record this turn's task in the session lane (read back on the next turn
    # via build_context's session-scoped working read). Session-scoped, gated, fail-open.
    if session_id:
        try:
            if runtime.mem._config.working.enabled:
                await runtime.mem.working.set(
                    session_id, runtime.agent_id, "current_task", message[:500], priority=1)
        except Exception:  # noqa: BLE001 — additive, never blocks
            log.warning("[chat] working-memory current_task write failed (non-fatal)", exc_info=True)
    # Phase 1 — attach the produced answer to this turn's surfacing-ledger row so the
    # offline echo-back pass can attribute which surfaced items the answer relied on.
    if _surf_turn_id:
        try:
            await runtime.bridge.record_surfacing_answer(_surf_turn_id, reply or "")
        except Exception:  # noqa: BLE001
            log.warning("[chat] record_surfacing_answer failed (non-fatal)", exc_info=True)
    return reply


async def _metacog_extra(runtime, message: str, *, arm: str | None = None,
                         competence_override: dict | None = None) -> tuple[dict, dict | None]:
    """This turn's metacognitive lever recommendations mapped to backend.chat kwargs,
    plus an optional eval audit record. Returns ``(extra, audit)``.

    Asks the stack's control seam (``mem.control_recommendations``) for the levers converse
    honours — currently ``reasoning_effort`` — with a ControlContext scoped to this turn's
    task. ``extra`` is {} (an unchanged call) when there is no seam, the stack returns
    nothing (off/canary), or anything errors. The abstract level is passed straight through
    as ``reasoning_effort``; the backend's ``_apply_family`` collapses it to the model
    family's real lever (Qwen: enable_thinking + token budget; gemma/generic: no-op).

    ``arm`` is EVAL-ONLY (design §16.7). When set, it is added to the seam context and, iff
    the stack has eval enabled, the seam returns a sentinel-tagged ``{recs, audit}`` which we
    unpack — otherwise it is ignored and the ordinary env-gated recs come back. Optional keys
    (``arm``/``competence_override``) are omitted when None so an ordinary turn's context
    payload is unchanged (codex P2-8)."""
    fn = getattr(getattr(runtime, "mem", None), "control_recommendations", None)
    if fn is None:
        return {}, None
    context = {
        "agent_id": runtime.agent_id,
        "task": message,
        "actuators": ["reasoning_effort"],
    }
    if arm is not None:
        context["arm"] = arm
        if competence_override is not None:
            context["competence_override"] = competence_override
    try:
        result = await fn(context)
    except Exception as e:  # noqa: BLE001
        log.debug("[chat] metacog recommendations failed (fail-open): %s", e)
        return {}, None
    result = result or {}
    if result.get("__metacog_arm__"):        # eval arm path: unpack recs + audit
        recs, audit = (result.get("recs") or {}), result.get("audit")
    else:                                    # ordinary env-gated recs (production shape)
        recs, audit = result, None
    eff = recs.get("reasoning_effort")
    return ({"reasoning_effort": eff} if eff else {}), audit
