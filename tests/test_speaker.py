"""Speaker resolution — the Phase-2 in-``converse`` resolver (design note
``nmem-identity-chat-style-modality``).

Pure unit tests over fakes (no Postgres): the ``Speaker`` value object, self-declared-name
parsing, and the flag-gated, fail-open ``resolve_speaker`` precedence (explicit → declared name →
session continuity) with a tiny in-memory working-memory tier.
"""
import json
from types import SimpleNamespace

import pytest

from nmem.agent_core import speaker as sp

# ── Speaker value object ─────────────────────────────────────────────────────────

def test_speaker_from_dict_variants():
    assert sp.Speaker.from_dict(None) is None
    assert sp.Speaker.from_dict("nope") is None                  # non-dict
    assert sp.Speaker.from_dict({"id": "   "}) is None            # blank id
    s = sp.Speaker.from_dict({"id": "dayyan", "confidence": 5, "source": "principal"})
    assert s.id == "dayyan" and s.confidence == 1.0 and s.source == "principal"    # clamped
    bad = sp.Speaker.from_dict({"id": "dayyan", "confidence": "x"})
    assert bad.confidence == 0.0 and bad.source == "unknown"      # bad conf → floor, default source


def test_speaker_reexported_from_obligation_extraction():
    # Back-compat: obligation_extraction re-exports the moved value object.
    from nmem.agent_core import obligation_extraction as oe
    assert oe.Speaker is sp.Speaker


# ── self-declared name parsing ────────────────────────────────────────────────────

@pytest.mark.parametrize("text,expected", [
    ("it's Dayyan", "Dayyan"),
    ("This is Dayyan, can you help?", "Dayyan"),
    ("hi, I'm Dayyan here", "Dayyan"),
    ("my name is Dayyan Smith", "Dayyan Smith"),
    ("call me Dayyan", "Dayyan"),
    ("I don't know yet — my name is Dayyan", "Dayyan"),   # contraction ≠ open quote (regression)
    ("I said 'yes' earlier — my name is Dayyan", "Dayyan"),  # balanced single quotes → not inside
    ("My name is Alice\nCan you send it by tomorrow?", "Alice"),  # codex: no cross-line surname
])
def test_parse_declared_name_hits(text, expected):
    s = sp._parse_declared_name(text)
    assert s is not None and s.id == expected
    assert s.source == "declared_name" and s.confidence == sp._DECLARED_NAME_CONFIDENCE


@pytest.mark.parametrize("text", [
    "",
    "what is pgvector?",              # no cue
    "it's raining outside",          # cue but lowercase word — not a name
    "this is great news",            # stoplist: Great
    "it's Monday already",           # stoplist: Monday
    "I'm SORRY about that",          # ALL-CAPS rejected by the capital-then-lowercase rule
    "it's PostgreSQL",               # codex: internal capital → reject, don't truncate to "Postgre"
    "I'm McDonald",                  # codex: internal capital → reject, don't truncate to "Mc"
    "it's Agent007",                 # trailing digits → not a whole-token name
    'Please translate "my name is Alice" into French.',   # codex: quoted mention, not a self-intro
    "It's Alice's birthday; remind me tomorrow.",         # codex: possessive → fails the boundary
    "Is this issue in Python? Yes, it is Python.",         # codex: demonstrative mid-turn = a thing
    "the winner, it's Dayyan",       # demonstrative mid-turn → not honoured (only at the opener)
    "Please translate `my name is Alice` into French by tomorrow.",   # codex: backtick code span
    "Please translate 'my name is Alice and I live in Paris' by tomorrow.",  # codex: single-quoted
    "Please translate 'Hello, my name is Alice, I live in Paris' now.",   # codex: non-adjacent
    '"Well, my name is Alice," she said',                 # codex: non-adjacent double-quote wrap
    "It's Alice Smith's birthday; please draft a greeting.",   # codex: possessive full name
])
def test_parse_declared_name_misses(text):
    assert sp._parse_declared_name(text) is None


def test_parse_declared_name_skips_earlier_non_name():
    # codex: a non-name cue earlier in the turn must not suppress a real introduction later.
    s = sp._parse_declared_name("it's Monday; my name is Alice.")
    assert s is not None and s.id == "Alice" and s.source == "declared_name"


def test_parse_declared_name_greeting_opener_ok():
    # A demonstrative cue is honoured after a bare greeting (still the opener), just not mid-turn.
    s = sp._parse_declared_name("hey, it's Dayyan")
    assert s is not None and s.id == "Dayyan"


# ── working-memory fake ───────────────────────────────────────────────────────────

class _Working:
    """Minimal stand-in for the working-memory tier: set/get over an in-memory slot dict."""
    def __init__(self): self.slots = {}
    async def set(self, session_id, agent_id, slot, content, priority=5, **kw):
        self.slots[(session_id, agent_id, slot)] = content
    async def get(self, session_id, agent_id):
        return [SimpleNamespace(slot=s, content=c)
                for (sess, ag, s), c in self.slots.items() if sess == session_id and ag == agent_id]


def _runtime(working=None, working_enabled=True):
    cfg = SimpleNamespace(working=SimpleNamespace(enabled=working_enabled))
    mem = SimpleNamespace(working=working if working is not None else _Working(), _config=cfg)
    return SimpleNamespace(mem=mem, agent_id="agent-a")


# ── resolve_speaker: flag gate ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_resolve_disabled_returns_explicit_no_writes(monkeypatch):
    monkeypatch.setenv("NMEM_CHAT_SPEAKER_ENABLED", "0")
    w = _Working()
    explicit = sp.Speaker(id="dayyan", confidence=1.0, source="principal")
    out = await sp.resolve_speaker(_runtime(w), "it's Someone Else", session_id="s1",
                                   explicit=explicit)
    assert out is explicit               # explicit honoured, declared name ignored while off
    assert w.slots == {}                 # no working-memory writes while off — byte-identical


@pytest.mark.asyncio
async def test_resolve_disabled_no_explicit_is_none(monkeypatch):
    monkeypatch.setenv("NMEM_CHAT_SPEAKER_ENABLED", "0")
    out = await sp.resolve_speaker(_runtime(), "it's Dayyan", session_id="s1", explicit=None)
    assert out is None                   # pre-Phase-2 behaviour: no speaker


# ── resolve_speaker: precedence when enabled ────────────────────────────────────────

@pytest.mark.asyncio
async def test_resolve_explicit_wins_and_persists(monkeypatch):
    monkeypatch.setenv("NMEM_CHAT_SPEAKER_ENABLED", "1")
    w = _Working()
    explicit = sp.Speaker(id="dayyan", confidence=1.0, source="principal")
    out = await sp.resolve_speaker(_runtime(w), "my name is Someone Else", session_id="s1",
                                   explicit=explicit)
    assert out is explicit               # explicit beats the self-declared name in the turn
    parked = json.loads(w.slots[("s1", "agent-a", sp._SPEAKER_SLOT)])
    assert parked["id"] == "dayyan" and parked["source"] == "principal"   # persisted for continuity


@pytest.mark.asyncio
async def test_resolve_declared_name_when_no_explicit(monkeypatch):
    monkeypatch.setenv("NMEM_CHAT_SPEAKER_ENABLED", "1")
    w = _Working()
    out = await sp.resolve_speaker(_runtime(w), "hey, it's Dayyan", session_id="s1", explicit=None)
    assert out.id == "Dayyan" and out.source == "declared_name"
    assert json.loads(w.slots[("s1", "agent-a", sp._SPEAKER_SLOT)])["id"] == "Dayyan"


@pytest.mark.asyncio
async def test_resolve_session_continuity_decays(monkeypatch):
    monkeypatch.setenv("NMEM_CHAT_SPEAKER_ENABLED", "1")
    w = _Working()
    rt = _runtime(w)
    # Turn 1 establishes the speaker by name; turn 2 carries no cue → continuity.
    await sp.resolve_speaker(rt, "it's Dayyan", session_id="s1", explicit=None)
    carried = await sp.resolve_speaker(rt, "ok great, what's next?", session_id="s1", explicit=None)
    assert carried.id == "Dayyan" and carried.source == "session_continuity"
    # decayed below the declared confidence it was stored at
    assert carried.confidence == pytest.approx(sp._DECLARED_NAME_CONFIDENCE * sp._CONTINUITY_DECAY)


@pytest.mark.asyncio
async def test_resolve_no_session_no_continuity(monkeypatch):
    monkeypatch.setenv("NMEM_CHAT_SPEAKER_ENABLED", "1")
    w = _Working()
    # No session_id → nothing to key continuity on, and nothing persisted.
    out = await sp.resolve_speaker(_runtime(w), "just a plain message", session_id=None,
                                   explicit=None)
    assert out is None and w.slots == {}


@pytest.mark.asyncio
async def test_resolve_working_disabled_is_fail_open(monkeypatch):
    monkeypatch.setenv("NMEM_CHAT_SPEAKER_ENABLED", "1")
    # Working memory off → a declared name still resolves this turn (just not persisted).
    out = await sp.resolve_speaker(_runtime(working_enabled=False), "it's Dayyan",
                                   session_id="s1", explicit=None)
    assert out.id == "Dayyan" and out.source == "declared_name"


@pytest.mark.asyncio
async def test_resolve_mem_none_is_fail_open(monkeypatch):
    monkeypatch.setenv("NMEM_CHAT_SPEAKER_ENABLED", "1")
    rt = SimpleNamespace(mem=None, agent_id="agent-a")
    # A declared name has no mem to persist to, but resolution itself must not raise.
    out = await sp.resolve_speaker(rt, "it's Dayyan", session_id="s1", explicit=None)
    assert out.id == "Dayyan"
    # And a continuity-only turn with no mem simply yields nothing.
    assert await sp.resolve_speaker(rt, "plain turn", session_id="s1", explicit=None) is None
