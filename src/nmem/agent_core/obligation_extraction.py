"""Conversational obligations — lift a commitment ("research X and answer by Friday")
out of a chat turn and impose it, so the accountability axis of the relational self is fed
by real interaction (chat / nmem-exchange), not only the extrinsic ObligationLedger.

Agent-agnostic and lives in agent_core so EVERY agent that talks through
``agent_core.chat.converse`` gets it. The flow is deliberately layered so it is cheap and
safe by default:

  cheap cue pre-filter  →  LLM structured extraction  →  authority gate  →  commitments.impose

- **Pre-filter** (`looks_like_commitment`): a regex over request + deadline cues so the LLM
  is only invoked on the small fraction of turns that plausibly contain a deliver-by-a-time
  ask. No LLM cost on ordinary chat.
- **Extraction** (`extract_commitment`): one small JSON call that decides whether the user
  actually asked THIS agent to deliver something by a specific time, and returns the task +
  an absolute deadline (the model resolves "by Friday" against a supplied `now`).
- **Authority gate** (`AuthorityGate`): WHO may bind the agent. v1 is *fully open* — anyone
  who chats can impose (founder decision) — but it is a pluggable seam, not hardcoded, so a
  later version can tighten it via the trust axis (self_defers_to) + verified identity
  without touching this flow. The gate returns (authorized, authority_weight).
- **Impose**: ``mem.commitments.impose(...)`` records the commitment and mirrors it into the
  nmem-sym ObligationLedger; its lifecycle (fulfil/miss/breach) is later recorded by the B1
  obligation producer → consolidated into a ``self_accountable_to`` belief.

Everything is flag-gated (``NMEM_CHAT_OBLIGATIONS_ENABLED``, default OFF) and fail-open: a
hiccup anywhere returns None and never disturbs the conversation.

This is the **live-chat** complement to the existing **nightly** journal-based commitment
detection (``consolidation.detect_commitments``, source='detected'): that one scans the day's
journal retrospectively; this one catches the ask AT THE TURN, so deadline pressure starts
immediately. Same guards (who + description + future deadline + confidence). The two don't
double-impose — converse does not journal the raw turn, and nightly detection dedups by
(requester, description) within its window.
"""
from __future__ import annotations

import asyncio
import inspect
import json
import logging
import math
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Awaitable, Callable, Protocol

# The Speaker value object now lives in agent_core.speaker (its home once speaker RESOLUTION —
# not just attribution — became a first-class per-turn concern, Phase 2). Re-exported here so
# existing ``obligation_extraction.Speaker`` callers and tests keep working unchanged.
from nmem.agent_core.speaker import SPEAKER_SOURCES, Speaker  # noqa: F401

log = logging.getLogger(__name__)

# v1 requester when no interlocutor identity is threaded through the chat turn. The chat path
# carries no speaker id today (only /chat/session-end does), and v1 authorization is "anyone
# who chats", so all conversational commitments accrue accountability toward one generic
# counterpart. When identity lands (nmem-identity), converse passes the real speaker and this
# collapses to per-person accountability with no change here.
DEFAULT_REQUESTER = "interlocutor"


# Cheap pre-filter: a request cue AND a deadline cue in the same turn. Deliberately broad
# (recall over precision — the LLM is the real decision) but not so broad it fires on every
# turn. Case-insensitive.
_REQUEST_CUE = re.compile(
    r"\b(can you|could you|please|i(?:'| a)?m asking|i need|i'd like|we need|"
    r"get me|send me|give me|provide|prepare|research|find out|look into|"
    r"put together|draft|write up|come back|report back|follow up|let me know)\b",
    re.IGNORECASE,
)
_DEADLINE_CUE = re.compile(
    r"\b(by (?:end of |eod |cob )?(?:today|tonight|tomorrow|monday|tuesday|wednesday|"
    r"thursday|friday|saturday|sunday|next week|this week|end of (?:the )?(?:day|week|month)|"
    r"\d|jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)|"
    r"\bdeadline\b|\bdue\b|\bbefore (?:the )?(?:end|close|\d|monday|tuesday|wednesday|"
    r"thursday|friday|saturday|sunday|tomorrow|next)|within \d+\s*(?:hours?|days?|weeks?)|"
    r"\bby then\b|\bno later than\b)",
    re.IGNORECASE,
)


def enabled() -> bool:
    """Flag gate. Default OFF (a new capability lands default-off per the graduation rule)."""
    return os.getenv("NMEM_CHAT_OBLIGATIONS_ENABLED", "").strip().lower() in ("1", "true", "yes", "on")


def looks_like_commitment(message: str) -> bool:
    """Cheap pre-filter: does this turn plausibly ask the agent to deliver something by a
    time? Gates the (more expensive) LLM extraction — keeps ordinary chat LLM-free."""
    if not message or len(message) > 4000:
        return False
    return bool(_REQUEST_CUE.search(message) and _DEADLINE_CUE.search(message))


@dataclass
class ExtractedCommitment:
    is_commitment: bool
    description: str
    deadline: datetime | None       # tz-aware UTC, in the future
    confidence: float


class AuthorityGate(Protocol):
    """WHO may bind the agent. Returns (authorized, authority_weight), or a coroutine of the
    same (an async gate that reads a store — e.g. `deference_gate` — is awaited by the caller).
    The weight feeds the obligation's `authority` (the requester's standing trust) downstream.
    A pluggable seam: v1 is open; Phase 6-B grades the weight by learned deference."""
    def __call__(self, requester: str, description: str, runtime): ...


def open_gate(requester: str, description: str, runtime) -> tuple[bool, float]:
    """v1 authorization (founder decision): anyone who chats can impose. Authorized always,
    with a modest default authority. Replace via the `gate` param to tighten later (e.g.
    authorize only interlocutors the agent already `self_defers_to`, once identity is known)."""
    return True, 0.5


# ─── Phase 6-B: relationship-weighted authority ──────────────────────────────
# The gate seam above anticipated this: grade the obligation's `authority` by how much the
# agent already `self_defers_to` the resolved requester (learned in nmem-sym from the host's
# approval/correction path). An ask from a strongly-deferred-to authority binds harder; a
# stranger's binds weakly. GRADED, NEVER a binary reject in the core — `authorized` stays True
# always; the host policy tier owns any hard "won't do this for them" (design §6 overtrust
# guardrail, non-negotiable). Deference lifts ONLY `authority` (standing trust); per-turn
# identity confidence stays on `importance` (Phase 1). Flag-gated OFF; a cold graph (no grounded
# deference yet) with the default BASE≈0.5 is behaviourally identical to `open_gate`.


# The deference read runs inside the post-reply obligation bookkeeping that converse AWAITS
# before returning; bound it tightly so a stalled query never withholds a ready reply (mirrors
# _EXTRACT_TIMEOUT_S). On expiry the gate degrades to the stranger base.
_DEFERENCE_TIMEOUT_S = 2.0


def deference_authority_enabled() -> bool:
    """Phase 6-B flag. Default OFF (graduation rule). When off, `maybe_impose_from_chat` uses
    `open_gate` and behaviour is byte-identical to Phase 5."""
    return os.getenv("NMEM_CHAT_DEFERENCE_AUTHORITY_ENABLED", "").strip().lower() \
        in ("1", "true", "yes", "on")


def _env_float(name: str, default: float) -> float:
    """A finite float from env, else the default (a malformed/non-finite value is ignored)."""
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        v = float(raw)
    except (TypeError, ValueError):
        return default
    return v if math.isfinite(v) else default


def _deference_base() -> float:
    """Stranger/ungrounded authority — the floor everyone binds at. Default 0.5 so a flag-ON
    cold graph == today's flat `open_gate`."""
    return _env_float("NMEM_CHAT_DEFERENCE_BASE", 0.5)


def _deference_span() -> float:
    """How far a maximally-deferred-to, warm authority climbs above BASE. Default 0.5 → up to
    ~1.0 total for the strongest grounded deference."""
    return _env_float("NMEM_CHAT_DEFERENCE_SPAN", 0.5)


def _candidate_refs(who: str) -> list[str]:
    """The nmem-sym counterpart refs that could carry deference toward this requester. Deference
    is produced under `authority:{id}` (the approval producer), so a bare resolved id maps to
    that ref. Phase 6-A extends this with person-aliases so voice/face/text converge; Phase 6-B
    reads the single authority ref."""
    who = (who or "").strip()
    return [f"authority:{who}"] if who else []


async def deference_gate(requester: str, description: str, runtime) -> tuple[bool, float]:
    """Phase 6-B graded gate. `authorized` is ALWAYS True (never a binary reject in the core).
    `authority = BASE + SPAN·(strength · max(0, valence))` from the strongest GROUNDED
    self_defers_to edge toward `requester`, clamped to [0,1]:
      - stranger / ungrounded / nmem-sym absent / read error → BASE (fail-open to today's flat);
      - a FRAUGHT bond (negative valence) lifts NOTHING (max(0, valence)) — real deference
        evidence, but you don't grant standing to a judgement you keep having to block."""
    base = _deference_base()
    try:
        graph = getattr(runtime, "graph", None)
        pool = getattr(graph, "pool", None)
        if pool is None:                    # no symbol graph (thin agent) → stranger default
            return True, base
        bridge = getattr(runtime, "bridge", None)
        if bridge is not None and hasattr(bridge, "effective_owner"):
            owner = bridge.effective_owner()
        else:                               # bridge not wired — fall back to the isolated owner key
            from nmem_sym import config as _sym_cfg
            owner = getattr(_sym_cfg.settings, "recall_agent_id", "") or ""
        from nmem_sym.relational_surface import deference_for
        # Bound the lookup: this gate is awaited by _finalize_turn BEFORE converse returns the
        # already-generated reply, so a stalled query / exhausted pool must not withhold it —
        # on timeout, degrade to the stranger base (same discipline as extract_commitment).
        d = await asyncio.wait_for(
            deference_for(pool, owner_agent=owner, counterpart_refs=_candidate_refs(requester)),
            timeout=_DEFERENCE_TIMEOUT_S)
        if not d.grounded:
            return True, base
        lift = _deference_span() * (d.strength * max(0.0, d.valence))
        authority = base + lift
        if not math.isfinite(authority):
            return True, base
        return True, max(0.0, min(1.0, authority))
    except Exception:  # noqa: BLE001 — the gate must never fail the turn; degrade to stranger
        log.warning("[chat-obl] deference_gate failed (non-fatal) — stranger auth", exc_info=True)
        return True, base


_EXTRACT_SYS = (
    "You extract commitments from a user's message to an AI agent. A commitment exists ONLY "
    "when the user is asking THIS agent to DO something and DELIVER it by a specific time. "
    "A plain question, a fact lookup answered inline, or a request with no deadline is NOT a "
    "commitment. Resolve any relative time ('by Friday', 'tomorrow', 'end of week') to an "
    "absolute UTC datetime using the provided current time. Reply with ONLY a JSON object: "
    '{"is_commitment": bool, "description": "<the deliverable, imperative, <=160 chars>", '
    '"deadline": "<ISO-8601 UTC or null>", "confidence": <0..1>}. No prose.'
)


def _extract_json(text: str) -> dict | None:
    """Pull the first JSON object out of an LLM reply, tolerantly."""
    if not text:
        return None
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        return None
    try:
        obj = json.loads(m.group(0))
        return obj if isinstance(obj, dict) else None
    except (ValueError, TypeError):
        return None


def _parse_deadline(value, *, now: datetime) -> datetime | None:
    """Normalize the model's deadline to a tz-aware UTC datetime in the future. Accepts a
    datetime or an ISO-8601 string (trailing 'Z' ok; naive → assumed UTC). None/past → None
    (the sym ObligationLedger needs a real future deadline for its pressure curve)."""
    if value is None:
        return None
    dt = value
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return None
        try:
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        except ValueError:
            return None
    if not isinstance(dt, datetime):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    dt = dt.astimezone(timezone.utc)
    return dt if dt > now else None


# The extraction is post-reply bookkeeping that converse AWAITS before returning the
# already-generated answer, so it must never withhold a good reply: a stalled backend (both
# bundled backends default to a 300s HTTP timeout) would otherwise block the chat turn for
# minutes (codex). Bound it tightly — on timeout we simply skip this turn's extraction.
_EXTRACT_TIMEOUT_S = 12.0


async def extract_commitment(backend, message: str, *, now: datetime,
                             temperature: float = 0.0,
                             timeout: float = _EXTRACT_TIMEOUT_S) -> ExtractedCommitment | None:
    """LLM structured extraction. Returns an ExtractedCommitment (possibly is_commitment=False)
    or None on any failure/timeout. `backend` is an agent_core chat backend (``.chat(messages)``)."""
    if backend is None:
        return None
    user = (f"Current time (UTC): {now.isoformat()}\n"
            f"User message:\n{message.strip()}")
    try:
        raw = await asyncio.wait_for(
            backend.chat(
                [{"role": "system", "content": _EXTRACT_SYS},
                 {"role": "user", "content": user}],
                temperature=temperature, max_tokens=250),
            timeout=timeout)
    except asyncio.TimeoutError:
        log.warning("[chat-obl] extraction timed out after %.0fs — skipped (non-fatal)", timeout)
        return None
    except Exception:  # noqa: BLE001 — extraction never disturbs the turn
        log.warning("[chat-obl] extraction backend call failed (non-fatal)", exc_info=True)
        return None
    obj = _extract_json(raw)
    if obj is None:
        return None
    desc = str(obj.get("description") or "").strip()[:160]
    try:
        conf = float(obj.get("confidence", 0.0))
    except (TypeError, ValueError):
        conf = 0.0
    return ExtractedCommitment(
        is_commitment=bool(obj.get("is_commitment")) and bool(desc),
        description=desc,
        deadline=_parse_deadline(obj.get("deadline"), now=now),
        confidence=max(0.0, min(1.0, conf)),
    )


async def maybe_impose_from_chat(
    runtime, message: str, *,
    speaker: Speaker | None = None,
    requester: str | None = None,
    now: datetime | None = None,
    gate: AuthorityGate | None = None,
    min_confidence: float = 0.6,
):
    """Orchestrate chat → obligation for one user turn. Flag-gated, fail-open. Returns the
    imposed CommitmentInfo, or None when nothing was imposed (disabled, no cue, not a
    commitment, no future deadline, unauthorized, or error).

    Called from converse AFTER the reply is produced (bookkeeping, like the checkpoint write).

    ``speaker`` (design note ``nmem-identity-chat-style-modality``) is the optional soft
    identity of the interlocutor. When supplied its ``id`` becomes the obligation ``requester``
    (per-person accountability) and its ``confidence`` modulates the obligation's per-turn
    ``importance`` — a weakly-identified ask binds the agent more loosely. (Confidence is
    deliberately NOT applied to ``authority``: that is the requester's standing trust weight,
    which the gate owns and the ledger caches per requester.) ``requester`` is the pre-Speaker
    string form, kept for callers/tests that pass only an id; ``speaker`` wins when both given.
    With neither (the default), attribution falls back to ``DEFAULT_REQUESTER`` exactly as
    before.
    """
    if not enabled():
        return None
    mem = getattr(runtime, "mem", None)
    commitments = getattr(mem, "commitments", None) if mem is not None else None
    if commitments is None:
        return None
    if not looks_like_commitment(message):     # cheap gate before any LLM
        return None
    now = now or datetime.now(timezone.utc)
    try:
        extracted = await extract_commitment(runtime.backend, message, now=now)
        if extracted is None or not extracted.is_commitment:
            return None
        if extracted.deadline is None or extracted.confidence < min_confidence:
            return None                        # obligations need a real future deadline
        who = (speaker.id if speaker is not None else None) or requester or DEFAULT_REQUESTER
        # Gate selection: an explicit gate wins (callers/tests); else the Phase 6-B
        # deference gate when its flag is on, otherwise the open v1 gate. A gate may be sync
        # (open_gate) or async (deference_gate reads nmem-sym) — await the latter.
        active_gate = gate if gate is not None else (
            deference_gate if deference_authority_enabled() else open_gate)
        gate_result = active_gate(who, extracted.description, runtime)
        if inspect.isawaitable(gate_result):
            gate_result = await gate_result
        authorized, authority = gate_result
        if not authorized:
            log.info("[chat-obl] extracted commitment from %r NOT authorized — skipped", who)
            return None
        # Identity confidence modulates THIS obligation's pressure — graded, never a gate
        # (design §3.1): a turn from a weakly-identified speaker binds the agent more loosely
        # than one from a confidently-known or explicit principal. Confidence is per-TURN (how
        # sure we are who asked), so it attenuates the per-obligation `importance` (mirrored to
        # the nmem-sym ledger on EVERY impose) — NOT the per-requester `authority`, which the
        # gate owns and `CommitmentManager._mirror` registers once per requester then caches
        # (modulating it would only ever 'stick' for a requester's first obligation; codex).
        # No speaker → importance 1.0, byte-identical. confidence∈[0,1] → importance∈[0.5,1.0]:
        # a weak identity binds loosely, a confident/explicit principal binds full-weight.
        authority = float(authority)
        importance = 1.0 if speaker is None else (0.5 + 0.5 * speaker.confidence)
        # Dedup vs currently-open commitments in this scope (codex): a repeated/retried
        # "do X by Friday" must not spawn a second live obligation with its own lifecycle.
        # Matches the nightly detector's (requester, description) key. Best-effort — a list
        # hiccup falls through to impose rather than dropping a genuine commitment.
        try:
            for c in await commitments.list("open"):
                if c.requester == who and c.description == extracted.description:
                    log.info("[chat-obl] commitment already open (%r) — not re-imposed", who)
                    return None
        except Exception:  # noqa: BLE001
            log.warning("[chat-obl] open-commitment dedup check failed (non-fatal)", exc_info=True)
        info = await commitments.impose(
            who, extracted.description, extracted.deadline,
            authority=float(authority), importance=importance, source="chat")
        log.info("[chat-obl] imposed from %r (src=%s conf=%.2f imp=%.2f) due %s: %r",
                 who, (speaker.source if speaker is not None else "none"),
                 (speaker.confidence if speaker is not None else 0.0),
                 importance, extracted.deadline.isoformat(), extracted.description)
        return info
    except Exception:  # noqa: BLE001 — additive; never blocks the conversation
        log.warning("[chat-obl] maybe_impose_from_chat failed (non-fatal)", exc_info=True)
        return None
