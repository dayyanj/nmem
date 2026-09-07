"""Channel-agnostic communication + comms-learning (sweep B3).

The communication drive fires a `communicate` intent; this module turns that into an actual
utterance over WHATEVER channel the host provides, assesses how it landed, and LEARNS what
communication works — so michelle gets better at what/how/when she communicates over time.

Layering (see nmem/docs/agent-comms-channel-agnostic.md):
  - nmem-sym owns WHEN (the drive→intent), WHAT candidates (symbol_pending_utterances, from the
    B1 surprise → B2 utterance pipeline), and CLOSING the loop (bridge.record_comms_response →
    value-EWMA + honest drive discharge).
  - THIS module (agent-core; lifts verbatim into nmem-agent-core) owns the orchestration + the
    channel-neutral CommsAssessment (LLM-judged from the sink's raw response) + comms-learning
    (reuses the skill loop via mem.skills under a "communication:" tag → dedup/salience/chronic free).
  - The host ships ONE ChannelSink; nothing channel-specific lives here.

Grounded-in-surprise (founder call): we only deliver real pending utterances (worth-gated by B2),
never fresh-composed chatter. Fail-open throughout — a comms hiccup never breaks a drive cycle.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Protocol

log = logging.getLogger(__name__)

_TAG = "communication"   # comms-skills namespace in mem.skills (reuses #2 canonicalize/salience/chronic)

# non-finding markers reused idea: map engagement ordinal → scalar for honest discharge.
_ENGAGEMENT_SCORE = {"ignored": 0.0, "acknowledged": 0.4, "answered": 0.8, "acted_on": 1.0}


@dataclass
class Utterance:
    """One thing michelle is saying, over some channel. Channel-neutral."""
    id: int
    text: str
    source: str                     # e.g. "drive:novelty" — provenance for honest discharge
    action_type: str
    addressee: str = ""
    context: dict = field(default_factory=dict)
    _on_response: Callable[["Utterance", str, float | None], Awaitable[None]] | None = None
    _sent_at: float = 0.0

    async def record_response(self, raw_text: str, *, latency_s: float | None = None) -> None:
        """Called by the sink when the recipient responds (sync or async). Routes the raw
        response into the core assessor. A channel with no response signal never calls this."""
        if self._on_response is not None:
            await self._on_response(self, raw_text, latency_s)


class ChannelSink(Protocol):
    """The ONLY channel-specific code. Send the utterance; when/if a response arrives, capture
    the raw text and call utterance.record_response(...). Return True if delivered."""
    async def deliver(self, utterance: Utterance) -> bool: ...


class CommsLoop:
    """Wire the communicate intent to a channel + comms-learning. Channel-free."""

    def __init__(self, bridge, mem, backend, sink: ChannelSink, *, agent_id: str = ""):
        self._bridge = bridge
        self._mem = mem
        self._backend = backend        # LLM for assessment; None → scalar fallback
        self._sink = sink
        self._agent_id = agent_id

    # ── intent handler (registered via drives.on_intent) ──────────
    async def on_intent(self, intent) -> float | None:
        """Fires on every drive intent; acts only on `communicate`. Delivers the top pending
        utterance and returns 0.0 (deferred) — the drive discharges later via
        record_comms_response when the response is assessed. Returns None for other intents
        (so this handler is inert for them)."""
        if getattr(intent, "action", None) != "communicate":
            return None
        try:
            row = await self._select_pending()
            if row is None:
                return 0.0                      # nothing worth saying yet → no discharge
            utt = Utterance(
                id=row["id"], text=row["text"], source=row["source"] or "drive:communication",
                action_type=row["action_type"] or "communicate", addressee=row["addressee"] or "",
                context={"valence": row.get("valence"), "relevance": row.get("relevance")},
                _on_response=self._handle_response, _sent_at=time.monotonic())
            delivered = await self._sink.deliver(utt)
            if delivered:
                await self._mark_delivered(row["id"])
                log.info("[comms] delivered utterance #%s to %s", row["id"], utt.addressee or "?")
            return 0.0                          # deferred discharge (async response)
        except Exception:  # noqa: BLE001
            log.warning("[comms] on_intent failed", exc_info=True)
            return 0.0

    # ── response → assess → learn → discharge ─────────────────────
    async def _handle_response(self, utt: Utterance, raw_text: str, latency_s: float | None) -> None:
        try:
            a = await self._assess(utt.text, raw_text)
            score = float(a.get("score", _ENGAGEMENT_SCORE.get(a.get("engagement", ""), 0.4)))
            answered = a.get("engagement") not in ("ignored", None)
            # 1) close the nmem-sym loop: value-EWMA + honest drive discharge by how it landed
            try:
                await self._bridge.record_comms_response(
                    utterance_id=utt.id, source=utt.source, action_type=utt.action_type,
                    outcome_strength=max(0.0, min(1.0, score)), answered=answered)
            except Exception:  # noqa: BLE001
                log.warning("[comms] record_comms_response failed", exc_info=True)
            # 2) LEARN what communication works — a comms-skill via the skill loop (#2)
            lesson = (a.get("lesson") or "").strip()
            if lesson:
                worked = score >= 0.6
                try:
                    await self._mem.skills.record(f"{_TAG}: {lesson}",
                                                  outcome=f"{a.get('engagement','')}/{a.get('valence','')}",
                                                  worked=worked, agent_id=self._agent_id)
                except Exception:  # noqa: BLE001
                    log.warning("[comms] comms-skill record failed", exc_info=True)
            log.info("[comms] utterance #%s assessed: engagement=%s score=%.2f",
                     utt.id, a.get("engagement"), score)
        except Exception:  # noqa: BLE001
            log.warning("[comms] _handle_response failed", exc_info=True)

    # ── helpers ───────────────────────────────────────────────────
    async def recall_comms_lessons(self, hint: str, *, limit: int = 3) -> str:
        """Surface learned comms-skills to steer the next utterance's phrasing/selection.
        (Exposed for a composer; grounded-first delivery uses pre-rendered text, so this is
        advisory for now.) Salience-ranked via #2."""
        try:
            hits = await self._mem.skills.find(f"{_TAG}: {hint}", limit=limit, agent_id=self._agent_id)
            return "\n".join(f"- [{'DO' if getattr(h,'worked',True) else 'AVOID'}] {getattr(h,'what','')}"
                             for h in (hits or []) if getattr(h, "what", ""))
        except Exception:  # noqa: BLE001
            return ""

    async def _select_pending(self) -> dict | None:
        graph = getattr(self._mem, "_graph", None) or getattr(self._bridge, "_graph", None)
        pool = getattr(graph, "pool", None)
        if pool is None:
            return None
        row = await pool.fetchrow(
            "SELECT id, source, action_type, text, valence, relevance, addressee "
            "FROM symbol_pending_utterances WHERE status='pending' AND text <> '' "
            "ORDER BY relevance DESC, magnitude DESC, created_at ASC LIMIT 1")
        return dict(row) if row else None

    async def _mark_delivered(self, utterance_id: int) -> None:
        graph = getattr(self._mem, "_graph", None) or getattr(self._bridge, "_graph", None)
        pool = getattr(graph, "pool", None)
        if pool is None:
            return
        await pool.execute(
            "UPDATE symbol_pending_utterances SET status='delivered', delivered_at=now() "
            "WHERE id=$1 AND status='pending'", utterance_id)

    async def _assess(self, utterance_text: str, raw_text: str) -> dict:
        """LLM-judge how the utterance landed → {engagement, valence, usefulness, score, lesson}.
        Scalar fallback (no backend / failure): a plain heuristic on the response text."""
        if self._backend is None or not raw_text:
            return self._fallback_assess(raw_text)
        import json
        system = (
            "You assess how an AI agent's message landed with its recipient, so the agent can learn "
            "to communicate better. Given the MESSAGE and the RECIPIENT'S RESPONSE, output JSON: "
            "engagement (one of: ignored, acknowledged, answered, acted_on), valence (-1..1, how well "
            "it was received), usefulness (0..1, did it help/advance things), and a SHORT reusable "
            "lesson about what made this message land well or poorly (about the communication style/"
            "timing/content — NOT the topic). One concise lesson.")
        user = f"MESSAGE:\n{utterance_text[:800]}\n\nRECIPIENT'S RESPONSE:\n{raw_text[:800]}"
        schema = {"type": "object", "properties": {
            "engagement": {"type": "string"}, "valence": {"type": "number"},
            "usefulness": {"type": "number"}, "lesson": {"type": "string"}},
            "required": ["engagement", "valence", "usefulness", "lesson"]}
        try:
            out = await self._backend.chat(
                [{"role": "system", "content": system}, {"role": "user", "content": user}],
                max_tokens=200, response_format={"type": "json_schema",
                                                 "json_schema": {"name": "comms_assessment", "schema": schema}})
            if isinstance(out, str):
                c = out.strip()
                if c.startswith("```"):
                    c = c.strip("`")
                    c = c[4:] if c.startswith("json") else c
                a = json.loads(c)
            else:
                a = out
            eng = a.get("engagement", "acknowledged")
            base = _ENGAGEMENT_SCORE.get(eng, 0.4)
            # blend engagement with valence/usefulness for the discharge scalar
            val = max(-1.0, min(1.0, float(a.get("valence", 0.0))))
            use = max(0.0, min(1.0, float(a.get("usefulness", 0.0))))
            a["score"] = max(0.0, min(1.0, 0.5 * base + 0.25 * ((val + 1) / 2) + 0.25 * use))
            return a
        except Exception as e:  # noqa: BLE001
            log.debug("[comms] LLM assess failed (→ fallback): %s", e)
            return self._fallback_assess(raw_text)

    @staticmethod
    def _fallback_assess(raw_text: str) -> dict:
        if not raw_text:
            return {"engagement": "ignored", "valence": 0.0, "usefulness": 0.0, "score": 0.0, "lesson": ""}
        # any substantive reply → 'answered' at a neutral score; no LLM to extract a lesson.
        return {"engagement": "answered", "valence": 0.1, "usefulness": 0.5,
                "score": _ENGAGEMENT_SCORE["answered"], "lesson": ""}
