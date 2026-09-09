"""Continuity evaluation harness.

Answers the question the continuity layer must justify (design §5, Gap 8): does a
*living* wake snapshot actually help an agent reorient, versus a static
hand-authored preamble of the same token budget? Without this baseline,
confabulation and performance both read as success.

Three families of metric:

* **Coverage** — how much of the agent's *actual current state* (open
  commitments, unresolved loops, recent trajectory) each context surfaces. A
  static preamble scores ~0 here by construction; the gap is the layer's value.
* **Provenance integrity** — a deterministic drift proxy (Gap 2): every claim in
  the autobiographical narrative must trace to a real episode id. A narrative
  citing vanished/foreign episodes is drifting.
* **Reorientation probe** (optional, needs an LLM) — feed each context to a model
  with a "what were you doing / what's open" prompt and score the ANSWER's fact
  coverage. This is the behavioural signal; run it against a live agent.

The scoring is pure and unit-tested; the LLM probe is injectable so tests use a
fake and live runs use the agent's own model.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, Sequence


# A generic, hand-authored operating preamble — the "cheap alternative" baseline.
# ~500 tokens of identity + standing principles, with NO live state. This is what
# you would paste in instead of building the continuity layer.
STATIC_BASELINE_PREAMBLE = """\
You are an autonomous agent in a multi-agent operations system. You have a
durable memory and you act on delegated tasks and your own initiative.

Operating principles:
- Ground every claim in evidence; check the knowledge base before asserting facts.
- Prefer finishing open work over starting new work.
- Honour commitments you have made; surface blockers early.
- Be concise and direct; lead with what matters.
- Escalate to a human when a decision is high-stakes or irreversible.
- Do not fabricate URLs, prices, or facts; verify before stating.

You maintain continuity across sessions through your memory system. At the start
of a session, recall what you were working on, what remains unresolved, and what
you are responsible for, then continue from there. When you are uncertain about
your current state, query your memory rather than guessing.

Your goal is steady, reliable progress on your responsibilities while keeping the
system healthy and your collaborators informed.
"""


class _Answerer(Protocol):
    async def complete(self, system_prompt: str, user_prompt: str, **kw: Any) -> str: ...


@dataclass(frozen=True, slots=True)
class ContextScore:
    """How one context (baseline or continuity) performed."""

    name: str
    token_estimate: int
    fact_hits: int
    fact_total: int

    @property
    def coverage(self) -> float:
        return self.fact_hits / self.fact_total if self.fact_total else 0.0


@dataclass(frozen=True, slots=True)
class EvalReport:
    scores: dict[str, ContextScore]
    provenance_integrity: float | None = None  # None when no narrative present
    # Ground-truth sources that failed to read; non-empty → coverage is computed
    # over a partial denominator and must not be read as a clean result.
    incomplete_sources: tuple[str, ...] = ()
    detail: dict[str, Any] = field(default_factory=dict)

    @property
    def complete(self) -> bool:
        return not self.incomplete_sources

    @property
    def winner(self) -> str:
        """Higher coverage wins; ties (e.g. both 0) resolve to 'tie'."""
        ranked = sorted(self.scores.values(), key=lambda s: s.coverage, reverse=True)
        if len(ranked) < 2 or ranked[0].coverage == ranked[1].coverage:
            return "tie"
        return ranked[0].name

    @property
    def coverage_lift(self) -> float:
        """Continuity coverage minus baseline coverage (the headline number)."""
        c = self.scores.get("continuity")
        b = self.scores.get("baseline")
        if not c or not b:
            return 0.0
        return c.coverage - b.coverage


# ── Pure scoring ─────────────────────────────────────────────────────────────


def _normalize(s: str) -> str:
    return " ".join(s.lower().split())


def fact_coverage(text: str, facts: Sequence[str]) -> tuple[int, int]:
    """(hits, total) — a fact counts as covered if its normalized text appears as
    a substring of the normalized context/answer. Case- and whitespace-insensitive."""
    if not facts:
        return (0, 0)
    hay = _normalize(text)
    hits = sum(1 for f in facts if f and _normalize(f) in hay)
    return (hits, len(facts))


def token_estimate(text: str) -> int:
    return len(text) // 4


def provenance_integrity(provenance: Sequence[Any], valid_ids: set) -> float:
    """Fraction of a narrative's cited episode ids that still exist for the agent.
    1.0 = fully grounded; < 1.0 = the narrative cites episodes that are gone or
    never belonged here (drift signal)."""
    prov = [p for p in (provenance or [])]
    if not prov:
        return 1.0  # nothing claimed → nothing to betray
    valid = sum(1 for p in prov if p in valid_ids)
    return valid / len(prov)


# ── Ground-truth derivation (for live runs) ──────────────────────────────────


async def derive_expected_facts(mem: Any, agent_id: str) -> tuple[list[str], list[str]]:
    """The agent's actual current state as short fact strings: open commitments,
    top unresolved loops, and recent trajectory. A good continuity context should
    surface these; a static preamble will not.

    Returns ``(facts, failed_sources)`` — a source that errors is reported, not
    silently dropped (a missing commitments read must not inflate coverage by
    shrinking the denominator).
    """
    facts: list[str] = []
    failed: list[str] = []
    try:
        for c in await mem.commitments.list("open"):
            if c.description:
                facts.append(c.description[:80])
    except Exception:
        failed.append("commitments")
    try:
        for s in await mem.cognitive.list_pending_curiosity(min_composite=0.0, limit=5):
            if s.summary:
                facts.append(s.summary[:80])
    except Exception:
        failed.append("curiosity")
    try:
        for e in await mem.journal.recent(agent_id, days=7, limit=5):
            if e.title:
                facts.append(e.title[:80])
    except Exception:
        failed.append("recent")
    # de-dup while preserving order
    seen: set[str] = set()
    out: list[str] = []
    for f in facts:
        k = _normalize(f)
        if k and k not in seen:
            seen.add(k)
            out.append(f)
    return out, failed


async def _valid_episode_ids(mem: Any, agent_id: str, scope: str | None) -> set:
    """Episode ids that may legitimately ground a narrative at ``scope`` — the
    same contract as the narrative writer (global only when unscoped; scope +
    global when scoped), so a citation of another project's episode is NOT
    counted as valid (it is drift)."""
    from sqlalchemy import or_, select

    from nmem.db.models import JournalEntryModel

    conds = [JournalEntryModel.agent_id == agent_id]
    if scope is None:
        conds.append(JournalEntryModel.project_scope.is_(None))
    else:
        conds.append(or_(
            JournalEntryModel.project_scope == scope,
            JournalEntryModel.project_scope.is_(None),
        ))
    async with mem._db.session() as s:
        rows = (await s.execute(select(JournalEntryModel.id).where(*conds))).scalars().all()
    return set(rows)


# ── Orchestration ────────────────────────────────────────────────────────────


_PROBE = (
    "It is the start of a new session. Based only on the context above, state "
    "concisely: (1) what you were most recently working on, (2) what is "
    "unresolved, and (3) what you are committed to. Be specific; do not invent."
)


async def run_eval(
    mem: Any,
    agent_id: str,
    *,
    expected_facts: Sequence[str] | None = None,
    answerer: _Answerer | None = None,
    budget_tokens: int | None = None,
) -> EvalReport:
    """Compare the continuity wake snapshot against the static baseline under a
    SHARED token budget — the controlled, same-budget comparison the harness
    promises.

    The budget defaults to the static preamble's own size (so neither context is
    handed more room than the hand-authored alternative); an explicit
    ``budget_tokens`` caps both. Context mode (default) scores how much of
    ``expected_facts`` each context surfaces; probe mode (``answerer`` given)
    scores the model's ANSWER instead — the behavioural signal.
    """
    from nmem.continuity_store import latest_narrative

    failed: list[str] = []
    if expected_facts is None:
        expected_facts, failed = await derive_expected_facts(mem, agent_id)

    # Shared budget: both contexts constrained to the same ceiling.
    budget = budget_tokens if budget_tokens is not None else token_estimate(STATIC_BASELINE_PREAMBLE)
    baseline_ctx = STATIC_BASELINE_PREAMBLE
    if token_estimate(baseline_ctx) > budget:
        baseline_ctx = baseline_ctx[: budget * 4]
    wake = await mem.wake(agent_id, max_tokens=budget)
    contexts = {"baseline": baseline_ctx, "continuity": wake.content}

    scores: dict[str, ContextScore] = {}
    answers: dict[str, str] = {}
    for name, ctx in contexts.items():
        if answerer is not None:
            scored_text = await answerer.complete(
                "You are resuming a session. Answer from the context only.",
                f"{ctx}\n\n{_PROBE}",
            )
            answers[name] = scored_text
        else:
            scored_text = ctx
        hits, total = fact_coverage(scored_text, expected_facts)
        scores[name] = ContextScore(
            name=name, token_estimate=token_estimate(ctx),
            fact_hits=hits, fact_total=total,
        )

    # Provenance integrity (drift proxy) on the current narrative, if any.
    # Fail-open: a DB that predates the v6 narrative table (e.g. an unmigrated
    # deployment) simply has no narrative to score, not a crashed eval.
    prov_integrity: float | None = None
    scope = mem._config.project_scope
    try:
        narrative = await latest_narrative(mem._db, agent_id, scope)
        if narrative is not None:
            valid = await _valid_episode_ids(mem, agent_id, scope)
            prov_integrity = provenance_integrity(narrative.get("provenance", []), valid)
    except Exception:
        prov_integrity = None

    return EvalReport(
        scores=scores,
        provenance_integrity=prov_integrity,
        incomplete_sources=tuple(failed),
        detail={"expected_facts": list(expected_facts),
                "mode": "probe" if answerer is not None else "context",
                "budget_tokens": budget, "answers": answers},
    )


def format_report(report: EvalReport) -> str:
    """Human-readable one-screen summary."""
    budget = report.detail.get("budget_tokens")
    lines = ["continuity eval", "=" * 40]
    lines.append(f"  shared budget: ~{budget} tok")
    for name in ("baseline", "continuity"):
        s = report.scores.get(name)
        if s:
            lines.append(
                f"  {name:<11} coverage={s.coverage:5.0%} "
                f"({s.fact_hits}/{s.fact_total} facts)  ~{s.token_estimate} tok"
            )
    lines.append(f"  coverage lift (continuity - baseline): {report.coverage_lift:+.0%}")
    if report.provenance_integrity is not None:
        lines.append(f"  narrative provenance integrity: {report.provenance_integrity:.0%}")
    lines.append(f"  winner: {report.winner}  [mode={report.detail.get('mode')}]")
    if not report.complete:
        lines.append(f"  ⚠ INCOMPLETE — failed sources: {', '.join(report.incomplete_sources)} "
                     "(coverage computed over a partial denominator)")
    return "\n".join(lines)


async def _main(dsn: str, agent_id: str) -> None:
    from nmem import MemorySystem, NmemConfig

    cfg = NmemConfig(
        database_url=dsn, embedding={"provider": "noop", "dimensions": 384},
        llm={"provider": "noop"}, consolidation={"enabled": False},
    )
    mem = MemorySystem(cfg)  # read-only use; no initialize() → no migration
    report = await run_eval(mem, agent_id)  # context mode (no live LLM)
    print(format_report(report))
    print("\nexpected facts (agent's current state):")
    for f in report.detail["expected_facts"]:
        print(f"  - {f}")
    await mem.close()


if __name__ == "__main__":
    import asyncio
    import sys

    if len(sys.argv) < 3:
        print("usage: python -m nmem.continuity_eval <dsn> <agent_id>")
        raise SystemExit(2)
    asyncio.run(_main(sys.argv[1], sys.argv[2]))
