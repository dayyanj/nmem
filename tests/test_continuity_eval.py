"""Tests for the continuity eval harness."""

from __future__ import annotations

import pytest

from nmem.continuity_eval import (
    ContextScore,
    EvalReport,
    STATIC_BASELINE_PREAMBLE,
    fact_coverage,
    provenance_integrity,
    run_eval,
    token_estimate,
)
from nmem.continuity_store import write_narrative


# ── Pure scoring ─────────────────────────────────────────────────────────────


def test_fact_coverage_counts_substring_hits():
    text = "I shipped the wake assembler and started the eval harness."
    facts = ["wake assembler", "eval harness", "narrative writer"]
    assert fact_coverage(text, facts) == (2, 3)


def test_fact_coverage_is_case_and_whitespace_insensitive():
    assert fact_coverage("The   WAKE   Assembler", ["wake assembler"]) == (1, 1)


def test_fact_coverage_empty_facts():
    assert fact_coverage("anything", []) == (0, 0)


def test_provenance_integrity():
    assert provenance_integrity([1, 2, 3], {1, 2, 3}) == 1.0
    assert provenance_integrity([1, 2, 99], {1, 2, 3}) == pytest.approx(2 / 3)
    assert provenance_integrity([], {1}) == 1.0  # nothing claimed → nothing betrayed


def test_static_baseline_is_budget_sized():
    # The fair-comparison baseline should be on the order of a ~500-token preamble.
    assert 200 <= token_estimate(STATIC_BASELINE_PREAMBLE) <= 800


def test_report_winner_and_lift():
    r = EvalReport(scores={
        "baseline": ContextScore("baseline", 400, 0, 4),
        "continuity": ContextScore("continuity", 380, 3, 4),
    })
    assert r.winner == "continuity"
    assert r.coverage_lift == pytest.approx(0.75)

    tie = EvalReport(scores={
        "baseline": ContextScore("baseline", 400, 0, 4),
        "continuity": ContextScore("continuity", 380, 0, 4),
    })
    assert tie.winner == "tie"


# ── Integration ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_continuity_beats_static_baseline(mem):
    await mem.commitments.impose("founder", "ship the continuity eval harness",
                                 importance=0.9)
    await mem.cognitive.emit_curiosity("agent-a", "contradiction",
                                       "why did the drift metric spike",
                                       novelty_score=0.8, uncertainty_score=0.8)
    await mem.journal.add(agent_id="agent-a", entry_type="session_summary",
                          title="Built the wake assembler", content="x", importance=6)

    report = await run_eval(mem, "agent-a")

    # The static preamble contains none of the agent's actual state.
    assert report.scores["baseline"].coverage == 0.0
    # The living snapshot surfaces it.
    assert report.scores["continuity"].coverage > 0.5
    assert report.winner == "continuity"
    assert report.coverage_lift > 0.5
    assert report.complete
    # Same-budget comparison: both contexts respect the shared ceiling.
    budget = report.detail["budget_tokens"]
    assert report.scores["continuity"].token_estimate <= budget
    assert report.scores["baseline"].token_estimate <= budget


@pytest.mark.asyncio
async def test_shared_budget_caps_both_contexts(mem):
    await mem.commitments.impose("founder", "some open obligation", importance=0.8)
    report = await run_eval(mem, "agent-a", budget_tokens=120)
    assert report.detail["budget_tokens"] == 120
    assert report.scores["baseline"].token_estimate <= 120
    assert report.scores["continuity"].token_estimate <= 120


@pytest.mark.asyncio
async def test_eval_degrades_on_missing_narrative_table(mem):
    # A pre-v6 DB has no narrative table; the eval must still complete (provenance
    # integrity unknown), not crash.
    import nmem.continuity_store as cs

    async def boom(*a, **k):
        raise RuntimeError('relation "nmem_narrative_self" does not exist')
    orig = cs.latest_narrative
    try:
        cs.latest_narrative = boom  # run_eval imports this at call time
        report = await run_eval(mem, "agent-a")
    finally:
        cs.latest_narrative = orig
    assert report.provenance_integrity is None
    assert "continuity" in report.scores


@pytest.mark.asyncio
async def test_incomplete_source_is_reported(mem):
    async def boom(*a, **k):
        raise RuntimeError("commitments table unavailable")
    mem._commitments.list = boom  # simulate a failed ground-truth source

    report = await run_eval(mem, "agent-a")
    assert "commitments" in report.incomplete_sources
    assert not report.complete


class _EchoAnswerer:
    """Stands in for an agent LLM: echoes the context it was given, so probe-mode
    scoring reflects what each context made available to answer from."""

    async def complete(self, system_prompt, user_prompt, **kw):
        return user_prompt


@pytest.mark.asyncio
async def test_probe_mode_scores_the_answer(mem):
    await mem.commitments.impose("founder", "finish the probe-mode eval",
                                 importance=0.9)
    report = await run_eval(mem, "agent-a", answerer=_EchoAnswerer())
    assert report.detail["mode"] == "probe"
    assert report.scores["continuity"].coverage > report.scores["baseline"].coverage
    assert "continuity" in report.detail["answers"]


@pytest.mark.asyncio
async def test_provenance_integrity_flags_drift(mem):
    await mem.journal.add(agent_id="agent-a", entry_type="note",
                          title="real episode", content="x", importance=5)
    from sqlalchemy import text
    async with mem._db.session() as s:
        real_id = (await s.execute(text(
            "SELECT id FROM nmem_journal_entries WHERE agent_id='agent-a' LIMIT 1"))).scalar()

    # Narrative that cites one real episode and one vanished one → integrity 0.5.
    await write_narrative(mem._db, "agent-a", None,
                          current_period="I did real and imagined things.",
                          longer_trajectory=None, provenance=[real_id, 88888888],
                          token_len=8, full_reconstruction=True)

    report = await run_eval(mem, "agent-a")
    assert report.provenance_integrity == pytest.approx(0.5)
