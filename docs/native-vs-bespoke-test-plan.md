# Native-vs-bespoke validation plan (michelle-ai)

**Status:** executing. **Written:** 2026-09-07. **Companion:** [nmem-agent-core-plan.md](./nmem-agent-core-plan.md).
Goal: switch on native nmem capabilities that would replace michelle's bespoke code, compare
outcomes/efficiency against the bespoke baseline, **and upstream any bespoke advantage into nmem**
so the library ends up better than either — then thin michelle.

## Methodology
- **Shadow-both-tag-compare** (primary): run bespoke + native together in the one live michelle,
  tag each artifact by source (goal `source_ref` / metadata; skill origin), compare the two
  populations in the *same world state*. No temporal drift, no second instance. (producer, skills)
- **Sequential windows**: measure a window bespoke-on, then native-on. For runtime *behavior* that
  can't be tagged (drive flood/no-op rate).
- **Second instance** (escalation): own isolated DB, native-vs-bespoke, only if a shadow result is
  ambiguous. Sandbox is the shared bottleneck → serialize.
- **Blind LLM-judge**: for the qualitative axis (goal specificity/verifiability/relevance; skill
  usefulness) — score bespoke vs native artifacts without revealing source.

## Decision rule
Adopt native if it **matches-or-beats** bespoke on {quality, yield, downstream achievement} at
**≤ cost**. Where **bespoke wins a dimension, upstream that mechanism into the owning nmem repo**
(michelle's code as the reference), re-test. nmem ends up better; michelle thins.

## Instrumentation (reusable)
- `michelle-ai/tools/metrics.sql` — DB snapshot (goals×status, achievement, dedup, finding merit,
  skills, access/promotion). Run: `docker exec -i michelle_pg psql -U michelle -d michelle_ai -f -`.
- Drive behavior from logs: `journalctl … | grep -c 'External pressure injected' / 'activated 0 underexplored'`.
- **Consolidation trigger** (needed to measure promotion): `mem._consolidator.run_full_cycle()`
  (MemorySystem, memory.py:194). Add as `POST /admin/consolidate` on michelle for on-demand cycles.
- Blind LLM-judge harness (Test 2/3).

## BESPOKE BASELINE — captured 2026-09-07 03:39Z
| Metric | Value |
|---|---|
| Goals achieved / failed / pending (drive_intent) | 111 / 10 / 1 |
| Achievement rate | 91.7% (overstated — includes salvaged non-findings) |
| Distinct-objective rate (dedup) | 92.6% |
| **Verified-finding yield** (fact/confirmed vs observation/inferred) | **82 / 25 = 76%** ← the real quality signal |
| Skills total / worked / avg-trials / max-trials | 351 / 221 / 1.13 / 7 |
| **Skill sprawl** | ~3 skills/session, avg 1.13 trials → dedup under-coalesces (bespoke weakness) |
| LTM / sym_nodes / procedures | 0 / 0 / 351 |
| Promotable-by-access (access≥5) / max_access | 18 / 19 |
| Promotion status | primed (82 fact-tagged + 18 access≥5) but **consolidation not yet run** |

## Test cards (sequenced by dependency)

### Test 1 — Drive foundation *(first; others sit on a healthy drive system)*
- Switch on: `NMEM_SYM_DRIVES_HONEST_DISCHARGE=true`, `NMEM_SYM_DRIVES_WAKE_MODE=pressure`
- Replaces: the starvation guard in `cognition._drive_loop`
- Method: sequential windows. **Before (bespoke, guard on): 0 pressure-injections, 0 no-op explores.**
- Metric: pressure-injection rate, no-op-`explore` rate, idle CPU, useful drive intents produced.
- Decision: no flood without the guard → delete guard. Flood persists → guard logic is a native
  fix candidate for nmem-sym drives (upstream). Test both cold-start (empty graph) and warm.

### Test 2 — Goal producer *(biggest payoff: curiosity.py = 140 LOC)*
- Switch on: `NMEM_SYM_CONCERNS_ENABLED`, `NMEM_SYM_CURIOSITY_CONCERNS`, `NMEM_SYM_DRIVES_CREATE_GOALS`
- Replaces: `curiosity.py`
- Method: shadow-both-tag (native concern-derived vs curiosity `source_ref`). Metrics per source:
  goals/cycle, dedup, goal quality (LLM-judge), downstream achievement, fact/confirmed yield, LLM cost.
- Decision: native ≥ bespoke → thin curiosity. Bespoke's LLM follow-ups richer → **upstream
  `DRIVES_GOAL_LLM_ENRICH`** into nmem-sym (curiosity.py = reference), re-test.

#### Test 2 — RESULT (2026-09-07): native producer CANNOT replace curiosity.py (two independent reasons)
Determined analytically from live DB state — **deliberately did NOT flip `DRIVES_CREATE_GOALS`
on live**, because it would spawn mis-targeted web pursuits that pollute michelle's real
goal/episode/memory store (founder directive on live systems), and the data makes the outcome
deterministic. Evidence:

1. **Threshold-starved.** michelle has only **5 curiosity signals, all `pending`, max
   `composite_score` = 0.43** — every one below the 0.5 `CURIOSITY_CONCERN_MIN_COMPOSITE` mirror
   floor. With defaults, `curiosity_concerns` mirrors **nothing** → 0 concerns → 0 targeted
   intents → **0 native goals** (`_maybe_create_goal_from_intent` returns early without a
   `target.key`). No `symbol_concerns` table exists yet (never enabled).
2. **Signal-TYPE mismatch (the deeper reason).** All 5 signals are `graph_hypothesis`:
   *"[Spwig] and [research Spwig] have 0.82 semantic similarity but no graph connection."* These
   are observations about michelle's **own graph topology**, not the world. The native goal would
   be *"Satisfy novelty drive: [Spwig] and [research Spwig] have 0.82 similarity but no graph
   connection"* — and her pursuit loop would fire the **web sandbox** at it. Category error: you
   can't web-research your own memory's missing edges.

Bespoke `curiosity.py` produces exactly what her web actuator needs — concrete, world-directed,
independently-verifiable questions grounded in objectives+entities+prior findings ("What are the
specific terms of the Spwig AGPL license?", "What product categories/pricing does CocosBotanica
use?"). 137 goals produced this way.

**Verdict: KEEP curiosity.py.** It is not surplus — it does work the native chain structurally
cannot for a web-browsing agent. Two upstream implications instead of deletion:
- **`DRIVES_GOAL_LLM_ENRICH` (nmem-sym):** teach the native goal path to LLM-enrich a concern into
  a concrete, actuator-appropriate objective — `curiosity.py` is the reference implementation. Then
  the native producer becomes useful for ANY agent and curiosity's logic moves upstream (matches
  the "improve the nmem repos, don't fork logic into each bot" principle).
- **graph-consolidation actuator (michelle/nmem-act):** `graph_hypothesis` signals are legitimate
  drivers for an INTERNAL action ("should I link these two similar-but-unconnected nodes?"), not a
  web pursuit. They want a different actuator, not the sandbox.
- Side-finding: michelle's hypothesis machinery currently emits ONLY graph-topology hypotheses
  (5 total) — worth a separate look at why she generates so few *world* hypotheses.

### Test 3 — Proactive recall + skill capture
- Switch on: `NMEM_AUTONOMY__ENABLED`, `NMEM_AUTONOMY__PROACTIVE_RETRIEVE`, `NMEM_AUTONOMY__AUTO_CAPTURE_SKILLS`
- Replaces: recall-before-pursuit + `tool_learning` capture (note the 351-skill sprawl to beat)
- Method: shadow-both-tag skills by origin; relevance of `memory.surfaced` vs `memory.recall`.
- Decision: reflection likely beats heuristic capture on tool-use → **upstream action-trace
  reflection into nmem-act's outcome sink** (`ACT_LLM_REFLECT_ENABLED`).

#### Test 3 — RESULT (2026-09-07): native autonomy CANNOT replace recall/tool_learning; fix the sprawl upstream
Same structural pattern as Test 2 — the native feature is keyed on a model that doesn't match
michelle's actuation reality. Determined analytically (no flag flip needed; `proactive_retrieve`
would only have run wasteful searches, `auto_capture_skills` would have captured 0):

- **`auto_capture_skills` fires on ZERO of michelle's entries.** Her journal is entirely
  `fact|pursuit` (92), `observation|pursuit` (27), `judgment|hypothesis` (3) — none in the default
  `skill_entry_types` (`decision/outcome/retro/lesson`). Native capture is keyed on entry types she
  doesn't produce, AND only sees the **journal** — never the tool-use **steps** where her real
  skills live. It also captures verbatim, `worked=True`, no reflection (heuristic; "Phase 1.5").
- **`proactive_retrieve` emits `memory.surfaced` that NOTHING consumes.** michelle's viz_bridge
  subscribes to `journal.added`/`ltm.saved`/`shared.saved`, not `memory.surfaced`. Her pursuit loop
  PULLs (targeted `memory.recall(obj)` right before acting). Native surface is PUSH-on-write — a
  conversational-agent feature (offer context mid-dialogue), structurally mismatched to a goal loop.
- **`tool_learning` captures the genuinely valuable thing** the native path can't: LLM-reflective
  browser procedures from actual sandbox steps ("verify URL after a click", "wait for skeleton
  screens before acting", "ctrl+a then backspace after ctrl+l to clear the address bar"). They DO
  coalesce with reuse (top skills 9×/8×/5×).

**Verdict: KEEP recall-before-pursuit AND tool_learning.** Neither is surplus. The real defect is
skill **sprawl**: 396 skills, avg 1.15 trials — semantic near-duplicates aren't merging
("repetitive clicking" / "repetitive scroll" / "repetitive scrolling" = one lesson, three phrasings).
Upstream fixes (benefit every agent), not deletions:
- **semantic coalescing in `nmem.skills.record`:** merge a new `what` into an existing skill above a
  cosine threshold (bump trial/success) instead of inserting a near-dup. This is THE sprawl fix.
- **`ACT_LLM_REFLECT_ENABLED` (nmem-act):** make LLM-reflective capture off the action trace a
  native option in the outcome sink — `tool_learning` is the reference implementation. Then reflective
  skill capture (keyed on STEPS, not journal type) is available to any actuating agent.

### Test 4 — Pursuit loop → nmem-act *(needs a SandboxExecutor adapter; last)*
- Register michelle's sandbox as an nmem-act executor. Second-instance A/B on achievement +
  goal-lifecycle correctness (infra-vs-failed / unclaim / salvage = acceptance spec).

## Notes
- Each flag flip needs a michelle restart (resets the 6h consolidation timer) — use the on-demand
  `/admin/consolidate` to measure promotion without waiting.
- michelle stays the live proving ground throughout; DJ-AI shares these libs → land+bake on
  michelle before any DJ-AI rollout.

## SWEEP CONCLUSION (2026-09-07) — all tests done
Recurring finding across Tests 2 & 3: **the bespoke code is NOT surplus.** Each native feature is
keyed on a model that doesn't match michelle's actuation reality (world-signals vs graph-topology
signals; PUSH-on-write vs PULL-at-decision; journal-type capture vs tool-STEP capture). So the
`nmem-agent-core` move is NOT "flip native flags + delete bespoke" — it's **upstream the bespoke
advantage into the nmem libs so every agent gets it** (the founder's stated principle). Keep
`curiosity.py`, `recall`-before-pursuit, and `tool_learning` in the core; make the libs able to
do what they do.

### Upstream backlog (ranked; all validated by this sweep + the codex review)
1. **honest_discharge for outward-actuated drives (nmem-sym)** — codex #3 + Test 1. An outward
   drive actuated via nmem-act must discharge on the *actuator's real outcome*, not internal
   self-satisfaction. Highest confidence (validated twice); correctness gap; benefits michelle+DJ-AI.
2. **semantic coalescing in `nmem.skills.record` (nmem)** — Test 3. Merge near-duplicate `what`
   above a cosine threshold instead of inserting. THE fix for skill sprawl (396 @ 1.15 trials).
3. **`ACT_LLM_REFLECT_ENABLED` (nmem-act)** — Test 3. LLM-reflective capture off the action trace
   as a native outcome-sink option; `tool_learning` is the reference. Reflective, STEP-keyed capture
   for any actuating agent.
4. **`DRIVES_GOAL_LLM_ENRICH` (nmem-sym)** — Test 2. LLM-enrich a concern into a concrete,
   actuator-appropriate objective; `curiosity.py` is the reference. Makes the native producer usable.
5. **graph-consolidation actuator (michelle/nmem-act)** — Test 2. `graph_hypothesis` curiosity
   signals want an INTERNAL "link these two similar-but-unconnected nodes?" action, not a web pursuit.
- Side-finding: michelle emits only graph-topology hypotheses (5) — why so few *world* hypotheses?
