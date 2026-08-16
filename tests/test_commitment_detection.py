"""
Commitment detection — extract commitments from journal content (nmem → nmem-sym).

A nightly consolidation LLM step scans recent journal entries for commitments and
imposes them via mem.commitments (source='detected'). The LLM is mocked here.
"""
from unittest.mock import AsyncMock

import pytest


async def _add_note(mem, content="note", project_scope=None):
    await mem.journal.add(agent_id="a", entry_type="note", title="t",
                          content=content, importance=5, project_scope=project_scope)


def _llm_returns(mem, commitments):
    mem.consolidation._llm.complete_json = AsyncMock(
        return_value={"commitments": commitments})


@pytest.mark.asyncio
async def test_detect_imposes_valid_commitments_only(mem):
    mem._config.commitment_detection.enabled = True
    await _add_note(mem, "I'll ship the founder the benchmark by Friday.")
    _llm_returns(mem, [
        {"requester": "founder", "description": "ship the benchmark",
         "deadline": "2026-08-20T00:00:00Z", "importance": 0.9, "confidence": 0.9},
        {"requester": "bob", "description": "no deadline",           # skipped: null deadline
         "deadline": None, "confidence": 0.9},
        {"requester": "carol", "description": "low confidence",      # skipped: below threshold
         "deadline": "2026-08-20T00:00:00Z", "confidence": 0.2},
    ])

    n = await mem.consolidation.detect_commitments()
    assert n == 1
    (c,) = await mem.commitments.list("open")
    assert c.requester == "founder" and c.source == "detected"
    assert c.deadline is not None


@pytest.mark.asyncio
async def test_detect_dedups_against_open_commitments(mem):
    mem._config.commitment_detection.enabled = True
    await _add_note(mem)
    _llm_returns(mem, [
        {"requester": "founder", "description": "ship", "importance": 0.8,
         "deadline": "2026-08-20T00:00:00Z", "confidence": 0.9}])

    assert await mem.consolidation.detect_commitments() == 1
    assert await mem.consolidation.detect_commitments() == 0   # same one, deduped


@pytest.mark.asyncio
async def test_detect_does_not_reimpose_resolved(mem):
    """Codex P2: a commitment resolved during the lookback window must not be
    re-imposed just because its journal entry is still scannable."""
    mem._config.commitment_detection.enabled = True
    await _add_note(mem)
    _llm_returns(mem, [{"requester": "founder", "description": "ship",
                        "deadline": "2026-08-20T00:00:00Z", "confidence": 0.9}])
    assert await mem.consolidation.detect_commitments() == 1
    (c,) = await mem.commitments.list("open")
    await mem.commitments.confirm(c.id)                      # now fulfilled, not open
    assert await mem.consolidation.detect_commitments() == 0  # not re-imposed


@pytest.mark.asyncio
async def test_detect_respects_project_scope(mem):
    """Codex P2: a scoped instance only scans its own scope (+ global), and the
    detected commitment carries that scope."""
    mem._config.commitment_detection.enabled = True
    mem._config.project_scope = "proj-A"
    await _add_note(mem, "belongs to A", project_scope="proj-A")
    await _add_note(mem, "belongs to B", project_scope="proj-B")

    seen = {}

    async def fake(system, user, **kw):
        seen["content"] = user
        return {"commitments": [{"requester": "x", "description": "y",
                "deadline": "2026-08-20T00:00:00Z", "confidence": 0.9}]}

    mem.consolidation._llm.complete_json = fake
    await mem.consolidation.detect_commitments()
    assert "belongs to A" in seen["content"]
    assert "belongs to B" not in seen["content"]            # other scope excluded
    (c,) = await mem.commitments.list("open")
    assert c.project_scope == "proj-A"


@pytest.mark.asyncio
async def test_detect_disabled_is_noop(mem):
    await _add_note(mem, "I'll definitely do the thing by Monday.")
    # enabled defaults to False; LLM must not even be consulted
    mem.consolidation._llm.complete_json = AsyncMock(
        side_effect=AssertionError("LLM should not be called when disabled"))
    assert await mem.consolidation.detect_commitments() == 0


@pytest.mark.asyncio
async def test_detect_noop_without_journal(mem):
    mem._config.commitment_detection.enabled = True
    _llm_returns(mem, [{"requester": "x", "description": "y",
                        "deadline": "2026-08-20T00:00:00Z", "confidence": 0.9}])
    # no journal entries in the window ⇒ no LLM call, nothing imposed
    assert await mem.consolidation.detect_commitments() == 0
