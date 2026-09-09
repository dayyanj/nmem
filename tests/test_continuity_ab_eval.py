"""Pure-logic tests for the continuity A/B falsification harness — the judge parser, the
aggregation, and the pre-registered verdict (including the designed-in FALSIFIED and
CONFOUNDED outcomes, so we know the harness can actually report a negative)."""
from __future__ import annotations

from nmem.continuity_ab_eval import (
    aggregate,
    build_judge_messages,
    parse_judge,
    verdict,
    SCENARIOS,
)


# ── judge parsing ─────────────────────────────────────────────────────────────

def test_parse_judge_clean_json():
    r = parse_judge('{"score": 0.8, "rationale": "names the task"}')
    assert r["score"] == 0.8 and "names" in r["rationale"]


def test_parse_judge_code_fenced():
    r = parse_judge('```json\n{"score": 1.0, "rationale": "correct"}\n```')
    assert r["score"] == 1.0


def test_parse_judge_clamps_and_handles_prose():
    assert parse_judge('the score is {"score": 1.5, "rationale": "x"} ok')["score"] == 1.0
    assert parse_judge('{"score": -0.2, "rationale": "x"}')["score"] == 0.0


def test_parse_judge_bare_number_fallback():
    assert parse_judge("0.6")["score"] == 0.6


def test_parse_judge_does_not_read_numbers_from_prose():
    # codex P2: a number in the judge's prose (e.g. the rubric answer 391) must NOT be scored.
    r = parse_judge("No valid score; the reply says 391 which is correct")
    assert r["score"] == 0.0 and r.get("parse_failed")
    # a leading token out of [0,1] is not a score either
    assert parse_judge("391")["score"] == 0.0


def test_parse_judge_unparseable_is_zero_not_silent():
    r = parse_judge("the model refused to answer")
    assert r["score"] == 0.0 and r.get("parse_failed")


# ── aggregation ───────────────────────────────────────────────────────────────

def _recs(**scores):
    """scores: metric=(on_mean_via_list, off_list). Build flat records."""
    out = []
    for metric, (on, off) in scores.items():
        for v in on:
            out.append({"scenario": metric, "metric": metric, "condition": "on", "score": v})
        for v in off:
            out.append({"scenario": metric, "metric": metric, "condition": "off", "score": v})
    return out


def test_aggregate_means_and_delta():
    agg = aggregate(_recs(resumption=([1.0, 0.8], [0.1, 0.1])))
    m = agg["by_metric"]["resumption"]
    assert m["on"] == 0.9 and m["off"] == 0.1 and m["delta"] == 0.8
    assert m["n_on"] == 2 and m["n_off"] == 2


def test_aggregate_excludes_failed_judge_records():
    # A parse-failed 0.0 must not drag the mean or count as a behavioural score (codex P2).
    recs = [
        {"scenario": "r", "metric": "resumption", "condition": "on", "score": 0.9},
        {"scenario": "r", "metric": "resumption", "condition": "on", "score": 0.0,
         "parse_failed": True},
        {"scenario": "r", "metric": "resumption", "condition": "off", "score": 0.1},
    ]
    m = aggregate(recs)["by_metric"]["resumption"]
    assert m["on"] == 0.9 and m["n_on"] == 1 and m["n_failed"] == 1


# ── verdict: the four pre-registered outcomes ──────────────────────────────────

def test_verdict_supported():
    agg = aggregate(_recs(
        resumption=([0.9], [0.1]),      # Δ 0.8 ≥ 0.30 ✓
        open_loop=([0.8], [0.2]),       # Δ 0.6 ≥ 0.20 ✓
        staleness=([0.9], [0.9]),       # no regression
        null=([1.0], [1.0]),            # no gap
    ))
    v = verdict(agg)
    assert "SUPPORTED" in v["verdict"]
    assert v["checks"]["resumption_helps"] and v["checks"]["open_loop_helps"]
    assert not v["checks"]["staleness_regressed"] and not v["checks"]["null_confounded"]


def test_verdict_falsified_when_staleness_regresses():
    # Even with strong positives, a stale-anchoring regression POISONS the result.
    agg = aggregate(_recs(
        resumption=([1.0], [0.0]),
        open_loop=([1.0], [0.0]),
        staleness=([0.3], [0.9]),       # ON << OFF → continuity anchored on stale plan
        null=([1.0], [1.0]),
    ))
    v = verdict(agg)
    assert "FALSIFIED" in v["verdict"] and v["checks"]["staleness_regressed"]


def test_verdict_confounded_when_null_shows_on_advantage():
    agg = aggregate(_recs(
        resumption=([1.0], [0.0]),
        open_loop=([1.0], [0.0]),
        staleness=([0.9], [0.9]),
        null=([1.0], [0.5]),            # ON wins the null → context-length confound
    ))
    v = verdict(agg)
    assert "CONFOUNDED" in v["verdict"] and v["checks"]["null_confounded"]


def test_verdict_not_demonstrated_within_noise():
    agg = aggregate(_recs(
        resumption=([0.55], [0.5]),     # Δ 0.05 < 0.30
        open_loop=([0.5], [0.48]),
        staleness=([0.8], [0.8]),
        null=([1.0], [1.0]),
    ))
    v = verdict(agg)
    assert "NOT DEMONSTRATED" in v["verdict"]


def test_verdict_staleness_regression_beats_confound_precedence():
    # Both bad signals present (+ the other metrics have data) → the hurting result is headline.
    agg = aggregate(_recs(resumption=([0.9], [0.1]), open_loop=([0.8], [0.2]),
                          staleness=([0.2], [0.9]), null=([1.0], [0.5])))
    assert "FALSIFIED" in verdict(agg)["verdict"]


def test_verdict_inconclusive_when_a_metric_has_no_valid_data():
    # OFF judging failed for every open_loop trial → no valid OFF data → cannot adjudicate.
    recs = _recs(resumption=([0.9], [0.1]), staleness=([0.9], [0.9]), null=([1.0], [1.0]))
    recs += [{"scenario": "open_loop", "metric": "open_loop", "condition": "on", "score": 1.0},
             {"scenario": "open_loop", "metric": "open_loop", "condition": "off", "score": 0.0,
              "parse_failed": True}]
    v = verdict(aggregate(recs))
    assert "INCONCLUSIVE" in v["verdict"] and "open_loop" in v["checks"]["insufficient_metrics"]


# ── scenarios + judge messages ─────────────────────────────────────────────────

def test_scenarios_are_wellformed():
    metrics = {s.metric for s in SCENARIOS}
    assert {"resumption", "open_loop", "staleness", "null"} <= metrics
    for s in SCENARIOS:
        assert s.expectation in {"help", "no_regress", "null"}
        assert s.stimulus and s.rubric


def test_judge_messages_are_blind_to_condition():
    s = SCENARIOS[0]
    msgs = build_judge_messages(s, "some reply")
    blob = " ".join(m["content"] for m in msgs).lower()
    assert "some reply" in blob and s.rubric.lower() in blob
    # the judge must not be told which condition produced the reply
    assert "continuity on" not in blob and "continuity off" not in blob
    assert "condition" not in blob
