"""Surface what an agent has LEARNED about its tools before it acts — the recall/apply half of
the capture→surface→apply loop (the capture half is nmem-act's ``make_reflective_sink``).

Composes nmem skills (``mem.skills.find``, salience-ranked) + nmem-sym compiled procedures
(``find_matching_procedures``) for a task, formats them for the actuator's prompt, and returns the
procedure ids it surfaced (=used) so the pursuit's real outcome can reward them (A2
utility-plasticity, threaded to ``record_action_outcome``).

Agent-agnostic — graduated from michelle's ``service/tool_learning.py``. The agent supplies its
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
            emb = graph._embedder.encode(f"{tool_tag}: {task_hint}").tolist()
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

    if not lines:
        return "", procedure_ids
    return ("What you have learned about operating this tool — APPLY it before "
            "anything else:\n" + "\n".join(lines)), procedure_ids
