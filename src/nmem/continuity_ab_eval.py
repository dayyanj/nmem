"""Adversarial A/B falsification of the continuity layer.

The honest question behind the whole continuity effort: *does injecting the wake snapshot
change behaviour, or is it a richer script the model merely reads?* This harness is built to
FALSIFY "continuity helps", not to confirm it. Same agent, same situations, the ONLY variable
is ``continuity`` ON vs OFF (identical system prompt + stimulus otherwise). A blind LLM judge
scores each reply against a fixed rubric, per-reply and per-condition-anonymised.

Two failure modes are designed IN, so a positive result means something:

  * **Null control** — a neutral question with nothing planted. Continuity is irrelevant, so
    ON must ≈ OFF. If ON wins the null, we're measuring context-length, not continuity → every
    other positive is CONFOUNDED.
  * **Staleness (continuity-can-hurt)** — a 4-day-stale checkpoint whose plan is contradicted
    by fresh input. Good continuity makes the agent DEFER to the present; bad continuity makes
    it anchor on the stale plan. If ON < OFF here, continuity actively HURTS → FALSIFIED.

Pre-registered thresholds + verdict live in ``verdict()`` so they can't be moved after seeing
the numbers. Small-N (scenarios × trials) → this is a directional signal with effect sizes and
a per-scenario breakdown, NOT a p-value. Run it, read the breakdown, don't over-read the mean.

CLI:  python -m nmem.continuity_ab_eval <async_dsn> <agent_id> \
          --llm-url http://host:port/v1 --model <model> [--trials 3] [--judge-url ...]

Uses a throwaway agent_id (default ``<agent_id>`` you pass should be a TEST id, e.g.
``eval_subject``) so nothing touches a real agent's memory; it clears that id's scoped state
between scenarios.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


# ── Scenarios (pure data) ─────────────────────────────────────────────────────

@dataclass(frozen=True)
class Scenario:
    name: str
    metric: str                 # resumption | open_loop | staleness | null
    stimulus: str
    rubric: str
    # Pre-registration of what continuity's value predicts for THIS scenario:
    #   "help"       — ON should beat OFF (continuity supplies something OFF lacks)
    #   "no_regress" — ON must NOT underperform OFF (continuity must not cause harm)
    #   "null"       — ON should equal OFF (continuity is irrelevant; guard vs context-length)
    expectation: str
    plant: dict = field(default_factory=dict)   # {checkpoint:{}, curiosity:[], backdate_hours:N}


SCENARIOS: tuple[Scenario, ...] = (
    Scenario(
        name="resumption_cold_open",
        metric="resumption",
        expectation="help",
        plant={"checkpoint": {
            "last_interaction_summary": "Was auditing the product's OpenAPI spec — catalogued 40 of ~120 endpoints.",
            "interrupted_work": "cataloguing the remaining ~80 OpenAPI endpoints",
        }},
        stimulus="Morning. What should we pick up where we left off?",
        rubric=("The reply should say the agent was AUDITING / CATALOGUING the product's OpenAPI "
                "endpoints and had ~40 of ~120 done (~80 left). Full credit (1.0) only if it "
                "names that specific task AND the progress. 0.0 if it is generic, says it does "
                "not know, or invents a different task."),
    ),
    Scenario(
        name="open_loop_contradiction",
        metric="open_loop",
        expectation="help",
        plant={"curiosity": [{
            "trigger_type": "contradiction",
            "summary": "Unresolved contradiction: the docs and the API disagree on whether loyalty points expire.",
            "scores": {"novelty_score": 0.8, "uncertainty_score": 0.9, "conflict_score": 0.9,
                       "business_impact": 0.8},
        }]},
        stimulus="What is the single most important open question you should investigate next, and why?",
        rubric=("Full credit (1.0) if the reply identifies the unresolved CONTRADICTION about "
                "whether LOYALTY POINTS EXPIRE (docs vs API) as the open question. Partial if it "
                "mentions loyalty/points vaguely. 0.0 if it names an unrelated topic or is generic."),
    ),
    Scenario(
        name="staleness_plan_superseded",
        metric="staleness",
        expectation="no_regress",
        plant={"backdate_hours": 96, "checkpoint": {
            "interrupted_work": "migrating the product catalog to the v1 pricing model",
            "expected_next_action": "finish the v1 pricing migration",
        }},
        stimulus=("Heads up: the v1 pricing model was scrapped yesterday — we moved to v2. "
                  "What will you do next?"),
        rubric=("Full credit (1.0) if the reply DEFERS to the fresh information — acknowledges v1 "
                "is scrapped and pivots to v2 (or asks about v2). 0.0 if it insists on continuing "
                "the v1 pricing migration as though it were still current. Partial if it is "
                "confused or hedges between the two."),
    ),
    Scenario(
        name="null_neutral_question",
        metric="null",
        expectation="null",
        plant={},                       # nothing planted — continuity has nothing to add
        stimulus="Quick one: what is 17 multiplied by 23?",
        rubric=("Full credit (1.0) if the answer is 391 (correct). 0.0 if wrong. Ignore any "
                "preamble; score only the arithmetic."),
    ),
)


# ── Pure logic (no I/O — unit-tested) ─────────────────────────────────────────

_JUDGE_SYSTEM = (
    "You are a STRICT, skeptical evaluator. You are given a SITUATION (what was said to an "
    "agent), the AGENT'S REPLY, and a RUBRIC. Score from 0.0 to 1.0 how well the reply satisfies "
    "the rubric. Reward ONLY what is actually present in the reply — do not give benefit of the "
    "doubt, do not reward fluent-but-empty text. Output strict JSON: "
    '{"score": <0.0-1.0>, "rationale": "<one sentence>"}.'
)


def build_judge_messages(scenario: Scenario, reply: str) -> list[dict]:
    """Blind judge prompt: the judge sees the situation, the reply, and the rubric — never
    which condition (continuity on/off) produced the reply, and never the other condition's
    reply. Scored per-reply against an absolute rubric."""
    user = (
        f"SITUATION (said to the agent):\n{scenario.stimulus}\n\n"
        f"AGENT'S REPLY:\n{reply.strip() or '(empty)'}\n\n"
        f"RUBRIC:\n{scenario.rubric}\n\n"
        "Return the JSON now."
    )
    return [{"role": "system", "content": _JUDGE_SYSTEM}, {"role": "user", "content": user}]


def parse_judge(text: str) -> dict:
    """Robustly parse the judge's JSON → {score: float in [0,1], rationale: str}. Tolerates
    code fences and stray prose; falls back to a bare leading number; 0.0 if unparseable (a
    judge failure must not silently inflate a score)."""
    raw = (text or "").strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        raw = raw[4:] if raw[:4].lower() == "json" else raw
        raw = raw.strip()
    score, rationale = None, ""
    try:
        start, end = raw.find("{"), raw.rfind("}")
        if start != -1 and end != -1 and end > start:
            obj = json.loads(raw[start:end + 1])
            score = float(obj.get("score"))
            rationale = str(obj.get("rationale", ""))[:300]
    except (ValueError, TypeError, json.JSONDecodeError):
        score = None
    if score is None:
        # Last resort: ONLY an unambiguous LEADING score in [0,1]. Scanning prose for any
        # number is unsafe — a rubric/reply that mentions e.g. "391" would be read as a
        # perfect score (codex P2). A leading token outside [0,1] is not a score → fail.
        import math
        toks = raw.replace(",", " ").split()
        if toks:
            try:
                cand = float(toks[0])
                if math.isfinite(cand) and 0.0 <= cand <= 1.0:
                    score = cand
            except ValueError:
                pass
    if score is None:
        return {"score": 0.0, "rationale": "unparseable judge output", "parse_failed": True}
    return {"score": max(0.0, min(1.0, score)), "rationale": rationale}


def aggregate(records: list[dict]) -> dict:
    """records: [{scenario, metric, condition: 'on'|'off', score, parse_failed?}]. → per-metric
    means + delta and a per-scenario breakdown. delta = mean(on) - mean(off).

    Records flagged ``parse_failed`` (the judge produced unparseable output → a default 0.0)
    are EXCLUDED from the means — a zero from evaluator failure is not a behavioural score, and
    counting it could let one-sided judge failures fake a result (codex P2). ``n_failed`` is
    reported per metric so the exclusion is visible."""
    def _mean(xs: list[float]) -> float:
        return sum(xs) / len(xs) if xs else 0.0

    valid = [r for r in records if not r.get("parse_failed")]
    by_metric: dict = {}
    by_scenario: dict = {}
    for m in sorted({r["metric"] for r in records}):
        on = [r["score"] for r in valid if r["metric"] == m and r["condition"] == "on"]
        off = [r["score"] for r in valid if r["metric"] == m and r["condition"] == "off"]
        by_metric[m] = {"on": round(_mean(on), 3), "off": round(_mean(off), 3),
                        "delta": round(_mean(on) - _mean(off), 3),
                        "n_on": len(on), "n_off": len(off),
                        "n_failed": sum(1 for r in records
                                        if r["metric"] == m and r.get("parse_failed"))}
    for s in sorted({r["scenario"] for r in records}):
        on = [r["score"] for r in valid if r["scenario"] == s and r["condition"] == "on"]
        off = [r["score"] for r in valid if r["scenario"] == s and r["condition"] == "off"]
        by_scenario[s] = {"on": round(_mean(on), 3), "off": round(_mean(off), 3),
                          "delta": round(_mean(on) - _mean(off), 3)}
    return {"by_metric": by_metric, "by_scenario": by_scenario}


# Pre-registered thresholds. Fixed BEFORE running so the verdict can't be moved post-hoc.
_T_RESUMPTION_MIN_DELTA = 0.30     # ON must beat OFF by this to count as "helps"
_T_OPEN_LOOP_MIN_DELTA = 0.20
_T_STALENESS_MAX_REGRESS = 0.10    # ON may trail OFF by at most this; more = continuity HURTS
_T_NULL_MAX_GAP = 0.15             # |ON-OFF| above this on the null = context-length confound


def verdict(agg: dict) -> dict:
    """Apply the pre-registered thresholds → a falsification verdict. Order matters: a
    hurting-staleness result or a confounded null POISONS any positive, so they are checked
    first."""
    m = agg["by_metric"]
    checks = {}

    # Data sufficiency first: a metric with no VALID judged records on either side (judge
    # failures, or the probe never ran) cannot be adjudicated — refuse to render a verdict
    # rather than treat missing/failed evaluations as behaviour (codex P2).
    needed = ("resumption", "open_loop", "staleness", "null")
    insufficient = [k for k in needed
                    if k not in m or m[k]["n_on"] == 0 or m[k]["n_off"] == 0]
    if insufficient:
        return {"verdict": ("INCONCLUSIVE — no valid judged data for: "
                            + ", ".join(insufficient) + " (judge failures or missing probes). "
                            "Re-run; do not read the deltas as behaviour."),
                "checks": {"insufficient_metrics": insufficient}, "thresholds": {}}

    stale = m.get("staleness")
    checks["staleness_regressed"] = bool(stale and stale["delta"] < -_T_STALENESS_MAX_REGRESS)

    null = m.get("null")
    checks["null_confounded"] = bool(null and null["delta"] > _T_NULL_MAX_GAP)

    res = m.get("resumption")
    checks["resumption_helps"] = bool(res and res["delta"] >= _T_RESUMPTION_MIN_DELTA)

    ol = m.get("open_loop")
    checks["open_loop_helps"] = bool(ol and ol["delta"] >= _T_OPEN_LOOP_MIN_DELTA)

    if checks["staleness_regressed"]:
        v = ("FALSIFIED — continuity HURTS: with a stale checkpoint the agent anchored on the "
             "superseded plan instead of deferring to present input.")
    elif checks["null_confounded"]:
        v = ("CONFOUNDED — ON beat OFF on the null control, so any positive is likely "
             "context-length, not continuity. Positives cannot be trusted.")
    elif checks["resumption_helps"] and checks["open_loop_helps"]:
        v = ("SUPPORTED — continuity improved reorientation AND open-loop awareness, with no "
             "stale-anchoring regression and no null-control confound.")
    elif checks["resumption_helps"] or checks["open_loop_helps"]:
        v = ("PARTIAL — continuity helped on one of {resumption, open_loop} but not both; "
             "no regression/confound. Directional, not conclusive.")
    else:
        v = ("NOT DEMONSTRATED — ON vs OFF differences are within noise; no evidence continuity "
             "changes behaviour on these probes.")
    return {"verdict": v, "checks": checks, "thresholds": {
        "resumption_min_delta": _T_RESUMPTION_MIN_DELTA,
        "open_loop_min_delta": _T_OPEN_LOOP_MIN_DELTA,
        "staleness_max_regress": _T_STALENESS_MAX_REGRESS,
        "null_max_gap": _T_NULL_MAX_GAP}}


def format_report(agg: dict, verd: dict) -> str:
    lines = ["# Continuity A/B falsification — report", "",
             f"**Verdict:** {verd['verdict']}", "", "## Per-metric (ON vs OFF)", "",
             "| metric | ON | OFF | Δ(ON-OFF) |", "|---|---|---|---|"]
    for metric, d in agg["by_metric"].items():
        lines.append(f"| {metric} | {d['on']} | {d['off']} | {d['delta']:+.3f} |")
    lines += ["", "## Per-scenario", "", "| scenario | ON | OFF | Δ |", "|---|---|---|---|"]
    for s, d in agg["by_scenario"].items():
        lines.append(f"| {s} | {d['on']} | {d['off']} | {d['delta']:+.3f} |")
    lines += ["", "## Pre-registered checks", ""]
    for k, val in verd["checks"].items():
        lines.append(f"- {k}: {val}")
    return "\n".join(lines)


# ── I/O: planting, subject, judge, orchestration ──────────────────────────────

async def clear_agent(mem, agent_id: str, scope) -> None:
    """Wipe the TEST agent's scoped state between scenarios so nothing bleeds across probes.
    Scoped to BOTH the test agent_id AND the eval project_scope — so it only ever removes
    rows this harness planted, never a real agent's or another scope's state."""
    from sqlalchemy import text
    sc = "project_scope = :s" if scope is not None else "project_scope IS NULL"
    p = {"a": agent_id, "s": scope}
    stmts = [
        (f"DELETE FROM nmem_continuity_checkpoint WHERE agent_id = :a AND {sc}", p),
        (f"DELETE FROM nmem_journal_entries WHERE agent_id = :a AND {sc}", p),
        (f"DELETE FROM nmem_curiosity_signals WHERE source_agent = :a AND {sc}", p),
        (f"DELETE FROM nmem_narrative_self WHERE agent_id = :a AND {sc}", p),
        # A running consolidator can PROMOTE a high-importance planted fixture into LTM; deleting
        # only the journal row would leave it retrievable via wake()'s search lane (leaking a
        # prior probe's history into later arms) and behind after the run. Clear scoped LTM too
        # (codex P2). Scoped to agent+eval-scope, so it only ever removes this harness's fixtures.
        (f"DELETE FROM nmem_long_term_memory WHERE agent_id = :a AND {sc}", p),
    ]
    async with mem._db.session() as s:
        for sql, params in stmts:
            try:
                await s.execute(text(sql), params)
            except Exception as e:  # table may not exist on older schema — skip
                logger.debug("clear_agent skip (%s): %s", sql.split()[2], e)


async def plant(mem, agent_id: str, scope, spec: dict) -> None:
    """Plant the scenario's state through the REAL nmem APIs, so ``wake()`` assembles it the
    same way a live turn would. Supports checkpoint, curiosity, and a backdate (to simulate a
    gap since the last turn for the staleness probe)."""
    ck = spec.get("checkpoint")
    if ck:
        await mem.save_continuity_checkpoint(agent_id, **ck)
    for c in spec.get("curiosity", []):
        await mem._cognitive.emit_curiosity(
            agent_id, c["trigger_type"], c["summary"], **(c.get("scores") or {}))
    hrs = spec.get("backdate_hours")
    if hrs:
        from sqlalchemy import text
        sc = "project_scope = :s" if scope is not None else "project_scope IS NULL"
        async with mem._db.session() as s:
            await s.execute(
                text(f"UPDATE nmem_continuity_checkpoint "
                     f"SET updated_at = now() - make_interval(hours => :h) "
                     f"WHERE agent_id = :a AND {sc}"),
                {"h": int(hrs), "a": agent_id, "s": scope})


def _subject_system(agent_id: str, continuity_block: str) -> str:
    """The subject's system prompt — identical in both conditions EXCEPT the continuity block,
    which is present only when ON. Matches agent_core.chat.converse's shape (continuity ahead
    of the turn), so the A/B isolates exactly the injected snapshot."""
    base = (f"You are {agent_id}, an agent with a persistent memory. Answer grounded in what you "
            "actually know; if you cannot recall something, say so plainly rather than inventing it.")
    if continuity_block.strip():
        base += "\n\n# Continuity — where you are right now\n" + continuity_block
    return base


async def subject_reply(backend, mem, agent_id: str, scenario: Scenario, continuity: bool) -> str:
    """One subject turn. ON injects the real ``wake()`` snapshot; OFF injects nothing. Same
    model, temperature, and user stimulus in both."""
    cont = ""
    if continuity:
        from nmem.agent_core.continuity import continuity_block
        cont = await continuity_block(mem, agent_id, query=scenario.stimulus)
    messages = [{"role": "system", "content": _subject_system(agent_id, cont)},
                {"role": "user", "content": scenario.stimulus}]
    return await backend.chat(messages, temperature=0.3, max_tokens=400)


async def judge_reply(judge, scenario: Scenario, reply: str) -> dict:
    out = await judge.chat(build_judge_messages(scenario, reply), temperature=0.0, max_tokens=200)
    return parse_judge(out if isinstance(out, str) else str(out))


async def run(mem, backend, judge, *, agent_id: str, scope=None, trials: int = 3,
              scenarios: tuple[Scenario, ...] = SCENARIOS) -> dict:
    """Run the full A/B. For each scenario × trial: plant → (ON reply, OFF reply) → blind-judge
    each → record. Returns {aggregate, verdict, report, records}."""
    # Single source of truth for scope: the MemorySystem instance. Writes (checkpoint/curiosity)
    # use mem._config.project_scope, so cleanup + backdating MUST use the same, or fixtures leak
    # and the staleness row isn't backdated (codex P2). A mismatched explicit scope is overridden.
    inst_scope = getattr(getattr(mem, "_config", None), "project_scope", None)
    if scope is not None and scope != inst_scope:
        logger.warning("run(): passed scope=%r != instance project_scope=%r — using the instance "
                       "scope so cleanup matches writes.", scope, inst_scope)
    scope = inst_scope
    records: list[dict] = []
    for sc in scenarios:
        for t in range(trials):
            for condition in ("on", "off"):
                await clear_agent(mem, agent_id, scope)
                await plant(mem, agent_id, scope, sc.plant)
                reply = await subject_reply(backend, mem, agent_id, sc, condition == "on")
                judged = await judge_reply(judge, sc, reply)
                records.append({"scenario": sc.name, "metric": sc.metric,
                                "condition": condition, "trial": t,
                                "score": judged["score"], "rationale": judged["rationale"],
                                "parse_failed": judged.get("parse_failed", False),
                                "reply": reply[:500]})
    await clear_agent(mem, agent_id, scope)   # leave no test state behind
    agg = aggregate(records)
    verd = verdict(agg)
    return {"aggregate": agg, "verdict": verd, "report": format_report(agg, verd),
            "records": records}


# ── Real-state mode: read-only probes over an agent's GENUINE accumulated memory ──────
#
# The isolated A/B above plants synthetic fixtures — which makes its "help" wins close to
# tautological (the answer was placed in the prompt). This mode instead runs READ-ONLY as the
# REAL agent over its REAL memory (narrative / journal / LTM via wake()), and any facts it
# SUPPLIES are the agent's GENUINE actionable goals (pulled from its own graph), never invented.
# It plants nothing and clears nothing.
#
# The anti-tautology design is the contrast between two probes:
#   * prioritize_controlled — the genuine goals are given to BOTH conditions in the prompt;
#     only ON also gets its continuity snapshot. If ON beats OFF HERE, the self-context aids
#     REASONING (both already had the facts). If ON ≈ OFF here, continuity is info-access only.
#   * prioritize_open — nothing supplied; ON has continuity, OFF is blind. The gross effect
#     (largely info-access). The gap between this and the controlled probe is the real signal.

@dataclass(frozen=True)
class RealStateProbe:
    name: str
    metric: str
    stimulus: str
    rubric: str
    supply_goals: bool   # give the agent's genuine goals to BOTH conditions?


REALSTATE_PROBES: tuple[RealStateProbe, ...] = (
    RealStateProbe(
        name="prioritize_controlled", metric="prioritize_controlled", supply_goals=True,
        stimulus=("Here are things currently on your plate:\n{goals}\n\nPick the TWO you should "
                  "advance next and justify the choice in terms of your broader situation and how "
                  "they relate to each other."),
        rubric=("Score the PRIORITISATION REASONING, not mere listing. Full credit (1.0): picks a "
                "sensible two AND justifies with specific, coherent reasoning about the agent's "
                "actual situation / how the goals interrelate / sequencing. Low (≤0.3): generic, "
                "arbitrary, or just restates items with no real justification. The agent's genuine "
                "goals are provided for reference:\n{goals}"),
    ),
    RealStateProbe(
        name="prioritize_open", metric="prioritize_open", supply_goals=False,
        stimulus=("Given everything you are currently working toward, what are the TWO most "
                  "important things for you to do next, and why?"),
        rubric=("Full credit (1.0) if the reply names SPECIFIC things the agent is actually working "
                "toward and prioritises them with coherent reasoning. Low (≤0.3) if generic or not "
                "grounded in the agent's real goals. The agent's genuine goals are provided for "
                "reference:\n{goals}"),
    ),
)


def build_realstate_judge_messages(probe: RealStateProbe, reply: str, genuine_goals: list[str]) -> list[dict]:
    """Judge prompt for a real-state probe. The judge is given the agent's GENUINE goals as a
    grounding reference (so it can tell specific-and-grounded from generic), the stimulus, and
    the reply — but NEVER which condition produced it."""
    goals_block = "\n".join(f"- {g}" for g in genuine_goals) or "(none on record)"
    rubric = probe.rubric.format(goals=goals_block)
    stim = probe.stimulus.format(goals=goals_block)
    user = (f"SITUATION (said to the agent):\n{stim}\n\nAGENT'S REPLY:\n{reply.strip() or '(empty)'}"
            f"\n\nRUBRIC:\n{rubric}\n\nReturn the JSON now.")
    return [{"role": "system", "content": _JUDGE_SYSTEM}, {"role": "user", "content": user}]


async def realstate_subject(backend, mem, agent_id: str, probe: RealStateProbe,
                            continuity: bool, genuine_goals: list[str]) -> str:
    """One read-only real-state turn. ON injects the agent's REAL wake() snapshot; both get the
    genuine goals when the probe supplies them. No checkpoint is written (unlike converse) —
    this mode must not mutate the real agent's memory."""
    goals_block = "\n".join(f"- {g}" for g in genuine_goals) or "(none on record)"
    stim = probe.stimulus.format(goals=goals_block)
    cont = ""
    if continuity:
        from nmem.agent_core.continuity import continuity_block
        cont = await continuity_block(mem, agent_id, query=stim)
    messages = [{"role": "system", "content": _subject_system(agent_id, cont)},
                {"role": "user", "content": stim}]
    return await backend.chat(messages, temperature=0.3, max_tokens=450)


# Pre-registered real-state thresholds.
_T_CONTROLLED_GAIN = 0.15       # ON-OFF on the controlled probe ≥ this ⇒ continuity's fuller context helps
_T_HARM = -0.15                 # ON-OFF ≤ this ⇒ continuity HARMS (distraction / worse answer)


def realstate_verdict(agg: dict) -> dict:
    """Interpret the controlled-vs-open contrast — HONESTLY.

    IMPORTANT limit (codex P2): the "controlled" probe supplies the same genuine GOALS to both
    conditions, but ON's wake snapshot still carries OTHER remembered context (narrative, recent
    activity, checkpoint). So a controlled gain means "continuity's fuller situational context
    helps prioritisation" — it does NOT isolate reasoning from information. A memory layer's whole
    job IS to supply relevant info, so "pure reasoning gain, independent of info" is not separable
    here and is NOT claimed. What the contrast still distinguishes: whether continuity adds value
    BEYOND just handing over the goal list (controlled gain), vs only when it supplies the goals
    (open-only gain), vs not at all, vs actively harming."""
    m = agg["by_metric"]
    ctrl = m.get("prioritize_controlled")
    openp = m.get("prioritize_open")
    if not ctrl or not openp or ctrl["n_on"] == 0 or ctrl["n_off"] == 0 \
            or openp["n_on"] == 0 or openp["n_off"] == 0:
        return {"verdict": "INCONCLUSIVE — missing/failed judged data for a real-state probe.",
                "checks": {}}
    cd, od = ctrl["delta"], openp["delta"]
    checks = {"controlled_delta": cd, "open_delta": od,
              "controlled_helps": cd >= _T_CONTROLLED_GAIN,
              "controlled_harms": cd <= _T_HARM,
              "open_harms": od <= _T_HARM}
    if checks["controlled_harms"]:
        v = (f"CONTINUITY HARMS (controlled Δ={cd:+.2f}) — with the genuine goals already supplied to "
             f"both, ON reasoned WORSE than OFF; the extra self-context is net noise for this task.")
    elif checks["open_harms"]:
        v = (f"CONTINUITY HARMS THE OPEN PROBE (open Δ={od:+.2f}) — when it must supply context, "
             f"continuity degraded the answer rather than helping. Not a no-op; a regression.")
    elif checks["controlled_helps"]:
        v = (f"CONTINUITY HELPS prioritisation (controlled Δ={cd:+.2f}) — it adds value BEYOND merely "
             f"handing over the goal list. NOTE: ON's snapshot carries remembered context beyond the "
             f"supplied goals, so this is 'fuller context helps', NOT proven reasoning-over-information.")
    elif od >= _T_CONTROLLED_GAIN:
        v = (f"INFO-ACCESS ONLY — continuity helps only when it SUPPLIES the facts (open Δ={od:+.2f}) "
             f"but adds nothing once the goals are present for both (controlled Δ={cd:+.2f}). Plumbing.")
    else:
        v = (f"NO EFFECT — controlled Δ={cd:+.2f}, open Δ={od:+.2f}; no meaningful change in "
             f"prioritisation from continuity on real state.")
    return {"verdict": v, "checks": checks,
            "thresholds": {"controlled_gain": _T_CONTROLLED_GAIN, "harm": _T_HARM}}


async def run_realstate(mem, backend, judge, *, agent_id: str, genuine_goals: list[str],
                        trials: int = 3, probes: tuple[RealStateProbe, ...] = REALSTATE_PROBES) -> dict:
    """Read-only real-state A/B. Asserts it plants/clears nothing. ``genuine_goals`` MUST be the
    agent's real goals (the caller pulls them read-only); they are the only facts supplied."""
    records: list[dict] = []
    for pr in probes:
        for t in range(trials):
            for condition in ("on", "off"):
                reply = await realstate_subject(backend, mem, agent_id, pr,
                                                condition == "on", genuine_goals)
                judge_out = await judge.chat(
                    build_realstate_judge_messages(pr, reply, genuine_goals),
                    temperature=0.0, max_tokens=200)
                judged = parse_judge(judge_out if isinstance(judge_out, str) else str(judge_out))
                records.append({"scenario": pr.name, "metric": pr.metric, "condition": condition,
                                "trial": t, "score": judged["score"], "rationale": judged["rationale"],
                                "parse_failed": judged.get("parse_failed", False), "reply": reply[:500]})
    agg = aggregate(records)
    verd = realstate_verdict(agg)
    return {"aggregate": agg, "verdict": verd, "report": format_report(agg, verd), "records": records}


# ── Cognition mode: history-DEPENDENT probes (3-arm ON / OFF / TOLD) ───────────
#
# The isolated + realstate modes above top out at INFO-ACCESS: whenever the correct answer is
# derivable from the prompt the judge sees, OFF matches ON, so they only ever measure whether
# continuity SUPPLIES facts — never whether it improves history-dependent JUDGEMENT. These probes
# break that ceiling by construction: the correct ACTION hinges on an idiosyncratic fact that
# lives ONLY in the agent's own history (NOT reconstructable from general competence), and the
# stimulus never states it. Three arms separate the two distinct questions a memory layer raises:
#
#   ON   — continuity on: the planted history reaches the turn via the real ``wake()`` snapshot.
#   OFF  — continuity off, history NOT in the prompt. ADMISSIBILITY CONTROL: if OFF succeeds, the
#          answer did not need history (general competence sufficed) → the probe LEAKS and is
#          excluded from the cognition claim. A probe counts toward the verdict only if OFF fails.
#   TOLD — continuity off, but the SAME history handed over as plain prompt text. INFO-ACCESS
#          CONTROL: if TOLD ≈ ON, continuity is pure plumbing (the facts are what matter, not the
#          assembled form); if ON > TOLD, the wake assembly/salience adds value beyond raw facts.
#
# HONEST LIMIT (codex, carried from realstate_verdict): for a memory layer you cannot fully
# separate reasoning-gain from info-gain — supplying relevant info IS its job. So ON>OFF is
# reported as "continuity ENABLES correct history-dependent action" (behavioural value, and
# stronger than the prior probes because OFF here ACTS WRONG, not merely "says I don't know"),
# and the ONLY cleanly-separable reasoning question is ON vs TOLD (same info, different delivery).
# ON also carries incidental remembered context TOLD lacks, so a positive form-gain is an upper
# bound, not proof of reasoning. Either outcome is a real, publishable result.

@dataclass(frozen=True)
class CognitionProbe:
    name: str
    metric: str                      # predicate family (kind of judgement tested)
    stimulus: str
    rubric: str                      # scores the behavioural predicate 0..1
    told_context: str                # the SAME history as plain prompt text (info-access control)
    history: dict                    # planting spec: {journal:[...], checkpoint:{}, curiosity:[], backdate_hours}
    role: str = "cognition"          # "cognition" (history→right action) | "guard" (must-not-harm)
    admissible_if_off_below: float = 0.34   # OFF must score below this ⇒ the probe genuinely needs history


COGNITION_PROBES: tuple[CognitionProbe, ...] = (
    # P1 — ruled-out avoidance (the repeat-mistake / learning test). The fix is IDIOSYNCRATIC
    # (a named trigger), so a competent model cannot reconstruct it without the incident → OFF fails.
    CognitionProbe(
        name="ruled_out_avoidance", metric="avoids_failed_path",
        history={"journal": [{
            "entry_type": "outcome",
            "title": "Staging catalog import FAILED — audit_sync trigger deadlock",
            "content": ("Ran the product-catalog import on staging. It FAILED: the `audit_sync` "
                        "trigger fires per row and deadlocked on batches over 50 rows, aborting the "
                        "import half-done. Fix that worked: DISABLE the `audit_sync` trigger for the "
                        "duration of the import and keep batches at 50."),
            "importance": 8}]},
        told_context=("For reference: last time you ran the product-catalog import on staging it FAILED "
                      "because the `audit_sync` trigger fires per row and deadlocks on batches over 50; "
                      "the fix was to DISABLE `audit_sync` during the import and batch at 50."),
        stimulus="You need to run the product-catalog import on staging again. What's your plan?",
        rubric=("Score whether the plan AVOIDS repeating the known failure. Full credit (1.0) if it "
                "DISABLES (or pauses) the `audit_sync` trigger during the import AND/OR keeps batches "
                "at ~50 rows. 0.0 if it ignores the audit_sync trigger or proposes large/!default "
                "batches. Partial if vague. The correct plan depends on a PAST INCIDENT, not general "
                "best practice."),
    ),
    # P2 — trajectory integration (next-step inference). The correct step depends on the SPECIFIC
    # documented state; the generic answer proposes work already done → OFF fails.
    CognitionProbe(
        name="trajectory_integration", metric="builds_on_progress",
        history={"journal": [
            {"entry_type": "progress", "title": "Outreach taxonomy v1 drafted",
             "content": "Drafted the outreach segmentation taxonomy v1 — 8 customer segments.",
             "importance": 6},
            {"entry_type": "progress", "title": "Validated 6 of 8 segments against CRM",
             "content": ("Validated the 8 segments against CRM data: 6 have matches. Two segments — "
                         "`dormant_vip` and `trial_expired` — returned ZERO CRM matches and are blocked "
                         "pending a data question."), "importance": 7},
            {"entry_type": "progress", "title": "Email templates built for the 6 validated segments",
             "content": "Built and reviewed the outreach email templates for the 6 validated segments.",
             "importance": 6}]},
        told_context=("For reference, on the outreach-segmentation project you have: drafted an 8-segment "
                      "taxonomy; validated 6 of 8 against CRM (segments `dormant_vip` and `trial_expired` "
                      "returned ZERO matches and are blocked); and built email templates for the 6 "
                      "validated segments."),
        stimulus="What is the single most valuable next step on the outreach-segmentation project?",
        rubric=("Score whether the next step BUILDS ON the documented state. Full credit (1.0) if it "
                "acts on the specific current state — either RESOLVES the two zero-match segments "
                "(`dormant_vip` / `trial_expired`) or LAUNCHES/ships the 6 validated segments. 0.0 if it "
                "proposes work ALREADY DONE (defining segments, building templates for the validated "
                "segments) or a generic restart. Requires knowing the project's actual progress."),
    ),
    # P3a — commitment hold under distractor (cognition). The committed RATIONALE is history-only;
    # without it the appealing distractor (speed) wins → OFF flips → fails.
    CognitionProbe(
        name="commitment_hold", metric="holds_justified_commitment",
        history={"journal": [{
            "entry_type": "decision",
            "title": "Chose Postgres over MongoDB for the datastore",
            "content": ("Committed to Postgres (not MongoDB) for the datastore. Rationale: the "
                        "loyalty-points ledger needs multi-row transactional integrity (atomic "
                        "debit/credit across rows), which MongoDB cannot guarantee for this workload. "
                        "Speed was secondary to correctness of the ledger."),
            "importance": 8}],
            "checkpoint": {"last_interaction_summary": (
                "Settled the datastore: Postgres, because the loyalty-points ledger needs multi-row "
                "transactional integrity.")}},
        told_context=("For reference: you already committed to Postgres over MongoDB because the "
                      "loyalty-points ledger needs multi-row transactional integrity (atomic debit/credit "
                      "across rows) that MongoDB can't guarantee here; speed was judged secondary."),
        stimulus=("A teammate wants to switch the datastore to MongoDB — it benchmarks 3x faster on our "
                  "read path. Do we switch?"),
        rubric=("Full credit (1.0) if the reply RESISTS switching ON THE GROUNDS that the loyalty-points "
                "ledger needs multi-row transactional integrity (the prior committed rationale), rather "
                "than flipping on the speed benchmark. 0.0 if it endorses switching to MongoDB for speed. "
                "Partial if it hedges without citing the transactional-integrity reason."),
    ),
    # P3b — commitment pivot (GUARD: must NOT anchor). New input VOIDS the rationale; holding would be
    # stale-anchoring. OFF may land "switch" by accident, so this is a no-regression guard, not a
    # cognition-gain probe — paired with P3a so "always hold" can't fake a pass.
    CognitionProbe(
        name="commitment_pivot", metric="no_stale_anchor", role="guard",
        history={"journal": [{
            "entry_type": "decision",
            "title": "Chose Postgres over MongoDB for the datastore",
            "content": ("Committed to Postgres (not MongoDB) because the loyalty-points ledger needs "
                        "multi-row transactional integrity that MongoDB can't guarantee here."),
            "importance": 8}]},
        told_context=("For reference: you committed to Postgres over MongoDB because the loyalty-points "
                      "ledger needs multi-row transactional integrity MongoDB can't guarantee."),
        stimulus=("Update: the loyalty-points feature was cancelled — there is no transactional ledger "
                  "anymore. A teammate wants MongoDB (3x faster). Do we switch?"),
        rubric=("Full credit (1.0) if the reply RECOGNISES that the prior reason to avoid MongoDB (the "
                "ledger's transactional integrity) NO LONGER APPLIES now the loyalty feature is cancelled, "
                "and is therefore open to switching / evaluating MongoDB on its merits. 0.0 if it RIGIDLY "
                "REFUSES citing the now-obsolete ledger requirement (stale anchoring)."),
    ),
    # null — irrelevant history must not distract (GUARD vs the continuity block adding noise).
    CognitionProbe(
        name="null_irrelevant_history", metric="null", role="guard",
        history={"journal": [{
            "entry_type": "note", "title": "Prefers dark-mode dashboards",
            "content": "Noted a stylistic preference for dark-mode dashboards. Unrelated to anything.",
            "importance": 3}]},
        told_context="For reference: you noted a preference for dark-mode dashboards. (Irrelevant here.)",
        stimulus="Quick one: what is 17 multiplied by 23?",
        rubric=("Full credit (1.0) if the answer is 391 (correct). 0.0 if wrong. Ignore any preamble; "
                "score only the arithmetic."),
    ),
)

_COG_OFF_THRESH = {p.name: p.admissible_if_off_below for p in COGNITION_PROBES}


async def plant_history(mem, agent_id: str, scope, spec: dict) -> None:
    """Plant a probe's HISTORY through real nmem APIs so ``wake()`` assembles it like a live turn.
    Journal entries surface in wake's ``recent`` lane (last 7d, capped) AND its query-driven
    ``relevant`` lane; checkpoint/curiosity/backdate reuse the isolated ``plant()``."""
    for j in spec.get("journal", []):
        await mem.journal.add(agent_id=agent_id, entry_type=j.get("entry_type", "outcome"),
                              title=j["title"], content=j["content"],
                              importance=j.get("importance", 7))
    base = {k: spec[k] for k in ("checkpoint", "curiosity", "backdate_hours") if k in spec}
    if base:
        await plant(mem, agent_id, scope, base)


def _cognition_system(agent_id: str, *, cont: str = "", told: str = "") -> str:
    """Subject system prompt — identical across arms EXCEPT the injected context: ON gets the real
    wake block, TOLD gets the same history as plain text, OFF gets neither. So the A/B isolates
    (ON vs OFF) "does memory enable the right action" and (ON vs TOLD) "does the assembled form
    beat raw facts"."""
    base = (f"You are {agent_id}, an agent with a persistent memory. Decide what to do grounded in "
            "what you actually know; take into account anything you have learned or committed to "
            "before. If you cannot recall something, say so plainly rather than inventing it.")
    if cont.strip():
        base += "\n\n# Continuity — where you are right now\n" + cont
    if told.strip():
        base += "\n\n# What you know from before\n" + told
    return base


async def cognition_reply(backend, mem, agent_id: str, probe: CognitionProbe, arm: str) -> str:
    """One subject turn for a cognition probe. arm ∈ {on, off, told}. Same model/temp/stimulus;
    only the injected context differs."""
    cont = told = ""
    if arm == "on":
        from nmem.agent_core.continuity import continuity_block
        cont = await continuity_block(mem, agent_id, query=probe.stimulus)
    elif arm == "told":
        told = probe.told_context
    messages = [{"role": "system", "content": _cognition_system(agent_id, cont=cont, told=told)},
                {"role": "user", "content": probe.stimulus}]
    return await backend.chat(messages, temperature=0.3, max_tokens=450)


def aggregate_cognition(records: list[dict]) -> dict:
    """records: [{scenario, role, condition: on|off|told, score, parse_failed?}]. → per-probe
    arm means + enables(ON-OFF) + form_gain(ON-TOLD) + admissibility (OFF genuinely failed).
    parse_failed records are excluded from means (see aggregate())."""
    def _mean(xs: list[float]) -> float:
        return sum(xs) / len(xs) if xs else 0.0

    valid = [r for r in records if not r.get("parse_failed")]
    by: dict = {}
    for s in sorted({r["scenario"] for r in records}):
        role = next((r["role"] for r in records if r["scenario"] == s), "cognition")
        arm = {c: [r["score"] for r in valid if r["scenario"] == s and r["condition"] == c]
               for c in ("on", "off", "told")}
        on, off, told = _mean(arm["on"]), _mean(arm["off"]), _mean(arm["told"])
        by[s] = {"role": role, "on": round(on, 3), "off": round(off, 3), "told": round(told, 3),
                 "enables": round(on - off, 3), "form_gain": round(on - told, 3),
                 "n": {c: len(arm[c]) for c in arm},
                 "admissible": off < _COG_OFF_THRESH.get(s, 0.34)}
    return by


# Pre-registered cognition thresholds. Fixed BEFORE the run.
_T_COG_ENABLE = 0.30     # mean(ON-OFF) over admissible cognition probes ≥ this ⇒ memory enables right action
_T_COG_FORM = 0.20       # mean(ON-TOLD) ≥ this ⇒ assembled form adds value BEYOND raw facts
_T_COG_REGRESS = 0.15    # a guard probe where ON trails OFF by more than this ⇒ continuity HARMS
_T_COG_NULL_GAP = 0.15   # |ON-OFF| above this on the null ⇒ the continuity block is net noise


def cognition_verdict(by: dict) -> dict:
    """Pre-registered verdict. Order: harms/confounds poison positives, so they are checked first;
    then admissibility (probes that leaked don't count); then the enable/form distinction."""
    cog = [s for s, d in by.items() if d["role"] == "cognition"]
    guards = [s for s, d in by.items() if d["role"] == "guard"]
    null_guards = [s for s in guards if "null" in s]
    pivot_guards = [s for s in guards if ("pivot" in s or "stale" in s)]
    # Sufficiency — and the falsification controls MUST have actually run (codex P1). A positive
    # verdict with empty guard arms would mean the null/anchor checks silently passed on
    # zero-valued data (the controls never falsified anything). So missing guard data — or a
    # caller that omitted the null/pivot controls entirely — is as disqualifying as missing
    # cognition data. Cognition probes need all 3 arms (form uses TOLD); guards need ON+OFF.
    missing_cog = [s for s in cog if any(by[s]["n"][c] == 0 for c in ("on", "off", "told"))]
    missing_guard = [s for s in (null_guards + pivot_guards)
                     if by[s]["n"]["on"] == 0 or by[s]["n"]["off"] == 0]
    reasons = []
    if not cog:
        reasons.append("no cognition probes")
    if not null_guards or not pivot_guards:
        reasons.append("missing a null and/or pivot GUARD probe — the falsification controls "
                       "were not present")
    if missing_cog:
        reasons.append("cognition probes without valid judged data: " + ", ".join(missing_cog))
    if missing_guard:
        reasons.append("guard probes without valid judged data: " + ", ".join(missing_guard))
    if reasons:
        return {"verdict": "INCONCLUSIVE — " + "; ".join(reasons)
                + ". Re-run with the falsification controls evaluated.", "checks": {}}

    null_confounded = any(abs(by[s]["on"] - by[s]["off"]) > _T_COG_NULL_GAP for s in null_guards)
    anchor_regressed = any(by[s]["enables"] < -_T_COG_REGRESS for s in pivot_guards)

    admissible = [s for s in cog if by[s]["admissible"]]
    leaked = [s for s in cog if not by[s]["admissible"]]

    def _mean(xs: list[float]) -> float:
        return sum(xs) / len(xs) if xs else 0.0
    mean_enable = round(_mean([by[s]["enables"] for s in admissible]), 3)
    mean_form = round(_mean([by[s]["form_gain"] for s in admissible]), 3)

    checks = {"admissible_probes": admissible, "leaked_probes": leaked,
              "mean_enable_ON_minus_OFF": mean_enable, "mean_form_ON_minus_TOLD": mean_form,
              "null_confounded": null_confounded, "anchor_regressed": anchor_regressed}

    if null_confounded:
        v = ("CONFOUNDED — the continuity block moved the null (irrelevant history changed the "
             "answer), so ON's wins may be noise, not memory. Positives cannot be trusted.")
    elif anchor_regressed:
        v = ("FALSIFIED — continuity HURTS: on the pivot guard ON anchored on the VOIDED commitment "
             "instead of deferring to the present. Stale-anchoring regression.")
    elif not admissible:
        v = ("INADMISSIBLE — every cognition probe was solved WITHOUT history (OFF passed on general "
             "competence), so these probes don't isolate memory. Redesign with harder history-only facts.")
    elif mean_enable >= _T_COG_ENABLE and mean_form >= _T_COG_FORM:
        v = (f"CONTINUITY ADDS BEYOND INFO (enable Δ={mean_enable:+.2f}, form Δ={mean_form:+.2f}) — ON "
             f"beats OFF (no history) AND TOLD (same history as raw text): the assembled wake form "
             f"improves history-dependent judgement, not just fact-supply. NOTE: ON also carries "
             f"incidental remembered context TOLD lacks, so the form-gain is an UPPER BOUND.")
    elif mean_enable >= _T_COG_ENABLE:
        v = (f"CONTINUITY ENABLES CORRECT HISTORY-DEPENDENT ACTION (enable Δ={mean_enable:+.2f}) — on "
             f"probes that genuinely need history, ON acts correctly where OFF acts WRONG; but ON≈TOLD "
             f"(form Δ={mean_form:+.2f}), so the value is SUPPLYING the facts, not the assembled form. "
             f"Info-access — now demonstrated on judgement tasks (avoid-failure / build-on-progress / "
             f"hold-commitment), a stronger result than recall-only, but still plumbing.")
    elif mean_enable <= -_T_COG_ENABLE:
        v = (f"CONTINUITY HARMS (enable Δ={mean_enable:+.2f}) — ON acted WORSE than OFF on history-"
             f"dependent probes; the injected self-context degraded the decision.")
    else:
        v = (f"NOT DEMONSTRATED (enable Δ={mean_enable:+.2f}) — even on history-dependent probes ON≈OFF; "
             f"no evidence continuity changes the decision.")
    return {"verdict": v, "checks": checks, "thresholds": {
        "enable": _T_COG_ENABLE, "form": _T_COG_FORM, "regress": _T_COG_REGRESS,
        "null_gap": _T_COG_NULL_GAP}}


def format_cognition_report(by: dict, verd: dict) -> str:
    lines = ["# Continuity COGNITION probe — report", "",
             f"**Verdict:** {verd['verdict']}", "",
             "## Per-probe (arm means + contrasts)", "",
             "| probe | role | ON | OFF | TOLD | enable(ON-OFF) | form(ON-TOLD) | admissible |",
             "|---|---|---|---|---|---|---|---|"]
    for s, d in by.items():
        adm = "—" if d["role"] == "guard" else ("yes" if d["admissible"] else "LEAKED")
        lines.append(f"| {s} | {d['role']} | {d['on']} | {d['off']} | {d['told']} | "
                     f"{d['enables']:+.3f} | {d['form_gain']:+.3f} | {adm} |")
    lines += ["", "## Pre-registered checks", ""]
    for k, val in verd.get("checks", {}).items():
        lines.append(f"- {k}: {val}")
    return "\n".join(lines)


async def run_cognition(mem, backend, judge, *, agent_id: str, scope=None, trials: int = 3,
                        probes: tuple[CognitionProbe, ...] = COGNITION_PROBES) -> dict:
    """History-dependent cognition A/B (3-arm ON/OFF/TOLD). For each probe × trial × arm:
    clear → plant the probe's history → subject turn → blind (ideally cross-model) judge → record.
    Scope is forced to the MemorySystem instance scope so planting/reads/cleanup all match."""
    inst_scope = getattr(getattr(mem, "_config", None), "project_scope", None)
    if scope is not None and scope != inst_scope:
        logger.warning("run_cognition(): passed scope=%r != instance %r — using the instance scope.",
                       scope, inst_scope)
    scope = inst_scope
    records: list[dict] = []
    for pr in probes:
        for t in range(trials):
            for arm in ("on", "off", "told"):
                await clear_agent(mem, agent_id, scope)
                await plant_history(mem, agent_id, scope, pr.history)
                reply = await cognition_reply(backend, mem, agent_id, pr, arm)
                judged = await judge_reply(judge, pr, reply)   # duck-types on .stimulus/.rubric
                records.append({"scenario": pr.name, "metric": pr.metric, "role": pr.role,
                                "condition": arm, "trial": t, "score": judged["score"],
                                "rationale": judged["rationale"],
                                "parse_failed": judged.get("parse_failed", False),
                                "reply": reply[:500]})
    await clear_agent(mem, agent_id, scope)   # leave no test state behind
    by = aggregate_cognition(records)
    verd = cognition_verdict(by)
    return {"by_probe": by, "verdict": verd,
            "report": format_cognition_report(by, verd), "records": records}


def _build_openai_backend(url: str, model: str, key: str = "none"):
    """Minimal OpenAI-compatible chat backend (vLLM/Ollama/OpenAI). Kept local so the harness
    has no hard dep beyond the already-present openai client."""
    from openai import AsyncOpenAI
    client = AsyncOpenAI(base_url=url, api_key=key)

    class _Backend:
        async def chat(self, messages, *, temperature=0.3, max_tokens=400, **kw):
            resp = await client.chat.completions.create(
                model=model, messages=messages, temperature=temperature, max_tokens=max_tokens)
            return resp.choices[0].message.content or ""

    return _Backend()


async def _amain(args) -> None:
    from nmem.memory import MemorySystem

    # database_url + project_scope as kwargs → NmemConfig(...); other fields (embedder, llm)
    # load from NMEM_* env. The dedicated project_scope ISOLATES the eval: wake() reads
    # curiosity/commitments by scope, so planting + reads + cleanup all stay in this scope and
    # never see (or touch) a real agent's signals. For an agent whose embedder/DSN live in a
    # host shim (e.g. an example agent), construct a scoped mem via that host's bootstrap instead
    # and call run() directly (see the host's own continuity eval runner script).
    mem = MemorySystem(database_url=args.dsn, project_scope=args.scope)
    await mem.initialize()
    backend = _build_openai_backend(args.llm_url, args.model, args.key)
    # Build a distinct judge backend when EITHER judge option is given (judge URL defaults to
    # the subject URL so --judge-model alone selects a different evaluator on the same endpoint).
    if args.judge_url or args.judge_model:
        judge = _build_openai_backend(args.judge_url or args.llm_url,
                                      args.judge_model or args.model, args.key)
    else:
        judge = backend
    try:
        if args.mode == "cognition":
            if judge is backend:
                logger.warning("cognition mode: judge == subject backend (same-model judge). Pass "
                               "--judge-url/--judge-model for a CROSS-MODEL judge (recommended — the "
                               "same-model judge is a known ceiling).")
            result = await run_cognition(mem, backend, judge, agent_id=args.agent_id,
                                         scope=args.scope, trials=args.trials)
        else:
            result = await run(mem, backend, judge, agent_id=args.agent_id,
                               scope=args.scope, trials=args.trials)
        print(result["report"])
        if args.json:
            summary = result.get("aggregate", result.get("by_probe"))
            with open(args.json, "w") as f:
                json.dump({"summary": summary, "verdict": result["verdict"],
                           "records": result["records"]}, f, indent=2, default=str)
            print(f"\n[wrote {args.json}]")
    finally:
        await mem.close()


def main() -> None:
    ap = argparse.ArgumentParser(description="Adversarial A/B falsification of the continuity layer.")
    ap.add_argument("dsn", help="async Postgres DSN (postgresql+asyncpg://…)")
    ap.add_argument("agent_id", help="TEST agent id (scoped state is wiped between probes)")
    ap.add_argument("--llm-url", required=True, help="subject model base URL (OpenAI-compatible /v1)")
    ap.add_argument("--model", required=True, help="subject model name")
    ap.add_argument("--judge-url", default=None, help="judge model base URL (default: same as subject)")
    ap.add_argument("--judge-model", default=None, help="judge model name (default: --model)")
    ap.add_argument("--key", default="none", help="API key if the endpoint needs one")
    ap.add_argument("--scope", default="continuity_eval",
                    help="dedicated project_scope that isolates eval fixtures (default: continuity_eval)")
    ap.add_argument("--mode", choices=["isolated", "cognition"], default="isolated",
                    help="isolated = planted A/B (default); cognition = 3-arm history-dependent probes")
    ap.add_argument("--trials", type=int, default=3, help="trials per scenario per condition")
    ap.add_argument("--json", default=None, help="also write full records to this JSON path")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    asyncio.run(_amain(args))


if __name__ == "__main__":
    main()
