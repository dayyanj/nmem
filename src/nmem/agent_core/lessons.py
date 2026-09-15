"""Surface what an agent has LEARNED about its tools before it acts — the recall/apply half of
the capture→surface→apply loop (the capture half is nmem-act's ``make_reflective_sink``).

Composes nmem skills (``mem.skills.find``, salience-ranked) + nmem-sym compiled procedures
(``find_matching_procedures``) for a task, formats them for the actuator's prompt, and returns the
procedure ids it surfaced (=used) so the pursuit's real outcome can reward them (A2
utility-plasticity, threaded to ``record_action_outcome``).

Agent-agnostic — graduated from the reference agent's ``service/tool_learning.py``. The agent supplies its
own ``tool_tag`` (its actuator's skill namespace, shared with the reflective-capture sink so
capture and recall hit the SAME namespace) and ``agent_id``. Fail-open throughout: a learning
hiccup never breaks a cognitive cycle.
"""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)


async def recall_lessons(mem, graph, task_hint: str, *, tool_tag: str,
                         agent_id: str, limit: int = 5) -> tuple[str, list[int]]:
    """Prior tool-use skills + compiled procedures, formatted for an actuator task.

    Returns ``(text, procedure_ids)``: the ids are the compiled procedures surfaced (=used) for
    this task, to thread to ``record_action_outcome`` so A2 utility-plasticity rewards them by the
    pursuit's real outcome. ``("", [])`` when nothing is learned yet."""
    lines: list[str] = []
    procedure_ids: list[int] = []

    try:
        hits = await mem.skills.find(f"{tool_tag}: {task_hint}", limit=limit, agent_id=agent_id)
        for h in hits:
            what = (getattr(h, "what", "") or getattr(h, "name", "") or "").strip()
            if not what:
                continue
            worked = getattr(h, "worked", True)
            lines.append(f"- [{'DO' if worked else 'AVOID'}] {what}")
    except Exception as e:  # noqa: BLE001
        log.warning("[lessons] skills.find failed: %s", e)

    try:
        if graph is not None:
            from nmem_sym.procedural import find_matching_procedures, format_procedure_context
            # graph._embedder is an nmem EmbeddingProvider (Protocol = .embed → list[float]); the
            # whole nmem-sym codebase uses .embed. (Historically this called the raw sentence-
            # transformers .encode(), which fails for every provider-wrapped embedder — the
            # compiled-procedures branch silently no-op'd.)
            emb = graph._embedder.embed(f"{tool_tag}: {task_hint}")
            procs = await find_matching_procedures(graph.pool, emb, limit=3)
            for p in (procs or []):
                pid = getattr(p, "id", None) if not isinstance(p, dict) else p.get("id")
                if isinstance(pid, int):
                    procedure_ids.append(pid)
            ctx = format_procedure_context(procs) if procs else ""
            if ctx:
                lines.append("Compiled procedures:\n" + ctx)
    except Exception as e:  # noqa: BLE001
        log.warning("[lessons] procedure recall failed: %s", e)

    # Negative-transfer (Phase A/B): distilled lessons from ANALOGOUS past failures — "what
    # looks like this failed before, here's why / how to recover" — so the tool-selector avoids
    # repeating the mistake. THIS is how a reopened retry_differently goal becomes genuinely
    # lesson-informed (the distilled recovery/preventative reaches the next attempt), and every
    # pursuit gets negative transfer for free. Self-gating: find_analogous_failures no-ops unless
    # NMEM_SYM_FAILURE_MEMORY; best-effort — never blocks a pursuit.
    try:
        if graph is not None:
            from nmem_sym.failures import find_analogous_failures
            emb = getattr(graph, "embedder", None) or getattr(graph, "_embedder", None)
            fails = await find_analogous_failures(
                graph.pool, emb, task_hint, agent_id=agent_id, limit=3) if emb is not None else []
            for f in fails:
                rec = f.recovery_action or f.preventative_rule
                if f.reason_failed or rec:
                    lines.append(f"- [AVOID-REPEAT] failed before: {f.reason_failed[:100]}"
                                 + (f" → {rec}" if rec else ""))
    except Exception as e:  # noqa: BLE001
        log.warning("[lessons] failure recall failed: %s", e)

    if not lines:
        return "", procedure_ids
    return ("What you have learned about operating this tool — APPLY it before "
            "anything else:\n" + "\n".join(lines)), procedure_ids


def default_skill_chronic(mem, agent_id: str):
    """The default nmem ``skill.chronic`` handler: a known lesson keeps recurring → escalate by
    writing ONE high-merit strategy-lesson journal entry (not another duplicate skill), framing it
    as an approach/actuator problem to change rather than notes to accumulate. Agent-agnostic —
    pass to ``AgentRuntime(skill_chronic=default_skill_chronic(mem, agent_id))``. Fail-open: a
    handler hiccup never surfaces into the drive/skill loop. Graduated from the reference agent."""

    async def handler(data: dict) -> None:
        try:
            name = data.get("name") or data.get("canonical_key") or "a known lesson"
            n = data.get("trial_count")
            log.warning("[skill-chronic] '%s' recurred %sx — needs a strategy change, not another note",
                        name, n)
            await mem.journal.add(
                agent_id=agent_id, entry_type="chronic_skill",
                title=f"chronic failure mode: {name}",
                content=(f"Hit '{name}' {n} times so far ({(data.get('what') or '')[:200]}). It recurs "
                         f"despite being known — the approach/actuator must change, not accumulate more "
                         f"notes. Try a different tactic next time this comes up."),
                importance=8, record_type="lesson", grounding="confirmed")
        except Exception:  # noqa: BLE001
            log.warning("[skill-chronic] handler failed", exc_info=True)

    return handler
