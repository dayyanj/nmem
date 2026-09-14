"""Who is speaking this turn — a soft, agent-agnostic identity for the chat interlocutor.

The chat path has one place a turn passes through for every seam (default ``/chat``, the studio
chat page, a host's custom handler, the dj-twin voice stream): ``agent_core.chat.converse`` /
``converse_stream``. This module owns the ``Speaker`` value object and ``resolve_speaker`` — the
resolver ``converse`` calls at the top of a turn — so *filling* the speaker slot happens inside the
library, reaching every host with zero host code (design note ``nmem-identity-chat-style-modality``,
Phase 2). A host that overrides ``/chat`` (michelle) never sees the route-level parse; resolving
here is the fix for exactly that.

Resolution is strongest-signal-first and every branch is fail-open — an interlocutor identity is a
*soft prior*, never an authorization, so a hiccup returns whatever we already have (possibly None)
and the turn proceeds anonymously exactly as before:

    explicit `speaker=` (host auth)  →  a name self-declared in the turn  →  session continuity

Voice-session and text_style (nmem-identity) are deferred to Phase 3/5; the resolver is written so
they slot in as additional signals without changing this seam. The feature is flag-gated
(``NMEM_CHAT_SPEAKER_ENABLED``, default OFF per the graduation rule): while off, ``resolve_speaker``
returns the caller's explicit speaker untouched and performs **no** working-memory writes, so the
chat path stays byte-identical to Phase 1.
"""
from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass

log = logging.getLogger(__name__)

# Speaker sources ordered weakest→strongest by trust. A later fusion step (design:
# nmem-identity-chat-style, Phase 5) prefers the strongest signal when more than one fills the
# slot for a turn. Plain strings (not an Enum) so a host may pass any label; an unknown label
# is treated as weakest.
SPEAKER_SOURCES = (
    "unknown", "text_style", "session_continuity", "voice_session", "declared_name", "principal",
)


@dataclass(frozen=True)
class Speaker:
    """A soft, optional identity for the interlocutor of a chat turn (design note
    ``nmem-identity-chat-style-modality``). ``id`` is the stable person key used as the
    obligation ``requester``; ``confidence`` (0..1) is how sure we are it is really them;
    ``source`` records which signal filled the slot (see ``SPEAKER_SOURCES``).

    Phase 1 uses ``id`` (attribution) and ``confidence`` (per-obligation importance modulation)
    only. A ``None`` Speaker is the pre-identity default and keeps the chat path byte-identical
    to before — identity is *soft*: it may modulate an obligation's pressure but never authorizes
    anything.
    """
    id: str
    confidence: float = 0.0
    source: str = "unknown"

    @classmethod
    def from_dict(cls, d: dict | None) -> Speaker | None:
        """Build from a chat-request payload ``{id, confidence?, source?}``. A missing/blank
        ``id`` (or a non-dict) → ``None`` (no speaker), so a malformed or absent field degrades
        cleanly to the anonymous default rather than raising on the request path."""
        if not isinstance(d, dict):
            return None
        sid = str(d.get("id") or "").strip()
        if not sid:
            return None
        try:
            conf = float(d.get("confidence", 0.0))
        except (TypeError, ValueError):
            conf = 0.0
        conf = max(0.0, min(1.0, conf))
        src = str(d.get("source") or "unknown").strip() or "unknown"
        return cls(id=sid, confidence=conf, source=src)


def speaker_resolution_enabled() -> bool:
    """Flag gate for the Phase-2 in-converse resolution (self-declared name + session continuity).
    Default OFF (a new capability lands default-off per the graduation rule). While off,
    ``resolve_speaker`` is a no-op that returns the caller's explicit speaker — Phase 1's explicit
    override path is unaffected either way."""
    return os.getenv("NMEM_CHAT_SPEAKER_ENABLED", "").strip().lower() in ("1", "true", "yes", "on")


# A self-declared name in the turn: "it's Dayyan", "this is Dayyan", "I'm Dayyan", "my name is
# Dayyan", "call me Dayyan". The cue is case-insensitive (`(?i:...)` scoped to the cue only); the
# NAME must be Capitalised-then-lowercase (`[A-Z][a-z]+`) — rejecting ALL-CAPS shouts, acronyms,
# and the "it's raining" false positive — with NO internal apostrophe/hyphen, so a possessive
# ("It's Alice's birthday") fails the `(?![\w'’-])` boundary instead of capturing "Alice's" (codex).
# That same boundary means a name may not be a prefix of a longer alnum token: "It's PostgreSQL" /
# "I'm McDonald" reject rather than truncating to "Postgre"/"Mc". Internal-capital or apostrophised
# real names (O'Brien, DeAngelo) are missed, not mis-attributed — the correct trade for a soft
# signal. The cue is captured (group `cue`) so the resolver can hold the weak DEMONSTRATIVE cues
# ("it's"/"it is"/"this is", which also front non-persons — "it is Python") to the opener position.
_DECLARED_NAME_RE = re.compile(
    r"\b(?P<cue>(?i:it['’]s|it is|this is|i['’]m|i am|my name is|call me))\s+"
    r"(?P<first>[A-Z][a-z]{1,20})(?![\w'’-])"
    # Surname separator is HORIZONTAL whitespace only: a `\s+` here would cross a newline and glue
    # the next sentence's capitalised word on as a "surname" — "My name is Alice\nCan you…" →
    # "Alice Can" (codex). A real two-token name sits on one line.
    r"(?:[ \t]+(?P<last>[A-Z][a-z]{1,20})(?![\w'’-]))?"
)

# The weak, demonstrative openers: they introduce a person only at the START of the turn ("It's
# Dayyan", "Hey, it's Dayyan"), not mid-utterance where they usually front a thing ("Yes, it is
# Python"; "the winner, it's decided"). The strong cues (I'm / my name is / call me / I am) are
# unambiguous self-reference and are accepted anywhere (subject to the quote guard).
_DEMONSTRATIVE_CUES = frozenset({"it's", "it’s", "it is", "this is"})
# Preceding text allowed before a demonstrative cue: nothing, or a bare greeting + punctuation.
_GREETING_ONLY_RE = re.compile(r"(?i)^(?:hi|hey|hello|hiya|heya|yo|ok|okay)?[\s,!.:;—-]*$")

# Opening quote / code-span marks. A cue immediately preceded by one of these (ignoring spaces)
# sits inside a quoted or `code`-formatted span, which catches single-quoted passages too — their
# apostrophes make a global count unreliable, so the adjacency test carries them (codex).
_OPEN_QUOTE_CHARS = "\"'`“‘«‹"
# A capitalised token going possessive right after the matched name ("Alice Smith's birthday") —
# marks the name as a third party's, so the whole candidate is rejected rather than kept as its
# boundary-clean prefix "Alice" (codex).
_TRAILING_POSSESSIVE_RE = re.compile(r"^\s+[A-Z][a-z]*['’]")

# Common capitalised words that follow those cues but are NOT names ("this is Great", "it's
# Monday", "I'm Sorry"). Lower-cased for comparison. Recall over precision elsewhere, but a
# mis-attributed obligation is worse than a missed one, so filter the obvious non-names.
_NOT_NAMES = frozenset({
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
    "january", "february", "march", "april", "may", "june", "july", "august",
    "september", "october", "november", "december",
    "okay", "sorry", "just", "actually", "really", "fine", "great", "done", "good",
    "here", "now", "time", "about", "going", "ready", "working", "looking", "not",
    "the", "yes", "no", "maybe", "today", "tomorrow", "tonight", "still", "back",
})

# A self-declared name is CLAIMED, not verified — a mid-strength source (above session-continuity,
# below an authenticated principal). Its confidence attenuates the obligation importance it later
# feeds (0.5 + 0.5·conf), so a claimed name binds moderately, not fully.
_DECLARED_NAME_CONFIDENCE = 0.5

_SPEAKER_SLOT = "speaker"       # working-memory slot the session's resolved speaker is parked in
# A carried-over identity is trusted a little less than the turn that established it: continuity
# says "probably still them", not "they just told me". One flat discount (no compounding — the
# stored value is only rewritten by a fresh explicit/declared resolution, never by a recall).
_CONTINUITY_DECAY = 0.8


def _inside_quote(before: str) -> bool:
    """True if a candidate at this position sits inside quoted / code-formatted text — a *mention*
    ('translate "my name is Alice"', `my name is Alice`), not a self-introduction (codex).

    An unbalanced double-quote, backtick, or smart-double run before the candidate opens a span.
    Single quotes are counted too, but only the *quote-like* ones: an apostrophe flanked by word
    chars on BOTH sides ("it's", "Alice's", "y'know") is a contraction/possessive and skipped, so
    a slang-free message with an open `'…` span (even with words between the quote and the cue)
    still registers as inside. A leftover leading opening mark (‘ « ‹) is caught by adjacency."""
    if (before.count('"') % 2 == 1 or before.count("`") % 2 == 1
            or before.count("“") > before.count("”")):
        return True
    singles = 0
    for i, ch in enumerate(before):
        if ch in "'‘’":
            flanked = (i > 0 and before[i - 1].isalnum()
                       and i + 1 < len(before) and before[i + 1].isalnum())
            if not flanked:                           # not a contraction/possessive apostrophe
                singles += 1
    if singles % 2 == 1:
        return True
    stripped = before.rstrip()
    return bool(stripped) and stripped[-1] in _OPEN_QUOTE_CHARS


def _parse_declared_name(message: str) -> Speaker | None:
    """The first plausible self-introduction in the turn as a ``declared_name`` Speaker, else None.

    Scans ALL cue matches (not just the first) so a non-name cue earlier in the turn — "it's
    Monday, my name is Alice" — does not suppress a real introduction later (codex). Each candidate
    must clear three guards before it counts as the speaker naming themselves: it must not sit
    inside quoted / code text (a mention, not a self-intro); a weak demonstrative cue ("it's"/"this
    is") is honoured only at the opener position; and the name must not run into a possessive
    ("Alice Smith's birthday" — a third party, not the speaker).
    """
    if not message:
        return None
    for m in _DECLARED_NAME_RE.finditer(message):
        before = message[:m.start()]
        if _inside_quote(before):
            continue                                  # quoted / code mention, not a self-intro
        if m.group("cue").lower() in _DEMONSTRATIVE_CUES and not _GREETING_ONLY_RE.match(before):
            continue                                  # weak opener only introduces at turn start
        if _TRAILING_POSSESSIVE_RE.match(message[m.end():]):
            continue                                  # "<Name> Something's …" — a third party's
        first = m.group("first")
        if first.lower() in _NOT_NAMES:
            continue
        last = m.group("last")
        name = f"{first} {last}" if (last and last.lower() not in _NOT_NAMES) else first
        return Speaker(id=name, confidence=_DECLARED_NAME_CONFIDENCE, source="declared_name")
    return None


async def _remember_speaker(runtime, session_id: str | None, speaker: Speaker) -> None:
    """Park the resolved speaker in this session's working memory so later turns in the same
    session can carry it forward. No ``session_id`` → nothing to key on; skip. Fail-open — a
    persistence hiccup never disturbs the turn."""
    if not session_id or speaker is None:
        return
    try:
        mem = getattr(runtime, "mem", None)
        if mem is None or not mem._config.working.enabled:
            return
        payload = json.dumps({"id": speaker.id, "confidence": speaker.confidence,
                              "source": speaker.source})
        await mem.working.set(session_id, runtime.agent_id, _SPEAKER_SLOT, payload, priority=2)
    except Exception:  # noqa: BLE001 — additive bookkeeping, never blocks
        log.warning("[chat] remember-speaker failed (non-fatal)", exc_info=True)


async def _recall_speaker(runtime, session_id: str | None) -> Speaker | None:
    """The last speaker parked for this session, carried forward at a decayed confidence and
    re-sourced as ``session_continuity``. None when there is none / working memory is off /
    anything errors. Fail-open."""
    if not session_id:
        return None
    try:
        mem = getattr(runtime, "mem", None)
        if mem is None or not mem._config.working.enabled:
            return None
        for slot in await mem.working.get(session_id, runtime.agent_id):
            if slot.slot != _SPEAKER_SLOT:
                continue
            data = json.loads(slot.content)
            sid = str(data.get("id") or "").strip()
            if not sid:
                return None
            conf = float(data.get("confidence", 0.0)) * _CONTINUITY_DECAY
            return Speaker(id=sid, confidence=max(0.0, min(1.0, conf)),
                           source="session_continuity")
    except Exception:  # noqa: BLE001
        log.warning("[chat] recall-speaker failed (non-fatal)", exc_info=True)
    return None


async def resolve_speaker(runtime, message: str, *, session_id: str | None = None,
                          explicit: Speaker | None = None) -> Speaker | None:
    """Resolve who is speaking this turn, strongest signal first. Called at the top of every
    ``converse`` / ``converse_stream`` turn (design note Phase 2), so every host gets speaker
    attribution with no host code.

    1. **Explicit** ``speaker=`` — a host that authenticated the caller passed one. Highest trust,
       short-circuits (design non-goal §10: never second-guess an authenticated principal).
    2. **Self-declared name** in this turn ("it's Dayyan") → ``declared_name``.
    3. **Session continuity** — the last speaker resolved for this ``session_id``, remembered in
       nmem working memory, carried forward at a decayed confidence → ``session_continuity``.

    Voice-session and text_style are deferred (Phase 3/5) and slot in between 1 and 2 by trust.
    Flag-gated (``NMEM_CHAT_SPEAKER_ENABLED``): while OFF this returns ``explicit`` untouched with
    no working-memory writes — byte-identical to Phase 1. Wholly fail-open: on any error it returns
    ``explicit`` and the turn proceeds anonymously. Returns a ``Speaker`` or None."""
    try:
        # Explicit override is honoured whether or not resolution is enabled (Phase 1). Only
        # persist it for continuity when resolution is on — otherwise no one reads it back.
        if explicit is not None:
            if speaker_resolution_enabled():
                await _remember_speaker(runtime, session_id, explicit)
            return explicit
        if not speaker_resolution_enabled():
            return None
        declared = _parse_declared_name(message)
        if declared is not None:
            await _remember_speaker(runtime, session_id, declared)
            return declared
        carried = await _recall_speaker(runtime, session_id)
        if carried is not None:
            return carried
    except Exception:  # noqa: BLE001 — resolution is a soft prior; never break the turn
        log.warning("[chat] resolve_speaker failed (non-fatal)", exc_info=True)
    return explicit
