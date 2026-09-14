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

import asyncio
import json
import logging
import os
import re
from contextlib import asynccontextmanager, nullcontext
from dataclasses import dataclass

log = logging.getLogger(__name__)

# Per-(agent, session) resolution gate. Overlapping turns of the SAME chat session must not
# interleave their read-modify-write of the author buffer / speaker slot: two concurrent turns
# can lose one's accumulated text, or let a stale fusion overwrite a newer identity (codex).
# Serialising the whole resolution per session makes it atomic within this process — the uncontended
# (normal, non-overlapping) case pays nothing. NOTE: working.get/set use separate DB sessions, so
# this is process-local; michelle/dj-twin run one event loop where that suffices. A future multi-
# worker deployment sharing the DB would additionally need a compare-and-swap on the buffer row (rev
# is the CAS token) — the in-flight staleness guard is the partial cross-process defence until then.
#
# The gate is USE-COUNTED rather than a lock table pruned by `locked()`: between a lock's release
# and its next waiter running, `locked()` is briefly False though a waiter is queued, so a prune
# `locked()` could drop a lock still in use and let two turns resolve concurrently (codex). Counting
# live users (holders + waiters) and dropping a guard only when the count hits zero has no such race
# and needs no size bound — the table holds at most the currently in-flight sessions.


class _SessionGuard:
    __slots__ = ("lock", "users")

    def __init__(self) -> None:
        self.lock = asyncio.Lock()
        self.users = 0


_session_guards: dict[str, _SessionGuard] = {}


@asynccontextmanager
async def _session_gate(agent_id: str, session_id: str):
    """Async context manager serialising resolution for one (agent, session). The guard is made on
    first use and dropped when its last user (holder or waiter) leaves — race-free under a single
    event loop (the user-count bump and the guard lookup run without an intervening await)."""
    key = f"{agent_id}\x00{session_id}"
    g = _session_guards.get(key)
    if g is None:
        g = _session_guards.setdefault(key, _SessionGuard())
    g.users += 1
    try:
        async with g.lock:
            yield
    finally:
        g.users -= 1
        if g.users <= 0 and _session_guards.get(key) is g:
            del _session_guards[key]


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


def text_identity_enabled() -> bool:
    """Flag gate for the Phase-5 text_style fusion (author-vector recognition via the nmem-identity
    ``/embed`` + ``/resolve`` sidecars). Default OFF, and additionally inert unless the endpoints
    are configured (``NMEM_IDENTITY_TEXT_EMBED_URL`` / ``NMEM_IDENTITY_MATCHER_URL``) — so turning
    the flag on without a running identity service still leaves the chat path on the Phase-2 local
    resolution. Requires ``NMEM_CHAT_SPEAKER_ENABLED`` too: text fusion layers ON the speaker slot,
    it does not replace it."""
    val = os.getenv("NMEM_CHAT_TEXT_IDENTITY_ENABLED", "").strip().lower()
    return val in ("1", "true", "yes", "on")


def person_alias_enabled() -> bool:
    """Flag gate for the Phase-6-A dossier convergence (person-alias layer). Default OFF. Governs
    BOTH ends of the additive alias: the session-summary SEED (record a name↔id alias) and the
    obligation gate's READ union (expand a requester's deference lookup over its aliases). While
    off, no alias is written and the deference read uses only the literal ref — Phase 6-B intact."""
    val = os.getenv("NMEM_CHAT_PERSON_ALIAS_ENABLED", "").strip().lower()
    return val in ("1", "true", "yes", "on")


# The author signal is gathered on a CADENCE, not every turn (design open-Q §11.5 recommendation):
# accumulate authored chat text per session and only embed+resolve once enough has built up — LUAR
# is episodic and unreliable on one short turn, and a per-turn HTTP round-trip would tax the path.
# Underscore-prefixed → internal bookkeeping the working-memory tier keeps OUT of the prompt and the
# journal flush (this is raw accumulated chat text, never model-facing context).
_TEXT_BUFFER_SLOT = "_text_identity_buffer"     # working-memory slot: the session's authored turns
_TEXT_EPISODE_LEN = 16                           # keep the last N authored turns (matches sidecar)


def _text_min_chars() -> int:
    """Authored chars that must accumulate for this session before the text signal fires again."""
    try:
        return max(1, int(os.getenv("NMEM_CHAT_TEXT_IDENTITY_MIN_CHARS", "400") or "400"))
    except ValueError:
        return 400


# How a local (Phase-2) resolution feeds fusion as a contextual PRIOR: (prior log-odds, is-strong).
# `strong` (an authenticated principal, or a real concurrent voice session) is what lets fusion hit
# *confident* grounding — a merely CLAIMED name or bare continuity is a prior, never corroboration.
_SOURCE_PRIOR: dict[str, tuple[float, bool]] = {
    "principal": (4.0, True),
    "voice_session": (2.0, True),
    "declared_name": (1.2, False),
    "session_continuity": (0.5, False),
}


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


async def _accumulate_text(runtime, session_id: str | None, message: str, *,
                           local_id: str | None = None) -> list[str] | None:
    """Append this turn's text to the session's authored-turn buffer and decide whether the text
    signal should FIRE this turn. Returns the buffered turns (an episode) when enough authored text
    has accumulated since the last fire, else None (still accumulating). The buffer lives in working
    memory (already a core dependency); fail-open — any hiccup returns None (no signal this turn).

    Cadence, not per-turn: the char counter resets on a fire, so the next signal waits for another
    ``_text_min_chars()`` of authored text. ``local_id`` is the currently-known speaker; when it
    changes to a DIFFERENT known identity (Alice was speaking, Bob now introduces himself), the
    previous author's turns are dropped so one author's writing never contaminates another's episode
    (codex). Paste-awareness spans are not threaded through ``converse`` yet, so v0 treats the whole
    turn as authored — the nmem-identity sidecar still applies its own sufficiency /
    heuristic-contamination gate on what it receives."""
    text = (message or "").strip()
    if not session_id or not text:
        return None
    try:
        mem = getattr(runtime, "mem", None)
        if mem is None or not mem._config.working.enabled:
            return None
        turns: list[str] = []
        chars = 0
        buf_speaker: str | None = None
        rev = 0
        # include_internal=True: the buffer is a "_"-prefixed internal slot get() hides from every
        # model-facing / durable consumer — the owning subsystem is the only reader.
        for slot in await mem.working.get(session_id, runtime.agent_id, include_internal=True):
            if slot.slot == _TEXT_BUFFER_SLOT:
                data = json.loads(slot.content)
                turns = [str(t) for t in (data.get("turns") or [])]
                chars = int(data.get("chars", 0))
                buf_speaker = data.get("speaker_id")
                rev = int(data.get("rev", 0))
                break
        # A known-speaker change resets the episode — never mix two authors' text into one vector.
        if local_id and buf_speaker and local_id != buf_speaker:
            turns, chars = [], 0
        turns = (turns + [text])[-_TEXT_EPISODE_LEN:]
        chars += len(text)
        fire = chars >= _text_min_chars()
        # `rev` bumps on every accumulation write — a monotone session-turn revision. A fusion call
        # captures the rev it fired at; if a later overlapping turn bumps it before the slow fusion
        # returns, the stale response is discarded rather than clobbering newer state (codex).
        rev += 1
        payload = json.dumps({"turns": turns, "chars": 0 if fire else chars,
                              "speaker_id": local_id or buf_speaker, "rev": rev})
        await mem.working.set(session_id, runtime.agent_id, _TEXT_BUFFER_SLOT, payload, priority=1)
        return (turns, rev) if fire else None
    except Exception:  # noqa: BLE001 — accumulation is additive bookkeeping, never blocks the turn
        log.warning("[chat] text accumulate failed (non-fatal)", exc_info=True)
        return None


async def _buffer_rev(runtime, session_id: str | None) -> int:
    """The author buffer's current revision (0 if no buffer / unavailable). Detects whether an
    overlapping turn advanced the session while a fusion request was in flight. Fail-open — on any
    error returns -1 so the caller treats the response as stale and discards it (safe default)."""
    if not session_id:
        return 0
    try:
        mem = getattr(runtime, "mem", None)
        if mem is None or not mem._config.working.enabled:
            return 0
        for slot in await mem.working.get(session_id, runtime.agent_id, include_internal=True):
            if slot.slot == _TEXT_BUFFER_SLOT:
                return int(json.loads(slot.content).get("rev", 0))
        return 0
    except Exception:  # noqa: BLE001
        log.warning("[chat] buffer-rev read failed (non-fatal)", exc_info=True)
        return -1


async def _set_buffer_owner(runtime, session_id: str | None, owner_id: str | None) -> None:
    """Stamp the author buffer with the RECOGNISED author id (after fusion), so the next turn's
    change-detection is anchored to who we now believe is speaking. When fusion identifies a
    *different* author than the buffer already held, also DROP the accumulated turns — otherwise the
    prior author's text keeps riding along in the episode (no declared-name change fires the
    _accumulate_text reset), contaminating later recognition and learning (codex). No-op if there is
    no buffer / no session / it already names this owner. Fail-open — bookkeeping never blocks."""
    if not session_id or not owner_id:
        return
    try:
        mem = getattr(runtime, "mem", None)
        if mem is None or not mem._config.working.enabled:
            return
        for slot in await mem.working.get(session_id, runtime.agent_id, include_internal=True):
            if slot.slot == _TEXT_BUFFER_SLOT:
                data = json.loads(slot.content)
                prev = data.get("speaker_id")
                if prev == owner_id:
                    return                            # already anchored — no redundant write
                data["speaker_id"] = owner_id
                if prev is not None:                  # KNOWN author changed → start a clean episode
                    data["turns"] = []
                    data["chars"] = 0
                await mem.working.set(session_id, runtime.agent_id, _TEXT_BUFFER_SLOT,
                                      json.dumps(data), priority=1)
                return
    except Exception:  # noqa: BLE001 — additive bookkeeping, never blocks
        log.warning("[chat] set-buffer-owner failed (non-fatal)", exc_info=True)


def _local_context(local: Speaker | None) -> list[dict]:
    """The Phase-2 local resolution as a fusion context prior (a claimed name / continuity /
    principal toward a candidate), or empty when there is none / the source has no prior weight."""
    if local is None:
        return []
    prior = _SOURCE_PRIOR.get(local.source)
    if prior is None:
        return []
    log_odds, strong = prior
    sig = {"kind": local.source, "log_odds": log_odds, "strong": strong}
    # A canonical candidate key (cluster:<id> / person:<id>, e.g. a carried-forward fused identity)
    # is echoed back as `candidate` so text + continuity evidence converge on the SAME identity; a
    # human name goes as `name` for the matcher to resolve to a person (codex).
    if local.id.startswith(("cluster:", "person:")):
        sig["candidate"] = local.id
    else:
        sig["name"] = local.id
    return [sig]


async def _resolve_text_style(runtime, message: str, *, session_id: str | None,
                              local: Speaker | None) -> Speaker | None:
    """The Phase-5 text_style signal: on a cadence, embed the accumulated authored turns and fuse
    the author vote with the local resolution (as a prior) into a calibrated identity. Returns a
    ``text_style``/fused Speaker when the identity service grounds one, else None (fall back to
    ``local``). Wholly fail-open and inert unless BOTH identity endpoints are configured."""
    from . import identity_client                # lazy: importing speaker must not require httpx
    if not identity_client.endpoints_configured():
        return None                              # inert without both endpoints — no buffer writes
    acc = await _accumulate_text(runtime, session_id, message,
                                 local_id=(local.id if local else None))
    if not acc:                                  # still accumulating, or accumulation unavailable
        return None
    turns, fire_rev = acc
    emb = await identity_client.embed_documents(turns)
    if not emb or "embedding" not in emb:            # below the sidecar's authored-length floor
        return None
    res = await identity_client.resolve(
        text_embedding=emb["embedding"], text_length=emb.get("authored_chars"),
        text_quality=emb.get("quality"), text_learn=True, session_id=session_id,
        context=_local_context(local))
    if not res:
        return None
    sp = res.get("speaker")
    if not isinstance(sp, dict) or not str(sp.get("id") or "").strip():
        return None                              # fusion grounded nothing (or below speculative)
    try:
        conf = max(0.0, min(1.0, float(sp.get("confidence", 0.0))))
    except (TypeError, ValueError):
        conf = 0.0
    fused = Speaker(id=str(sp["id"]).strip(), confidence=conf,
                    source=str(sp.get("source") or "text_style").strip() or "text_style")
    # Staleness guard: if an overlapping turn advanced the session buffer while this (slow) fusion
    # was in flight, its rev no longer matches — discard this response rather than overwrite the
    # newer speaker or clear the newer author's text (codex). Optimistic check; a soft prior, so a
    # rare residual race self-corrects on the next non-overlapping turn.
    if await _buffer_rev(runtime, session_id) != fire_rev:
        return None
    # Anchor the episode to the RECOGNISED author. _accumulate_text stamped the buffer with the
    # pre-fusion local id (often None on first sighting); once fusion names the author, record it so
    # a later change to a DIFFERENT author triggers the reset — else an unnamed→named→other-author
    # sequence would keep mixing text under a stale/absent owner (codex).
    await _set_buffer_owner(runtime, session_id, fused.id)
    return fused


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

    4. **Text_style fusion** (Phase 5, flag-gated ``NMEM_CHAT_TEXT_IDENTITY_ENABLED``) — on a
       cadence, the accumulated authored text is embedded and fused with the above as a prior into a
       calibrated identity via the nmem-identity sidecars. It LAYERS on the local result: a grounded
       fusion wins, otherwise the local resolution stands. text is a weak vote (the matcher never
       lets it authorise alone), so this only sharpens or corroborates who we thought was speaking.

    Flag-gated (``NMEM_CHAT_SPEAKER_ENABLED``): while OFF this returns ``explicit`` untouched with
    no working-memory writes — byte-identical to Phase 1. Wholly fail-open: on any error it returns
    ``explicit`` and the turn proceeds anonymously. Returns a ``Speaker`` or None."""
    try:
        # Serialise this resolution per (agent, session) so overlapping turns of the same session
        # can't interleave their buffer / speaker read-modify-writes (codex). Uncontended (free)
        # in the normal non-overlapping case. No session_id → nothing to serialise against.
        gate = _session_gate(runtime.agent_id, session_id) if session_id else nullcontext()
        async with gate:
            # Explicit override is honoured whether or not resolution is enabled (Phase 1). An
            # authenticated principal is never second-guessed, so text fusion does not run over it.
            # Persist it for continuity only when resolution is on — otherwise no one reads it back.
            if explicit is not None:
                if speaker_resolution_enabled():
                    await _remember_speaker(runtime, session_id, explicit)
                return explicit
            if not speaker_resolution_enabled():
                return None
            # Phase-2 local resolution: self-declared name, then session continuity.
            declared = _parse_declared_name(message)
            if declared is not None:
                await _remember_speaker(runtime, session_id, declared)
                local = declared
            else:
                local = await _recall_speaker(runtime, session_id)
            # Phase-5: layer text_style fusion on top (additive; inert unless enabled+configured).
            # A grounded fusion supersedes the local one, carried forward for continuity. Its own
            # try/except keeps an optional-feature failure from discarding the Phase-2 `local`:
            # is a soft add-on, never allowed to lose an already-resolved declared/continuity id.
            if text_identity_enabled():
                try:
                    fused = await _resolve_text_style(
                        runtime, message, session_id=session_id, local=local)
                except Exception:  # noqa: BLE001 — fusion is additive; fall back to local
                    log.warning("[chat] text_style fusion failed (non-fatal)", exc_info=True)
                    fused = None
                if fused is not None:
                    await _remember_speaker(runtime, session_id, fused)
                    return fused
            return local
    except Exception:  # noqa: BLE001 — resolution is a soft prior; never break the turn
        log.warning("[chat] resolve_speaker failed (non-fatal)", exc_info=True)
    return explicit
