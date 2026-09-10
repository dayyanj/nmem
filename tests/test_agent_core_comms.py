"""agent_core.comms.CommsLoop — channel-agnostic communication: a `communicate` intent delivers
the top pending utterance over the host's ChannelSink, and (async) the response is assessed →
nmem-sym drive discharge + a comms-skill lesson.

Ported from michelle's standalone service/communication.py dev test when that (dead, superseded)
copy was retired — the logic graduated to agent_core.comms; the coverage graduates with it.
Pure unit tests (mocked bridge/mem/sink/backend), no DB.
"""
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from nmem.agent_core.comms import CommsLoop, Utterance, _TAG


class _FakePool:
    def __init__(self, row):
        self._row = row
        self.executed = []

    async def fetchrow(self, *a, **k):
        return dict(self._row) if self._row else None

    async def execute(self, sql, *a):
        self.executed.append((sql, a))


def _bridge_mem(row):
    pool = _FakePool(row)
    graph = SimpleNamespace(pool=pool)
    bridge = SimpleNamespace(_graph=graph, record_comms_response=AsyncMock())
    mem = SimpleNamespace(_graph=graph,
                          skills=SimpleNamespace(record=AsyncMock(), find=AsyncMock(return_value=[])))
    return bridge, mem, pool


_ROW = {"id": 7, "source": "drive:communication", "action_type": "communicate",
        "text": "Found: Spwig ships a POS component.", "valence": "positive",
        "relevance": 0.8, "addressee": "djai"}


@pytest.mark.asyncio
async def test_ignores_non_communicate():
    b, m, _ = _bridge_mem(_ROW)
    loop = CommsLoop(b, m, backend=None, sink=SimpleNamespace(deliver=AsyncMock(return_value=True)))
    assert await loop.on_intent(SimpleNamespace(action="explore")) is None


@pytest.mark.asyncio
async def test_delivers_top_pending_and_defers():
    b, m, pool = _bridge_mem(_ROW)
    sink = SimpleNamespace(deliver=AsyncMock(return_value=True))
    loop = CommsLoop(b, m, backend=None, sink=sink)
    r = await loop.on_intent(SimpleNamespace(action="communicate"))
    assert r == 0.0                                  # deferred discharge (async response)
    assert sink.deliver.await_count == 1
    utt = sink.deliver.await_args.args[0]
    assert isinstance(utt, Utterance) and utt.id == 7 and "POS component" in utt.text
    assert any("UPDATE symbol_pending_utterances" in s for s, _ in pool.executed)  # marked delivered


@pytest.mark.asyncio
async def test_no_pending_no_deliver():
    b, m, _ = _bridge_mem(None)
    sink = SimpleNamespace(deliver=AsyncMock(return_value=True))
    loop = CommsLoop(b, m, backend=None, sink=sink)
    r = await loop.on_intent(SimpleNamespace(action="communicate"))
    assert r == 0.0 and sink.deliver.await_count == 0


@pytest.mark.asyncio
async def test_response_assessed_llm_discharges_and_learns():
    b, m, _ = _bridge_mem(_ROW)
    backend = SimpleNamespace(chat=AsyncMock(return_value=json.dumps(
        {"engagement": "answered", "valence": 0.6, "usefulness": 0.8,
         "lesson": "terse factual findings to djai get engaged replies"})))
    loop = CommsLoop(b, m, backend=backend, sink=SimpleNamespace(deliver=AsyncMock(return_value=True)),
                     agent_id="michelle")
    utt = Utterance(id=7, text=_ROW["text"], source="drive:novelty", action_type="communicate")
    await loop._handle_response(utt, "Interesting — does it support offline mode?", latency_s=5.0)
    # discharge closed with a high-ish score, answered=True
    assert b.record_comms_response.await_count == 1
    kw = b.record_comms_response.await_args.kwargs
    assert kw["utterance_id"] == 7 and kw["answered"] is True and kw["outcome_strength"] > 0.6
    # comms-skill recorded under the communication tag, worked=True
    assert m.skills.record.await_count == 1
    what = m.skills.record.await_args.args[0]
    assert what.startswith(f"{_TAG}: ") and "terse factual" in what
    assert m.skills.record.await_args.kwargs["worked"] is True


@pytest.mark.asyncio
async def test_fallback_no_backend_no_lesson():
    b, m, _ = _bridge_mem(_ROW)
    loop = CommsLoop(b, m, backend=None, sink=SimpleNamespace(deliver=AsyncMock(return_value=True)))
    utt = Utterance(id=7, text=_ROW["text"], source="drive:novelty", action_type="communicate")
    await loop._handle_response(utt, "ok thanks", latency_s=None)
    assert b.record_comms_response.await_count == 1            # still discharges (answered, neutral)
    assert m.skills.record.await_count == 0                    # no lesson without an LLM → no skill
