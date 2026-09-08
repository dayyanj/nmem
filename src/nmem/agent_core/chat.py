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
  * ``converse(runtime, msg)``   — assemble [system + memory] + history + the user turn,
                                    call the agent's own backend, return the reply.

michelle's converse (service/identity.build_memory_context + build_system_prompt) is the
reference; this keeps only what is agent-generic (her core_profile/persona files and the
capability-context prefix stay hers).
"""
from __future__ import annotations

import logging

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
                   max_tokens: int = 700) -> str:
    """Hold one grounded turn: system prompt (persona) + retrieved memory for `message` +
    prior `history` ([{role,content},...]) + the user turn → the agent's backend → reply.

    Stateless w.r.t. conversation: the caller keeps `history` (the studio chat page does). The
    agent's memory tiers still ground every turn, so it 'remembers' across sessions what its
    cognition has consolidated — independent of the transient chat history."""
    persona = runtime._persona
    ctx = await build_context(runtime.mem, runtime.agent_id, message, session_id=session_id)
    system = system_prompt(persona)
    if ctx.strip():
        system += "\n\n# Memory\n" + ctx
    messages = [{"role": "system", "content": system}]
    messages += list(history or [])
    messages.append({"role": "user", "content": message})
    return await runtime.backend.chat(messages, temperature=temperature, max_tokens=max_tokens)
