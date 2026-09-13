"""Conversational obligations — the chat → commitment extraction pipeline.

Pure unit tests over fakes (no Postgres, no real backend): the cue pre-filter, deadline
normalization, LLM-JSON extraction, the pluggable authority gate, and the flag-gated,
fail-open orchestration that imposes a commitment from a chat turn.
"""
import asyncio
import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from nmem.agent_core import obligation_extraction as oe

NOW = datetime(2026, 9, 13, 12, 0, tzinfo=timezone.utc)


# ── cue pre-filter ────────────────────────────────────────────────────────────

def test_prefilter_catches_request_with_deadline():
    assert oe.looks_like_commitment("Michelle, can you research the pgvector options and answer by Friday?")
    assert oe.looks_like_commitment("Please prepare the summary by end of week.")
    assert oe.looks_like_commitment("I need the report before Monday.")


def test_prefilter_rejects_non_commitments():
    assert not oe.looks_like_commitment("What is pgvector?")               # plain question
    assert not oe.looks_like_commitment("Can you explain how decay works?")  # request, no deadline
    assert not oe.looks_like_commitment("It's due process, nothing to do.")  # deadline word, no request cue...
    assert not oe.looks_like_commitment("")
    assert not oe.looks_like_commitment("x" * 5000)                         # oversized


# ── deadline normalization ────────────────────────────────────────────────────

def test_parse_deadline_iso_variants():
    d = oe._parse_deadline("2026-09-18T17:00:00Z", now=NOW)
    assert d == datetime(2026, 9, 18, 17, 0, tzinfo=timezone.utc)
    naive = oe._parse_deadline("2026-09-18T17:00:00", now=NOW)             # assumed UTC
    assert naive == datetime(2026, 9, 18, 17, 0, tzinfo=timezone.utc)


def test_parse_deadline_rejects_past_and_garbage():
    assert oe._parse_deadline("2020-01-01T00:00:00Z", now=NOW) is None     # past
    assert oe._parse_deadline("not a date", now=NOW) is None
    assert oe._parse_deadline(None, now=NOW) is None


# ── LLM extraction ─────────────────────────────────────────────────────────────

class _Backend:
    def __init__(self, reply): self.reply = reply; self.calls = []
    async def chat(self, messages, **kw): self.calls.append((messages, kw)); return self.reply


@pytest.mark.asyncio
async def test_extract_commitment_parses_json():
    reply = json.dumps({"is_commitment": True, "description": "research pgvector options and reply",
                        "deadline": "2026-09-18T17:00:00Z", "confidence": 0.9})
    ex = await oe.extract_commitment(_Backend(reply), "…", now=NOW)
    assert ex.is_commitment and ex.confidence == 0.9
    assert ex.description == "research pgvector options and reply"
    assert ex.deadline == datetime(2026, 9, 18, 17, 0, tzinfo=timezone.utc)


@pytest.mark.asyncio
async def test_extract_commitment_non_commitment_and_garbage():
    ex = await oe.extract_commitment(
        _Backend(json.dumps({"is_commitment": False, "description": "", "deadline": None, "confidence": 0.1})),
        "…", now=NOW)
    assert ex.is_commitment is False
    assert await oe.extract_commitment(_Backend("sorry, no json here"), "…", now=NOW) is None


@pytest.mark.asyncio
async def test_extract_commitment_backend_error_is_none():
    class _Boom:
        async def chat(self, *a, **k): raise RuntimeError("down")
    assert await oe.extract_commitment(_Boom(), "…", now=NOW) is None


# ── authority gate ─────────────────────────────────────────────────────────────

def test_open_gate_authorizes_anyone():
    ok, authority = oe.open_gate("interlocutor", "do the thing", runtime=None)
    assert ok is True and 0.0 < authority <= 1.0


# ── orchestration ───────────────────────────────────────────────────────────────

class _Commitments:
    def __init__(self, open_infos=None):
        self.imposed = []
        self._open = list(open_infos or [])
    async def list(self, status="open"):
        return self._open
    async def impose(self, requester, description, deadline, *, authority=0.5, **kw):
        self.imposed.append(dict(requester=requester, description=description,
                                 deadline=deadline, authority=authority, kw=kw))
        return SimpleNamespace(id=1, requester=requester, description=description)


def _runtime(backend, commitments):
    return SimpleNamespace(backend=backend, mem=SimpleNamespace(commitments=commitments), agent_id="agent-a")


@pytest.mark.asyncio
async def test_impose_disabled_is_noop(monkeypatch):
    monkeypatch.setenv("NMEM_CHAT_OBLIGATIONS_ENABLED", "0")
    c = _Commitments()
    out = await oe.maybe_impose_from_chat(_runtime(_Backend("{}"), c),
                                          "please research X and answer by Friday", now=NOW)
    assert out is None and c.imposed == []


@pytest.mark.asyncio
async def test_impose_skips_llm_when_cue_absent(monkeypatch):
    monkeypatch.setenv("NMEM_CHAT_OBLIGATIONS_ENABLED", "1")
    b = _Backend("{}"); c = _Commitments()
    out = await oe.maybe_impose_from_chat(_runtime(b, c), "what is pgvector?", now=NOW)
    assert out is None and b.calls == [] and c.imposed == []          # no LLM call at all


@pytest.mark.asyncio
async def test_impose_happy_path(monkeypatch):
    monkeypatch.setenv("NMEM_CHAT_OBLIGATIONS_ENABLED", "1")
    reply = json.dumps({"is_commitment": True, "description": "research pgvector and reply",
                        "deadline": "2026-09-18T17:00:00Z", "confidence": 0.9})
    b = _Backend(reply); c = _Commitments()
    out = await oe.maybe_impose_from_chat(
        _runtime(b, c), "Michelle, can you research pgvector and answer by Friday?", now=NOW)
    assert out is not None and len(c.imposed) == 1
    imp = c.imposed[0]
    assert imp["requester"] == oe.DEFAULT_REQUESTER
    assert imp["deadline"] == datetime(2026, 9, 18, 17, 0, tzinfo=timezone.utc)
    assert imp["authority"] == 0.5 and imp["kw"].get("source") == "chat"


@pytest.mark.asyncio
async def test_impose_respects_gate_denial(monkeypatch):
    monkeypatch.setenv("NMEM_CHAT_OBLIGATIONS_ENABLED", "1")
    reply = json.dumps({"is_commitment": True, "description": "do X", "deadline": "2026-09-18T17:00:00Z",
                        "confidence": 0.9})
    c = _Commitments()
    deny = lambda requester, description, runtime: (False, 0.0)
    out = await oe.maybe_impose_from_chat(
        _runtime(_Backend(reply), c), "please do X by Friday", now=NOW, gate=deny)
    assert out is None and c.imposed == []


@pytest.mark.asyncio
async def test_impose_skips_low_confidence_and_missing_deadline(monkeypatch):
    monkeypatch.setenv("NMEM_CHAT_OBLIGATIONS_ENABLED", "1")
    c = _Commitments()
    low = json.dumps({"is_commitment": True, "description": "do X",
                      "deadline": "2026-09-18T17:00:00Z", "confidence": 0.2})
    await oe.maybe_impose_from_chat(_runtime(_Backend(low), c), "please do X by Friday", now=NOW)
    nodl = json.dumps({"is_commitment": True, "description": "do X", "deadline": None, "confidence": 0.9})
    await oe.maybe_impose_from_chat(_runtime(_Backend(nodl), c), "please do X by Friday", now=NOW)
    assert c.imposed == []


@pytest.mark.asyncio
async def test_impose_noop_without_commitments_manager(monkeypatch):
    monkeypatch.setenv("NMEM_CHAT_OBLIGATIONS_ENABLED", "1")
    rt = SimpleNamespace(backend=_Backend("{}"), mem=SimpleNamespace(commitments=None))
    assert await oe.maybe_impose_from_chat(rt, "please do X by Friday", now=NOW) is None


@pytest.mark.asyncio
async def test_impose_dedups_against_open_commitment(monkeypatch):
    # A repeated deliver-by request must not spawn a second live obligation (codex).
    monkeypatch.setenv("NMEM_CHAT_OBLIGATIONS_ENABLED", "1")
    reply = json.dumps({"is_commitment": True, "description": "research pgvector and reply",
                        "deadline": "2026-09-18T17:00:00Z", "confidence": 0.9})
    existing = SimpleNamespace(requester=oe.DEFAULT_REQUESTER, description="research pgvector and reply")
    c = _Commitments(open_infos=[existing])
    out = await oe.maybe_impose_from_chat(
        _runtime(_Backend(reply), c), "can you research pgvector and answer by Friday?", now=NOW)
    assert out is None and c.imposed == []          # deduped — not re-imposed


@pytest.mark.asyncio
async def test_extract_commitment_times_out(monkeypatch):
    # A stalled backend must not withhold the chat reply — extraction is bounded (codex).
    class _Stall:
        async def chat(self, *a, **k):
            await asyncio.sleep(10)
            return "{}"
    ex = await oe.extract_commitment(_Stall(), "…", now=NOW, timeout=0.05)
    assert ex is None
