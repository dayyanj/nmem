"""Session-summary + interlocutor dossier — graduated from the dj-twin's ``/api/twin/session-end``.

GENERIC (agent_core): given a finished conversation transcript, an agent SUMMARIZES the takeaway
(not the raw turns — :func:`~nmem.agent_core.chat.converse` already writes a rolling per-turn
continuity checkpoint) into a journal entry; and — when it knows WHO it spoke with — accumulates a
durable per-interlocutor ENTITY dossier plus an LTM note (the LTM write is what actually feeds the
nmem-sym symbol graph, so the person/agent gets correlated across conversations).

This is exactly what converse lacked: a consolidated conversation memory and a profile of the
interlocutor that survives across sessions (the rolling checkpoint only ever holds the LAST turn).
Only the interlocutor-IDENTITY SOURCE is host-specific — the twin gets it from voice recognition, a
chat host from the request ("I am Claude Code") — so identity is passed IN, everything else is here.

Fail-open by construction: every failure returns ``{"ok": False, …}`` and never raises into a caller.
The twin's original endpoint (DJ-AI ``service/server.py`` :func:`twin_session_end`) stays — it adds
voice-identity grounding on top; it can later be re-expressed over this helper.
"""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)

_SUMMARY_SYS = (
    "You are {agent}, writing a memory of a conversation you just had.{who} Summarize in 2-4 "
    "sentences what was ACTUALLY discussed, decided, or discovered — concrete and specific (names, "
    "facts, decisions), not generic chatter about there being a conversation. If nothing substantive "
    "happened, say that briefly instead of padding it out.")


def _convo_text(transcript) -> str:
    """Render a transcript to ``Them:/Me:`` lines. Accepts ``[{role,content}]`` or a list of
    alternating strings (user first)."""
    lines: list[str] = []
    for i, t in enumerate(transcript):
        if isinstance(t, dict):
            role = (t.get("role") or "user").lower()
            content = (t.get("content") or "").strip()
            who = "Them" if role in ("user", "human") else "Me"
        else:
            content = str(t).strip()
            who = "Them" if i % 2 == 0 else "Me"
        if content:
            lines.append(f"{who}: {content}")
    return "\n".join(lines)


async def summarize_and_remember(mem, backend, agent_id: str, transcript, *,
                                 interlocutor_id: str | None = None,
                                 interlocutor_name: str | None = None,
                                 interlocutor_type: str = "agent",
                                 min_turns: int = 2, max_tokens: int = 220) -> dict:
    """Summarize a finished conversation → journal, and (if the interlocutor is identified by a
    STABLE id) update a durable entity dossier + LTM note for them.

    ``transcript`` is ``[{role,content}]`` or alternating strings (user first). ``interlocutor_id``
    must be stable across conversations for the dossier to cohere (e.g. ``"claude-code"``). Returns
    ``{"ok", "journal_id"?, "dossier"?, "skipped"?/"error"?}``. Fail-open."""
    if not transcript or len(transcript) < min_turns:
        return {"ok": True, "skipped": "too short"}
    convo = _convo_text(transcript)
    if not convo.strip():
        return {"ok": True, "skipped": "empty"}

    who = f" You were talking with {interlocutor_name}." if interlocutor_name else ""
    sys = _SUMMARY_SYS.format(agent=agent_id, who=who)
    try:
        summary = (await backend.chat(
            [{"role": "system", "content": sys}, {"role": "user", "content": convo}],
            max_tokens=max_tokens) or "").strip()
    except Exception as e:  # noqa: BLE001
        log.warning("[session-summary] summarization failed (non-fatal): %s", e, exc_info=True)
        return {"ok": False, "error": str(e)}
    if not summary:
        return {"ok": True, "skipped": "empty summary"}

    tags = ["conversation", "summary"]
    if interlocutor_name:
        tags.append(f"with:{interlocutor_name}")
    result: dict = {"ok": True}
    # 1. Consolidated takeaway → journal (importance 5: a synthesized takeaway, above a raw turn).
    try:
        entry = await mem.journal.add(
            agent_id=agent_id, entry_type="conversation_summary",
            title=summary[:80], content=summary, importance=5,
            tags=tags, record_type="evidence", grounding="inferred",
            compress=len(summary) > 300)
        result["journal_id"] = getattr(entry, "id", None)
    except Exception as e:  # noqa: BLE001
        log.warning("[session-summary] journal write failed (non-fatal): %s", e, exc_info=True)
        return {"ok": False, "error": str(e)}

    # 2. Durable interlocutor profile — only with a stable identity. Entity dossier + an LTM note;
    # the LTM write is what feeds the symbol graph (entity saves alone don't), so the interlocutor
    # gets correlated into nmem-sym. Keyed by id so later saves version the SAME entry, not pile up.
    if interlocutor_id:
        try:
            await mem.entity.save(
                entity_type=interlocutor_type, entity_id=str(interlocutor_id),
                entity_name=interlocutor_name or str(interlocutor_id), agent_id=agent_id,
                content=summary, record_type="summary", grounding="inferred",
                tags=["conversation"])
            await mem.ltm.save(
                agent_id=agent_id, category="person_note",
                key=f"conversations_with_{interlocutor_id}",
                content=f"{interlocutor_name or interlocutor_id}: {summary}",
                importance=5, record_type="fact", grounding="inferred")
            result["dossier"] = str(interlocutor_id)
        except Exception as e:  # noqa: BLE001
            log.warning("[session-summary] dossier write failed (non-fatal): %s", e, exc_info=True)
    return result
