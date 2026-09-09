"""Continuity seam for agent_core turns — the shared read/write helpers every turn-taking
seam uses to consume the continuity layer.

The continuity layer (nmem core: ``MemorySystem.wake()`` + the narrative/checkpoint store)
PRODUCES a living "where am I right now" snapshot and a per-turn checkpoint. This module is
the CONSUMER side, factored out of ``agent_core.chat`` so every seam where the agent takes a
turn or acts — a chat turn (``chat.converse``), a peer challenge (``peer.PeerExchange``), a
proactive utterance (``comms.CommsLoop``) — reads and advances continuity through ONE tested
path instead of each re-implementing it:

  * ``continuity_block(mem, id)``     — the living wake snapshot, rendered for prompt injection.
  * ``record_turn_checkpoint(mem, id, …)`` — advance the "where I left off" checkpoint after a turn.

Both are best-effort / fail-open: an agent without the continuity layer (an older ``mem``)
degrades cleanly to no snapshot and no checkpoint, and neither ever raises into a turn.
"""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)


async def continuity_block(mem, agent_id: str, *, query: str | None = None,
                           max_tokens: int = 1200) -> str:
    """The living wake snapshot for `agent_id` — ``mem.wake()`` rendered for injection.

    Query-independent by design (it answers "where am I", not "what is relevant to this
    query"), so it surfaces current commitments / open loops / goals / narrative / the
    "picking up from" checkpoint even on an empty stimulus. `query`, when given, adds a
    query-relevant lane on top. Best-effort: any hiccup (an old mem without ``wake``, a
    provider failure) returns an empty block rather than breaking the turn."""
    if mem is None or not hasattr(mem, "wake"):
        return ""
    try:
        result = await mem.wake(agent_id, query=query, max_tokens=max_tokens)
        return result.content or ""
    except Exception as e:  # noqa: BLE001
        log.warning("[continuity] wake failed: %s", e, exc_info=True)
        return ""


async def record_turn_checkpoint(mem, agent_id: str, message: str, reply: str) -> None:
    """Advance the immediate-continuity checkpoint after a turn — the *living write* that
    makes "where I left off" survive to the next turn and across a restart. Per-turn (not
    per-session) so it is progressive and does not depend on a session-close the hosts never
    emit. Best-effort: a checkpoint write must never fail a turn that already produced a reply."""
    if mem is None or not hasattr(mem, "save_continuity_checkpoint"):
        return
    try:
        # Keep it compact: the wake renderer's "immediate" lane is budget-capped, and the
        # summary is the first (priority) checkpoint line so it survives as a preview. No
        # filler last_action — it would compete for the lane ahead of the summary.
        summary = f"Asked: {message.strip()[:140]} — I answered: {reply.strip()[:140]}"
        await mem.save_continuity_checkpoint(agent_id, last_interaction_summary=summary)
    except Exception as e:  # noqa: BLE001
        log.warning("[continuity] turn checkpoint write failed: %s", e, exc_info=True)


async def record_action_checkpoint(mem, agent_id: str, last_action: str) -> None:
    """Advance the checkpoint after a one-sided ACTION (not a reply-producing turn) — a
    proactive utterance, a pursued goal. Records ``last_action`` only, so "where I left off"
    reflects what the agent last *did* even when no one asked it anything. Best-effort."""
    if mem is None or not hasattr(mem, "save_continuity_checkpoint") or not (last_action or "").strip():
        return
    try:
        await mem.save_continuity_checkpoint(agent_id, last_action=last_action.strip()[:200])
    except Exception as e:  # noqa: BLE001
        log.warning("[continuity] action checkpoint write failed: %s", e, exc_info=True)
