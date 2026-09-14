"""Phase 6-B — relationship-weighted obligation authority.

Unit tests over fakes (no Postgres): the graded ``deference_gate`` (grounded-warm lift,
fraught no-lift, stranger/ungrounded/no-graph/read-error → BASE, [0,1] clamp, BASE/SPAN env),
and gate SELECTION inside ``maybe_impose_from_chat`` (flag ON → deference authority flows onto
the imposed commitment; flag OFF → byte-identical to the flat ``open_gate``; explicit gate wins).

The gate reads the strongest GROUNDED self_defers_to edge via nmem_sym.relational_surface over a
fake pool; ``rc._table_ready`` is forced so the fake never runs DDL.
"""
import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from nmem.agent_core import obligation_extraction as oe

# nmem-sym is an optional dependency (CI installs nmem without it) — skip the whole module
# rather than abort collection when the sibling relational layer isn't importable.
rc = pytest.importorskip("nmem_sym.relational_consolidator")

NOW = datetime(2026, 9, 13, 12, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _table_ready(monkeypatch):
    monkeypatch.setattr(rc, "_table_ready", True)   # skip DDL on the fake pool


# ── fakes ──────────────────────────────────────────────────────────────────────

class _DeferPool:
    """Honours the self_defers_to + grounded + ANY(refs) query; returns the strongest match."""
    def __init__(self, rows): self._rows = rows
    async def execute(self, *a): return None
    async def fetch(self, sql, *a):
        assert "re.relation = $2" in sql and "status = 'grounded'" in sql
        _owner, relation, refs = a[0], a[1], set(a[2])
        assert relation == "self_defers_to"
        hit = [r for r in self._rows if r["counterpart_ref"] in refs]
        hit.sort(key=lambda r: -r["evidence_mass"])
        return hit[:1]


def _edge(ref, mass, swv):
    return {"counterpart_ref": ref, "evidence_mass": mass, "sum_weighted_valence": swv}


def _rt(rows, *, owner="michelle", have_graph=True):
    graph = SimpleNamespace(pool=_DeferPool(rows)) if have_graph else None
    bridge = SimpleNamespace(effective_owner=lambda: owner)
    return SimpleNamespace(graph=graph, bridge=bridge)


# ── deference_gate (the graded weight) ───────────────────────────────────────────

@pytest.mark.asyncio
async def test_gate_grounded_warm_lifts_authority():
    # mass 4 / swv 3 → strength 1.0, valence .75; lift = SPAN(.5)*(1*.75)=.375 → .875.
    ok, auth = await oe.deference_gate("dayyan", "do X", _rt([_edge("authority:dayyan", 4.0, 3.0)]))
    assert ok is True and auth == pytest.approx(0.875)


@pytest.mark.asyncio
async def test_gate_fraught_gives_no_lift():
    # negative valence → max(0, valence)=0 → authority stays at BASE (real evidence, no standing).
    ok, auth = await oe.deference_gate("foe", "do X", _rt([_edge("authority:foe", 8.0, -6.0)]))
    assert ok is True and auth == pytest.approx(0.5)


@pytest.mark.asyncio
async def test_gate_stranger_is_base():
    ok, auth = await oe.deference_gate("nobody", "do X", _rt([]))
    assert ok is True and auth == pytest.approx(0.5)


@pytest.mark.asyncio
async def test_gate_no_graph_is_base():
    ok, auth = await oe.deference_gate("dayyan", "do X", _rt([], have_graph=False))
    assert ok is True and auth == pytest.approx(0.5)


@pytest.mark.asyncio
async def test_gate_read_error_is_base():
    class _BoomGraph:
        class pool:
            @staticmethod
            async def execute(*a, **k): return None
            @staticmethod
            async def fetch(*a, **k): raise RuntimeError("db down")
    rt = SimpleNamespace(graph=_BoomGraph(), bridge=SimpleNamespace(effective_owner=lambda: "m"))
    ok, auth = await oe.deference_gate("dayyan", "do X", rt)
    assert ok is True and auth == pytest.approx(0.5)      # fail-open, never raises


@pytest.mark.asyncio
async def test_gate_never_rejects():
    # Even a strongly fraught bond authorizes (never a binary reject in the core).
    ok, _ = await oe.deference_gate("foe", "do X", _rt([_edge("authority:foe", 20.0, -18.0)]))
    assert ok is True


@pytest.mark.asyncio
async def test_gate_clamps_and_honours_env(monkeypatch):
    monkeypatch.setenv("NMEM_CHAT_DEFERENCE_BASE", "0.4")
    monkeypatch.setenv("NMEM_CHAT_DEFERENCE_SPAN", "0.6")
    ok, auth = await oe.deference_gate("dj", "do X", _rt([_edge("authority:dj", 4.0, 3.0)]))
    assert auth == pytest.approx(0.4 + 0.6 * 0.75)        # .85
    monkeypatch.setenv("NMEM_CHAT_DEFERENCE_SPAN", "5.0")
    _ok, auth2 = await oe.deference_gate("dj", "do X", _rt([_edge("authority:dj", 4.0, 3.0)]))
    assert auth2 == 1.0                                   # clamped to [0,1]


def test_candidate_refs():
    assert oe._candidate_refs("Dayyan") == ["authority:Dayyan"]
    assert oe._candidate_refs("  spaced  ") == ["authority:spaced"]
    assert oe._candidate_refs("") == [] and oe._candidate_refs(None) == []


def test_flag_default_off(monkeypatch):
    monkeypatch.delenv("NMEM_CHAT_DEFERENCE_AUTHORITY_ENABLED", raising=False)
    assert oe.deference_authority_enabled() is False


# ── gate SELECTION inside maybe_impose_from_chat ─────────────────────────────────

class _Backend:
    def __init__(self, reply): self.reply = reply
    async def chat(self, messages, **kw): return self.reply


class _Commitments:
    def __init__(self): self.imposed = []
    async def list(self, status="open"): return []
    async def impose(self, requester, description, deadline, *, authority=0.5,
                     importance=1.0, **kw):
        self.imposed.append(dict(requester=requester, authority=authority, importance=importance))
        return SimpleNamespace(id=1, requester=requester, description=description)


_COMMIT = json.dumps({"is_commitment": True, "description": "research pgvector and reply",
                      "deadline": "2026-09-18T17:00:00Z", "confidence": 0.9})
_ASK = "Michelle, can you research pgvector and answer by Friday?"


def _impose_rt(backend, commitments, rows, owner="agent-a"):
    return SimpleNamespace(backend=backend, mem=SimpleNamespace(commitments=commitments),
                           graph=SimpleNamespace(pool=_DeferPool(rows)),
                           bridge=SimpleNamespace(effective_owner=lambda: owner))


@pytest.mark.asyncio
async def test_impose_flag_on_uses_deference_authority(monkeypatch):
    monkeypatch.setenv("NMEM_CHAT_OBLIGATIONS_ENABLED", "1")
    monkeypatch.setenv("NMEM_CHAT_DEFERENCE_AUTHORITY_ENABLED", "1")
    c = _Commitments()
    # a grounded, warm deference toward the DEFAULT requester ("interlocutor")
    rows = [_edge(f"authority:{oe.DEFAULT_REQUESTER}", 4.0, 3.0)]
    out = await oe.maybe_impose_from_chat(_impose_rt(_Backend(_COMMIT), c, rows), _ASK, now=NOW)
    assert out is not None and len(c.imposed) == 1
    assert c.imposed[0]["authority"] == pytest.approx(0.875)   # deference-weighted, not flat 0.5


@pytest.mark.asyncio
async def test_impose_flag_off_is_flat_open_gate(monkeypatch):
    monkeypatch.setenv("NMEM_CHAT_OBLIGATIONS_ENABLED", "1")
    monkeypatch.setenv("NMEM_CHAT_DEFERENCE_AUTHORITY_ENABLED", "0")
    c = _Commitments()
    rows = [_edge(f"authority:{oe.DEFAULT_REQUESTER}", 4.0, 3.0)]   # present but flag off → ignored
    out = await oe.maybe_impose_from_chat(_impose_rt(_Backend(_COMMIT), c, rows), _ASK, now=NOW)
    assert out is not None and c.imposed[0]["authority"] == 0.5     # byte-identical to Phase 5


@pytest.mark.asyncio
async def test_impose_explicit_gate_still_wins(monkeypatch):
    monkeypatch.setenv("NMEM_CHAT_OBLIGATIONS_ENABLED", "1")
    monkeypatch.setenv("NMEM_CHAT_DEFERENCE_AUTHORITY_ENABLED", "1")
    c = _Commitments()
    explicit = lambda requester, description, runtime: (True, 0.11)
    out = await oe.maybe_impose_from_chat(
        _impose_rt(_Backend(_COMMIT), c, []), _ASK, now=NOW, gate=explicit)
    assert out is not None and c.imposed[0]["authority"] == pytest.approx(0.11)


# ── Phase 6-A: person-alias union in the deference read ──────────────────────

@pytest.mark.asyncio
async def test_gate_converges_via_alias(monkeypatch):
    import nmem_sym.person_alias as pa

    async def _fake(pool, *, owner_agent, ref):
        return {"Dayyan"} if ref == "person:7" else set()
    monkeypatch.setattr(pa, "aliases_of", _fake)
    # deference lives under the declared name; the speaker is addressed by the voice id
    named = [_edge("authority:Dayyan", 4.0, 3.0)]

    monkeypatch.setenv("NMEM_CHAT_PERSON_ALIAS_ENABLED", "0")
    _ok, auth = await oe.deference_gate("person:7", "do X", _rt(named))
    assert auth == pytest.approx(0.5)                     # no union → not found → stranger

    monkeypatch.setenv("NMEM_CHAT_PERSON_ALIAS_ENABLED", "1")
    _ok, auth = await oe.deference_gate("person:7", "do X", _rt(named))
    assert auth == pytest.approx(0.875)                  # union finds it → converged
