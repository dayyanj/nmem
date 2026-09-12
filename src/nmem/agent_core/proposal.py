"""The default pursuit-proposal builder — the app-agnostic assembly graduated out of the reference agent's
``_build_pursuit_proposal`` (upstream plan §2.5). Turns a goal into an :class:`~nmem_act.ActionProposal`,
injecting the context every actuating agent wants:

  1. recalled tool lessons (+ the procedures they came from),
  2. prior findings the agent already knows (build on them, don't redo),
  3. anything the recall DRIVE proactively surfaced for this objective (then seed the next target),
  4. continuity — where the agent is right now (open loops / goals / drives / narrative),

and the drive-derived ``source`` (so the RIGHT drive discharges on success). Every step is fail-open —
context is a bonus; a pursuit must never die because a recall lookup failed.

The RUNTIME auto-wires this when a host supplies an executor but no ``build_proposal`` (a thin host gets
pursuit for free), reading its knobs from the ``pursuit:`` config; a host may still pass its own builder.

The assembled context lands in BOTH the proposal's ``rationale`` (what a ``ToolCallingExecutor``'s
selector reads) AND ``params['lessons']`` (what a params-reading runner like a sandbox reads), so it
reaches whichever executor the agent uses.
"""
from __future__ import annotations

import logging
from typing import Any, Callable

log = logging.getLogger(__name__)


def default_proposal(mem, graph, bridge=None, *, agent_id: str,
                     action_type: str = "llm_tool_call", capability_class: Any = None,
                     tool_tag: str | None = None, lessons_query: str | None = None,
                     recall_top_k: int = 3, continuity_tokens: int = 600) -> Callable:
    """Build an async ``build(goal) -> ActionProposal`` closure over an agent's memory + graph + bridge.

    ``action_type`` is what the executor dispatches on / the autonomy gate names (a ToolCallingExecutor
    ignores it and runs from ``rationale``; a params-runner like an agent's sandbox keys its handler on
    it — e.g. ``"pursue_knowledge"``). ``lessons_query`` defaults to the goal objective; ``tool_tag``
    scopes lesson recall to the agent's actuator skill namespace."""
    from nmem_act import ActionProposal, CapabilityClass
    cap = capability_class or CapabilityClass.READ_ONLY

    async def build(goal):
        obj, gid = goal.objective, goal.id

        lessons, proc_ids = "", []
        try:
            from nmem.agent_core import recall_lessons
            lessons, proc_ids = await recall_lessons(mem, graph, lessons_query or obj,
                                                     tool_tag=tool_tag, agent_id=agent_id)
        except Exception:  # noqa: BLE001
            log.warning("[proposal] recall_lessons failed", exc_info=True)

        prior = ""
        try:
            hits = await mem.search(agent_id, obj, top_k=recall_top_k)
            facts = [getattr(h, "content", "") for h in (hits or []) if getattr(h, "content", "")]
            if facts:
                prior = ("What you ALREADY know from earlier work — build on it, don't redo it:\n"
                         + "\n".join(f"- {f[:220]}" for f in facts) + "\n\n")
        except Exception:  # noqa: BLE001
            log.warning("[proposal] prior-findings recall failed", exc_info=True)

        try:
            from nmem.agent_core import recall
            surfaced = recall.context_for(obj)
            if surfaced:
                prior = surfaced + "\n\n" + prior
            if bridge is not None and getattr(bridge, "seed_recall", None):
                bridge.seed_recall(obj)
        except Exception:  # noqa: BLE001
            log.warning("[proposal] recall seed/inject failed", exc_info=True)

        try:
            from nmem.agent_core.continuity import continuity_block
            cont = await continuity_block(mem, agent_id, query=obj, max_tokens=continuity_tokens)
            if cont.strip():
                prior = "Where you are right now (act consistently with this):\n" + cont + "\n\n" + prior
        except Exception:  # noqa: BLE001
            log.warning("[proposal] continuity inject failed", exc_info=True)

        try:
            if mem._config.working.enabled:
                from nmem.tiers.working import AUTONOMOUS_SESSION
                wm = await mem.working.build_prompt(AUTONOMOUS_SESSION, agent_id)
                if wm and wm.strip():
                    # First = most immediate: what I'm focused on / just did, this session.
                    prior = "Your working memory (current focus / recent outcome):\n" + wm.strip() + "\n\n" + prior
        except Exception:  # noqa: BLE001
            log.warning("[proposal] working-memory inject failed", exc_info=True)

        try:
            from nmem.agent_core import proposal_source
            source = proposal_source(goal)
        except Exception:  # noqa: BLE001
            source = None

        context = (prior + lessons).strip()
        rationale = obj if not context else f"{obj}\n\n{context}"   # the tool selector reads rationale
        return ActionProposal(
            action_type=action_type, id=f"pursue:{gid}", source=source,
            rationale=rationale, capability_class=cap,
            params={"objective": obj, "goal_id": gid, "lessons": context, "procedure_ids": proc_ids})

    return build
