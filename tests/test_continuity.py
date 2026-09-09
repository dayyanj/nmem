"""Tests for the continuity / wake-snapshot layer.

Two layers: pure ranking/assembly (no DB) and integration through
``MemorySystem.wake()`` on the shared Postgres test DB.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from nmem.continuity import (
    assemble_continuity,
    commitment_salience,
    merge_open_loops,
)
from nmem.types import ContinuityResult, SymContinuityInputs

NOW = datetime(2026, 9, 9, 12, 0, 0, tzinfo=timezone.utc)


def _commitment(description, *, importance=0.5, requester="founder", deadline=None):
    # NB: commitment importance is the model's native 0..1 scale.
    return SimpleNamespace(
        description=description, importance=importance,
        requester=requester, deadline=deadline,
    )


def _curiosity(summary, *, composite_score, trigger_type="contradiction"):
    return SimpleNamespace(
        summary=summary, composite_score=composite_score, trigger_type=trigger_type,
    )


# ── Pure: salience ───────────────────────────────────────────────────────────


def test_commitment_salience_deadline_urgency_orders_correctly():
    far = _commitment("far", importance=1.0, deadline=NOW + timedelta(days=30))
    soon = _commitment("soon", importance=1.0, deadline=NOW + timedelta(days=5))
    imminent = _commitment("imminent", importance=1.0, deadline=NOW + timedelta(hours=12))
    overdue = _commitment("overdue", importance=1.0, deadline=NOW - timedelta(days=1))

    s_far = commitment_salience(far, NOW)
    s_soon = commitment_salience(soon, NOW)
    s_imminent = commitment_salience(imminent, NOW)
    s_overdue = commitment_salience(overdue, NOW)

    assert s_overdue > s_imminent > s_soon > s_far


def test_commitment_salience_has_floor():
    # A near-zero importance commitment must still stay visible.
    c = _commitment("low", importance=0.0, deadline=None)
    assert commitment_salience(c, NOW) >= 0.4


def test_commitment_salience_clamped_to_unit():
    c = _commitment("huge", importance=100.0, deadline=NOW - timedelta(days=1))
    assert commitment_salience(c, NOW) == 1.0


def test_commitment_importance_affects_ranking_on_native_scale():
    # Two obligations, same (no) deadline; the higher-importance one must win —
    # regression for treating the 0..1 scale as if it were 0..10.
    high = _commitment("important", importance=0.9)
    low = _commitment("trivial", importance=0.1)
    assert commitment_salience(high, NOW) > commitment_salience(low, NOW)


# ── Pure: unified open-loop merge ────────────────────────────────────────────


def test_merge_ranks_across_kinds_by_salience():
    commitments = [_commitment("ship benchmark", importance=0.4)]  # → floored ~0.4
    curiosity = [
        _curiosity("why did X regress", composite_score=0.9),
        _curiosity("minor gap", composite_score=0.1),
    ]
    loops, total = merge_open_loops(commitments, curiosity, NOW, k=10)

    assert total == 3
    # Highest-salience curiosity outranks the floored commitment, which outranks
    # the trivial curiosity.
    assert [l.kind for l in loops] == ["curiosity", "commitment", "curiosity"]
    assert loops[0].salience >= loops[1].salience >= loops[2].salience


def test_merge_top_k_reports_total():
    curiosity = [_curiosity(f"gap {i}", composite_score=i / 10.0) for i in range(10)]
    loops, total = merge_open_loops([], curiosity, NOW, k=3)
    assert total == 10
    assert len(loops) == 3
    # Top-3 are the highest scores.
    assert [round(l.salience, 1) for l in loops] == [0.9, 0.8, 0.7]


def test_merge_tie_prefers_commitment():
    # Same salience → commitment (prospective obligation) ranks first.
    # importance 0.7, no deadline → 0.5*0.7 + 0.5*0.3 = 0.5, tying the signal.
    commitments = [_commitment("obligation", importance=0.7)]
    curiosity = [_curiosity("gap", composite_score=0.5)]
    loops, _ = merge_open_loops(commitments, curiosity, NOW, k=10)
    assert loops[0].salience == pytest.approx(loops[1].salience)
    assert loops[0].kind == "commitment"


# ── Pure: assembly ───────────────────────────────────────────────────────────


def _assemble(**over):
    base = dict(
        agent_id="michelle", now=NOW, identity=None, recent=[], commitments=[],
        curiosity=[], policies=[], relevant=[], sym=None, max_tokens=1500,
        k_open_loops=7,
    )
    base.update(over)
    return assemble_continuity(**base)


def test_empty_is_the_morning_case_and_does_not_raise():
    r = _assemble()
    assert isinstance(r, ContinuityResult)
    assert "michelle" in r.content
    assert r.sections == ()
    assert r.n_open_loops_total == 0


def test_sections_appear_in_canonical_order():
    r = _assemble(
        identity="I am michelle, a peer reasoner.",
        policies=[SimpleNamespace(key="safety", content="Never delete prod data.")],
        commitments=[_commitment("ship", importance=0.8)],
        sym=SymContinuityInputs(
            self_model_summary="I reason well but drift on long horizons.",
            drive_state_prose="a contradiction between two sources is pulling attention",
            active_goals=("close the loop on X",),
        ),
    )
    assert r.sections == (
        "identity", "warnings", "open_loops", "goals", "self_model", "internal_state",
    )
    assert r.has_self_model and r.has_drive_state and r.n_goals == 1


def test_drive_state_rendered_as_prose_not_numbers():
    prose = "an unresolved contradiction between A and B is pulling attention"
    r = _assemble(sym=SymContinuityInputs(drive_state_prose=prose))
    assert prose in r.content
    # No scalar dashboard leaked in.
    assert "0." not in r.content.split("### Right now")[1]


def test_budget_truncates_and_reports_dropped():
    curiosity = [_curiosity(f"open question number {i} with padding", composite_score=0.5)
                 for i in range(40)]
    r = _assemble(curiosity=curiosity, max_tokens=200, k_open_loops=30)
    assert r.n_open_loops_total == 40
    # Global hard cap: content never exceeds the caller's char budget.
    assert len(r.content) <= 200 * 4
    assert "more unresolved, not shown" in r.content


def test_populated_snapshot_respects_token_budget():
    # Regression for the 120%-of-budget defect: a fully-populated snapshot must
    # not exceed the requested token budget.
    r = _assemble(
        identity="I am michelle. " * 20,
        policies=[SimpleNamespace(key="safety", content="Never delete prod data. " * 10)],
        recent=[SimpleNamespace(title=f"did thing {i} " * 5, content="") for i in range(10)],
        commitments=[_commitment(f"ship deliverable {i} " * 5, importance=0.8) for i in range(10)],
        curiosity=[_curiosity(f"why did {i} regress " * 5, composite_score=0.5) for i in range(10)],
        sym=SymContinuityInputs(
            self_model_summary="I reason well but drift. " * 10,
            drive_state_prose="a contradiction is pulling attention. " * 10,
            active_goals=tuple(f"goal {i} that is quite wordy" for i in range(10)),
        ),
        max_tokens=1500,
    )
    assert r.token_estimate <= 1500
    assert len(r.content) <= 1500 * 4


def test_oversized_first_item_renders_preview_not_blank():
    # A single huge commitment must not blank the whole open-loops lane.
    huge = _commitment("X" * 1800, importance=0.9)
    small = _commitment("quick follow-up", importance=0.9)
    r = _assemble(commitments=[huge, small], max_tokens=200)
    assert "(no continuity state yet)" not in r.content
    assert "open_loops" in r.sections
    assert "…" in r.content  # preview marker
    assert r.n_open_loops_shown >= 1


def test_counts_reflect_rendered_not_inputs():
    # 40 signals, tiny budget, k=30: shown count must equal what actually
    # rendered, not the pre-budget selection.
    curiosity = [_curiosity(f"open question {i} " * 4, composite_score=0.5) for i in range(40)]
    r = _assemble(curiosity=curiosity, max_tokens=200, k_open_loops=30)
    rendered = r.content.count("\n- [")  # loop lines actually in the content
    assert r.n_open_loops_shown == rendered
    assert r.n_open_loops_shown < 30


def test_applicable_policies_filters_foreign_scope():
    from nmem.continuity import applicable_policies

    pols = [
        SimpleNamespace(scope="global", key="a"),
        SimpleNamespace(scope="agent:michelle", key="b"),
        SimpleNamespace(scope="agent:sales", key="c"),
        SimpleNamespace(scope="entity_type:lead", key="d"),
    ]
    kept = {p.key for p in applicable_policies(pols, "michelle")}
    assert kept == {"a", "b"}  # global + own agent scope only


def test_tiny_budget_is_honored_not_floored():
    # A small max_tokens must not be silently raised to a 200-char floor.
    r = _assemble(commitments=[_commitment("do the thing", importance=0.9)], max_tokens=20)
    assert len(r.content) <= 20 * 4
    assert r.token_estimate <= 20


def test_total_counts_backlog_independent_of_fetched_candidates():
    # The ranked list holds only the fetched candidates, but curiosity_total
    # reflects the true backlog — the omission notice must use the real number.
    fetched = [_curiosity(f"gap {i}", composite_score=0.5) for i in range(5)]
    r = _assemble(curiosity=fetched, curiosity_total=130, k_open_loops=7)
    assert r.n_open_loops_total == 130  # not 5
    # 130 total − 5 shown = 125 unresolved and not shown.
    assert "+123 more unresolved, not shown" in r.content or "more unresolved" in r.content


# ── Runtime wiring: install_continuity_provider (generic agent_core glue) ─────


@pytest.mark.asyncio
async def test_install_continuity_provider_wires_and_wraps():
    from nmem.agent_core.runtime import install_continuity_provider

    class FakeMem:
        def __init__(self):
            self.provider = None

        def register_continuity_provider(self, p):
            self.provider = p

    class FakeBridge:
        async def continuity_inputs(self):
            return {"self_model_summary": "strong at synthesis",
                    "drive_state_prose": "a contradiction is pulling attention",
                    "active_goals": ["ship the seam", "close the loop"]}

    mem = FakeMem()
    assert install_continuity_provider(mem, FakeBridge()) is True
    assert mem.provider is not None
    si = await mem.provider("michelle")
    assert isinstance(si, SymContinuityInputs)
    assert si.active_goals == ("ship the seam", "close the loop")  # list → tuple
    assert si.self_model_summary == "strong at synthesis"
    assert si.drive_state_prose == "a contradiction is pulling attention"


def test_install_continuity_provider_noop_without_support():
    from nmem.agent_core.runtime import install_continuity_provider

    class MemWithReg:
        def register_continuity_provider(self, p):
            raise AssertionError("should not be called")

    # mem lacks register_continuity_provider → no-op
    assert install_continuity_provider(object(), object()) is False
    # bridge lacks continuity_inputs → no-op (reg never invoked)
    assert install_continuity_provider(MemWithReg(), object()) is False


# ── Integration: through MemorySystem.wake() (needs Postgres) ─────────────────


@pytest.mark.asyncio
async def test_wake_no_query_surfaces_commitments_and_curiosity(mem):
    await mem.commitments.impose("founder", "ship the continuity benchmark",
                                 importance=0.9)
    await mem.cognitive.emit_curiosity(
        "michelle", "contradiction", "two sources disagree on the release date",
        novelty_score=0.9, uncertainty_score=0.9,
    )
    await mem.journal.add(agent_id="michelle", entry_type="session_summary",
                          title="Explored the wake-snapshot design",
                          content="Sketched the two-speed continuity model.", importance=6)

    r = await mem.wake("michelle")  # no query — the "Morning." case

    assert "continuity benchmark" in r.content
    assert "two sources disagree" in r.content
    assert "Explored the wake-snapshot design" in r.content
    assert r.n_commitments >= 1
    assert r.n_open_loops_total >= 2
    assert "open_loops" in r.sections


@pytest.mark.asyncio
async def test_wake_sym_provider_seam_is_consulted(mem):
    async def provider(agent_id):
        return SymContinuityInputs(
            self_model_summary="I am strong at synthesis.",
            drive_state_prose="curiosity about the regression is pulling attention",
            active_goals=("finish the eval harness",),
        )

    mem.register_continuity_provider(provider)
    r = await mem.wake("michelle")

    assert "finish the eval harness" in r.content
    assert "curiosity about the regression" in r.content
    assert r.has_self_model and r.has_drive_state


@pytest.mark.asyncio
async def test_wake_sym_provider_failure_is_fail_open(mem):
    async def broken(agent_id):
        raise RuntimeError("bridge down")

    mem.register_continuity_provider(broken)
    # Must not raise — reorientation degrades to memory-only.
    r = await mem.wake("michelle")
    assert isinstance(r, ContinuityResult)
    assert not r.has_self_model


@pytest.mark.asyncio
async def test_wake_empty_agent_is_safe(mem):
    r = await mem.wake("nobody")
    assert isinstance(r, ContinuityResult)
    assert "nobody" in r.content


@pytest.mark.asyncio
async def test_wake_curiosity_is_project_scoped_end_to_end(mem):
    # Emit through the public API under two project scopes on a shared DB; an
    # agent configured for proj-A must see only proj-A's curiosity — and the
    # backlog count must agree (the emit path persists scope, the read filters).
    mem._config.project_scope = "proj-A"
    await mem.cognitive.emit_curiosity("michelle", "gap", "A-scoped gap",
                                       novelty_score=0.8, uncertainty_score=0.8)
    mem._config.project_scope = "proj-B"
    await mem.cognitive.emit_curiosity("other", "gap", "B-scoped gap",
                                       novelty_score=0.9, uncertainty_score=0.9)

    mem._config.project_scope = "proj-A"
    r = await mem.wake("michelle")

    assert "A-scoped gap" in r.content
    assert "B-scoped gap" not in r.content
    assert r.n_open_loops_total == 1  # count + list agree on the scoped backlog
