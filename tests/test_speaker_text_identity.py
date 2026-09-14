"""Phase-5 text_style fusion wiring in resolve_speaker — the cadence accumulator and the fusion
layer, exercised without the identity service (the client is monkeypatched). The identity-side
fusion math itself lives in nmem-identity's test_fuse / test_calibrate; here we prove the chat path
accumulates on a cadence, threads the local resolution as a prior, uses the fused Speaker, and stays
byte-identical to Phase 2 when the flag is off."""
import json
from types import SimpleNamespace

import pytest

from nmem.agent_core import identity_client
from nmem.agent_core import speaker as sp


class _Working:
    """Minimal working-memory tier stand-in (mirrors test_speaker._Working)."""
    def __init__(self):
        self.slots = {}

    async def set(self, session_id, agent_id, slot, content, priority=5, **kw):
        self.slots[(session_id, agent_id, slot)] = content

    async def get(self, session_id, agent_id, *, include_internal=False):
        return [SimpleNamespace(slot=s, content=c)
                for (sess, ag, s), c in self.slots.items()
                if sess == session_id and ag == agent_id
                and (include_internal or not s.startswith("_"))]


def _runtime(working=None, working_enabled=True):
    cfg = SimpleNamespace(working=SimpleNamespace(enabled=working_enabled))
    mem = SimpleNamespace(working=working if working is not None else _Working(), _config=cfg)
    return SimpleNamespace(mem=mem, agent_id="agent-a")


def _buffer(w, sid, agent="agent-a"):
    raw = w.slots.get((sid, agent, sp._TEXT_BUFFER_SLOT))
    return json.loads(raw) if raw else None


@pytest.fixture
def endpoints(monkeypatch):
    """Configure both identity endpoints so endpoints_configured() is True (HTTP is never sent —
    the client's embed/resolve are faked by spy_identity)."""
    monkeypatch.setenv("NMEM_IDENTITY_TEXT_EMBED_URL", "http://identity:9406")
    monkeypatch.setenv("NMEM_IDENTITY_MATCHER_URL", "http://identity:9404")


@pytest.fixture
def spy_identity(monkeypatch):
    """Fake the identity client; record embed/resolve calls and return a canned fused speaker."""
    calls = {"embed": [], "resolve": []}

    async def fake_embed(documents, *, pasted_ratio=None):
        calls["embed"].append(list(documents))
        return {"embedding": [0.1] * 512, "authored_chars": 500, "quality": 0.8,
                "regime": "text_authentic"}

    async def fake_resolve(*, text_embedding, text_length, text_quality, text_learn,
                           session_id, context):
        calls["resolve"].append({"len": text_length, "learn": text_learn, "context": context})
        return {"speaker": {"id": "Dayyan", "confidence": 0.71, "source": "text_style"},
                "grounding": "speculative", "authorize_ok": False}

    monkeypatch.setattr(identity_client, "embed_documents", fake_embed)
    monkeypatch.setattr(identity_client, "resolve", fake_resolve)
    return calls


_LONG = "I have been thinking a great deal about the architecture and the tradeoffs here today"


@pytest.mark.asyncio
async def test_below_floor_accumulates_no_call(monkeypatch, spy_identity, endpoints):
    monkeypatch.setenv("NMEM_CHAT_SPEAKER_ENABLED", "1")
    monkeypatch.setenv("NMEM_CHAT_TEXT_IDENTITY_ENABLED", "1")
    monkeypatch.setenv("NMEM_CHAT_TEXT_IDENTITY_MIN_CHARS", "50")
    w = _Working()
    out = await sp.resolve_speaker(_runtime(w), "hi there", session_id="s1")
    assert out is None                                   # nothing local, text below floor
    assert spy_identity["embed"] == []                   # did not fire
    buf = _buffer(w, "s1")
    assert buf["turns"] == ["hi there"] and buf["chars"] == len("hi there")


@pytest.mark.asyncio
async def test_cadence_fires_and_returns_fused_speaker(monkeypatch, spy_identity, endpoints):
    monkeypatch.setenv("NMEM_CHAT_SPEAKER_ENABLED", "1")
    monkeypatch.setenv("NMEM_CHAT_TEXT_IDENTITY_ENABLED", "1")
    monkeypatch.setenv("NMEM_CHAT_TEXT_IDENTITY_MIN_CHARS", "50")
    w = _Working()
    await sp.resolve_speaker(_runtime(w), "hi there", session_id="s1")     # accumulate
    out = await sp.resolve_speaker(_runtime(w), _LONG, session_id="s1")    # crosses floor → fire
    assert out is not None and out.id == "Dayyan" and out.source == "text_style"
    assert out.confidence == pytest.approx(0.71)
    assert spy_identity["embed"][0] == ["hi there", _LONG]                 # episode = the turns
    assert spy_identity["resolve"][0]["learn"] is True                    # text channel learns
    assert _buffer(w, "s1")["chars"] == 0                                 # counter resets on fire
    # The fused speaker is carried forward for continuity, AND the buffer is re-anchored to the
    # recognised author (codex R4) so a later author change triggers the episode reset.
    assert w.slots.get(("s1", "agent-a", sp._SPEAKER_SLOT)) is not None
    assert _buffer(w, "s1")["speaker_id"] == "Dayyan"


@pytest.mark.asyncio
async def test_declared_name_threaded_as_weak_prior(monkeypatch, spy_identity, endpoints):
    monkeypatch.setenv("NMEM_CHAT_SPEAKER_ENABLED", "1")
    monkeypatch.setenv("NMEM_CHAT_TEXT_IDENTITY_ENABLED", "1")
    monkeypatch.setenv("NMEM_CHAT_TEXT_IDENTITY_MIN_CHARS", "10")
    w = _Working()
    await sp.resolve_speaker(_runtime(w), "my name is Dayyan. " + _LONG, session_id="s2")
    ctx = spy_identity["resolve"][0]["context"]
    assert ctx and ctx[0]["kind"] == "declared_name" and ctx[0]["name"] == "Dayyan"
    assert ctx[0]["strong"] is False                     # a claimed name is a prior, not proof


@pytest.mark.asyncio
async def test_fusion_none_falls_back_to_local(monkeypatch, endpoints):
    # When fusion grounds nothing (client returns None), the local declared name still stands.
    monkeypatch.setenv("NMEM_CHAT_SPEAKER_ENABLED", "1")
    monkeypatch.setenv("NMEM_CHAT_TEXT_IDENTITY_ENABLED", "1")
    monkeypatch.setenv("NMEM_CHAT_TEXT_IDENTITY_MIN_CHARS", "10")

    async def none_embed(documents, *, pasted_ratio=None):
        return None                                      # below the sidecar floor / service down

    monkeypatch.setattr(identity_client, "embed_documents", none_embed)
    w = _Working()
    out = await sp.resolve_speaker(_runtime(w), "my name is Alice. " + _LONG, session_id="s3")
    assert out is not None and out.id == "Alice" and out.source == "declared_name"


@pytest.mark.asyncio
async def test_flag_off_is_phase2_identical(monkeypatch, spy_identity):
    monkeypatch.setenv("NMEM_CHAT_SPEAKER_ENABLED", "1")
    monkeypatch.setenv("NMEM_CHAT_TEXT_IDENTITY_ENABLED", "0")     # text fusion off
    w = _Working()
    out = await sp.resolve_speaker(_runtime(w), "my name is Alice", session_id="s4")
    assert out is not None and out.id == "Alice" and out.source == "declared_name"
    assert spy_identity["embed"] == [] and spy_identity["resolve"] == []   # no identity traffic
    assert _buffer(w, "s4") is None                                        # no text-buffer writes


@pytest.mark.asyncio
async def test_explicit_principal_not_second_guessed(monkeypatch, spy_identity, endpoints):
    monkeypatch.setenv("NMEM_CHAT_SPEAKER_ENABLED", "1")
    monkeypatch.setenv("NMEM_CHAT_TEXT_IDENTITY_ENABLED", "1")
    monkeypatch.setenv("NMEM_CHAT_TEXT_IDENTITY_MIN_CHARS", "10")
    w = _Working()
    explicit = sp.Speaker(id="dayyan", confidence=1.0, source="principal")
    out = await sp.resolve_speaker(_runtime(w), _LONG, session_id="s5", explicit=explicit)
    assert out is explicit                               # authenticated principal wins, no fusion
    assert spy_identity["embed"] == [] and spy_identity["resolve"] == []


@pytest.mark.asyncio
async def test_inert_without_endpoints(monkeypatch, spy_identity):
    # Flag ON but endpoints UNSET → inert: no accumulation, no HTTP, no buffer writes (codex F6).
    monkeypatch.delenv("NMEM_IDENTITY_TEXT_EMBED_URL", raising=False)
    monkeypatch.delenv("NMEM_IDENTITY_MATCHER_URL", raising=False)
    monkeypatch.setenv("NMEM_CHAT_SPEAKER_ENABLED", "1")
    monkeypatch.setenv("NMEM_CHAT_TEXT_IDENTITY_ENABLED", "1")
    monkeypatch.setenv("NMEM_CHAT_TEXT_IDENTITY_MIN_CHARS", "10")
    w = _Working()
    await sp.resolve_speaker(_runtime(w), _LONG, session_id="s6")
    assert spy_identity["embed"] == [] and spy_identity["resolve"] == []
    assert _buffer(w, "s6") is None                       # no buffer writes without endpoints


@pytest.mark.asyncio
async def test_fusion_exception_preserves_local(monkeypatch, endpoints):
    # An exception in the optional fusion step must NOT discard the already-resolved local speaker
    # (codex N2): the declared name survives a fusion blow-up rather than resolving anonymously.
    monkeypatch.setenv("NMEM_CHAT_SPEAKER_ENABLED", "1")
    monkeypatch.setenv("NMEM_CHAT_TEXT_IDENTITY_ENABLED", "1")
    monkeypatch.setenv("NMEM_CHAT_TEXT_IDENTITY_MIN_CHARS", "10")

    async def boom_embed(documents, *, pasted_ratio=None):
        raise RuntimeError("embed exploded")

    monkeypatch.setattr(identity_client, "embed_documents", boom_embed)
    w = _Working()
    out = await sp.resolve_speaker(_runtime(w), "my name is Alice. " + _LONG, session_id="s8")
    assert out is not None and out.id == "Alice" and out.source == "declared_name"


@pytest.mark.asyncio
async def test_stale_fusion_response_is_discarded(monkeypatch, endpoints):
    # An overlapping newer turn advances the buffer rev while a slow fusion is in flight; the stale
    # response must be discarded, not overwrite the newer state or clear its text (codex).
    monkeypatch.setenv("NMEM_CHAT_SPEAKER_ENABLED", "1")
    monkeypatch.setenv("NMEM_CHAT_TEXT_IDENTITY_ENABLED", "1")
    monkeypatch.setenv("NMEM_CHAT_TEXT_IDENTITY_MIN_CHARS", "10")
    w = _Working()

    async def fake_embed(documents, *, pasted_ratio=None):
        return {"embedding": [0.1] * 512, "authored_chars": 500, "quality": 0.8}

    async def racing_resolve(*, text_embedding, text_length, text_quality, text_learn,
                             session_id, context):
        # Simulate an overlapping turn bumping the session buffer mid-flight.
        buf = _buffer(w, "sr")
        buf["rev"] = buf["rev"] + 5
        await w.set("sr", "agent-a", sp._TEXT_BUFFER_SLOT, json.dumps(buf), priority=1)
        return {"speaker": {"id": "Alice", "confidence": 0.7, "source": "text_style"},
                "grounding": "speculative", "authorize_ok": False}

    monkeypatch.setattr(identity_client, "embed_documents", fake_embed)
    monkeypatch.setattr(identity_client, "resolve", racing_resolve)
    out = await sp.resolve_speaker(_runtime(w), "my name is Bob. " + _LONG, session_id="sr")
    # Stale Alice discarded → local declared Bob stands, and the buffer is NOT re-owned by Alice.
    assert out is not None and out.id == "Bob"
    assert _buffer(w, "sr")["speaker_id"] != "Alice"


@pytest.mark.asyncio
async def test_set_buffer_owner_change_resets_turns():
    # When fusion identifies a DIFFERENT author than the buffer held, the prior author's turns are
    # dropped (codex R4) — but the FIRST identification (prev owner None) keeps the current turns.
    w = _Working()
    rt = _runtime(w)
    await w.set("s9", "agent-a", sp._TEXT_BUFFER_SLOT,
                json.dumps({"turns": ["alice txt"], "chars": 9, "speaker_id": "Alice"}), priority=1)
    await sp._set_buffer_owner(rt, "s9", "Bob")
    buf = _buffer(w, "s9")
    assert buf["speaker_id"] == "Bob" and buf["turns"] == [] and buf["chars"] == 0

    await w.set("s10", "agent-a", sp._TEXT_BUFFER_SLOT,
                json.dumps({"turns": ["x"], "chars": 1, "speaker_id": None}), priority=1)
    await sp._set_buffer_owner(rt, "s10", "Alice")
    buf2 = _buffer(w, "s10")
    assert buf2["speaker_id"] == "Alice" and buf2["turns"] == ["x"]   # first ID keeps the turns


@pytest.mark.asyncio
async def test_speaker_change_resets_episode(monkeypatch, spy_identity, endpoints):
    # Alice accumulates, then Bob introduces himself in the same session: the episode must drop
    # Alice's text so one author's writing never contaminates another's author vector (codex F5).
    monkeypatch.setenv("NMEM_CHAT_SPEAKER_ENABLED", "1")
    monkeypatch.setenv("NMEM_CHAT_TEXT_IDENTITY_ENABLED", "1")
    monkeypatch.setenv("NMEM_CHAT_TEXT_IDENTITY_MIN_CHARS", "100000")   # never fire; inspect buffer
    w = _Working()
    await sp.resolve_speaker(_runtime(w), "my name is Alice. early alice notes", session_id="s7")
    assert _buffer(w, "s7")["speaker_id"] == "Alice"
    await sp.resolve_speaker(_runtime(w), "my name is Bob. bob speaks now instead", session_id="s7")
    buf = _buffer(w, "s7")
    assert buf["speaker_id"] == "Bob"
    assert all("alice" not in t.lower() for t in buf["turns"])          # Alice's turn dropped
