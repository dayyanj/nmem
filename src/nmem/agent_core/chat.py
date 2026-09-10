"""Grounded conversation for any nmem agent — the reusable core behind a chat UI.

The **G graduation** from michelle-ai: turning "talk to the agent" into three small,
agent-agnostic pieces so a new agent (and the studio's chat page) gets a memory-grounded
conversation for free:

  * ``system_prompt(persona)``   — the agent's identity + standing objectives, from Persona
                                    DATA (not michelle's markdown files).
  * ``build_context(mem, id, q)``— the tiered memory block for a query. This is now a
                                    one-liner over ``mem.prompt.build(...).full_injection``
                                    (nmem already assembles policy/shared/LTM/skills/journal/
                                    recipes with headers), so michelle's hand-rolled version
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

michelle's converse (service/identity.build_memory_context + build_system_prompt) is the
reference; this keeps only what is agent-generic (her core_profile/persona files and the
capability-context prefix stay hers).
"""
from __future__ import annotations

import logging

# The continuity read/write helpers are shared across all turn-taking seams (chat, peer,
# comms) — re-exported here so existing ``chat.continuity_block`` / ``chat.record_turn_checkpoint``
# call sites keep working while the single implementation lives in agent_core.continuity.
from nmem.agent_core.continuity import continuity_block, record_turn_checkpoint  # noqa: F401

log = logging.getLogger(__name__)


def system_prompt(persona) -> str:
    """The agent's base system prompt, derived from its Persona (identity + objectives +
    the entities it reasons about). Deliberately plain: an agent's *voice* is a host concern
    (michelle layers hers on top); this is the honest, grounded floor every agent shares."""
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
                   continuity_tokens: int = 1200) -> str:
    """Hold one grounded turn: system prompt (persona) + the living continuity snapshot +
    retrieved memory for `message` + prior `history` ([{role,content},...]) + the user turn →
    the agent's backend → reply. After replying, advance the continuity checkpoint so the next
    turn / a restart resumes from here.

    Stateless w.r.t. conversation: the caller keeps `history` (the studio chat page does). The
    agent's memory tiers still ground every turn, so it 'remembers' across sessions what its
    cognition has consolidated — independent of the transient chat history.

    `continuity` (default on) gates both the wake-snapshot read and the checkpoint write; set
    it False for a pure query-grounded turn with no reorientation overhead."""
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
    messages = [{"role": "system", "content": system}]
    messages += list(history or [])
    messages.append({"role": "user", "content": message})
    # Metacognitive control (Level 4): fetch this turn's host-actuator recommendations
    # and apply them to the brain call. The stack (recommend_mode) decides whether anything
    # comes back ({} in off/canary); converse is a dumb applier and the backend's
    # _apply_family maps the abstract level to the model family's dialect. Fail-open.
    extra = await _metacog_extra(runtime, message)
    reply = await runtime.backend.chat(messages, temperature=temperature,
                                       max_tokens=max_tokens, **extra)
    if continuity:
        await record_turn_checkpoint(runtime.mem, runtime.agent_id, message, reply)
    return reply


async def _metacog_extra(runtime, message: str) -> dict:
    """This turn's metacognitive lever recommendations mapped to backend.chat kwargs.

    Asks the stack's control seam (``mem.control_recommendations``) for the levers converse
    honours — currently ``reasoning_effort`` — with a ControlContext scoped to this turn's
    task. Returns {} (an unchanged call) when there is no seam, the stack returns nothing
    (off/canary), or anything errors. The abstract level is passed straight through as
    ``reasoning_effort``; the backend's ``_apply_family`` collapses it to the model family's
    real lever (Qwen: enable_thinking + token budget; gemma/generic: no-op)."""
    fn = getattr(getattr(runtime, "mem", None), "control_recommendations", None)
    if fn is None:
        return {}
    try:
        recs = await fn({
            "agent_id": runtime.agent_id,
            "task": message,
            "actuators": ["reasoning_effort"],
        })
    except Exception as e:  # noqa: BLE001
        log.debug("[chat] metacog recommendations failed (fail-open): %s", e)
        return {}
    eff = (recs or {}).get("reasoning_effort")
    return {"reasoning_effort": eff} if eff else {}
