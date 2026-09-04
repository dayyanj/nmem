# Executive Experiential Loop — Build Log

Running record of what actually shipped per slice — **divergences from
[executive-experiential-loop.md](executive-experiential-loop.md)** and codex peer-review
outcomes. The end-of-build **critique** (`executive-experiential-loop-critique.md`) is
written last and draws from this log so it reflects the real build, not the plan.

Process per feature: implement → tests → `codex review --uncommitted` (bugs +
performance) → address findings → re-review until clean.

---

## Slice A1 — Honest, outcome-gated discharge ✅

**What shipped.** A drive is now discharged in proportion to what its action actually
achieved. `Drive.relieve(amount=None, outcome_strength=1.0)` scales the drop; the
`DriveAccumulator` reads each intent handler's return as an outcome_strength in
[0, 1] (max across handlers) and scales relief by it; the bridge's outward stub
handlers (`ground_sensory`, `communicate`) report `0.0` so a drive is only relieved
to the extent a *real* host handler satisfied it. All gated by a single new flag
`NMEM_SYM_DRIVES_HONEST_DISCHARGE` (default off → byte-identical to pre-A1).

**Divergences from the design doc:**
1. **Bridge stubs return `0.0` rather than `raise`.** The doc said "stub handlers
   raise (mirror `_do_recall`)." Instead the composable outward stubs report a `0.0`
   outcome strength. Rationale: `communicate`/`ground_sensory` are *meant* to be
   satisfied by a co-registered host handler; with `max()` across handlers, a real
   host handler still discharges, while raising would inflate `errors_by_subsystem`
   and emit tracebacks for an expected "no built-in work" path. `_do_recall` keeps
   raising because it is a terminal (non-composable) no-op.
2. **One flag gates both halves.** The flag governs *both* the accumulator's
   interpretation of return values *and* the bridge stubs — not just the stubs.
   Forced by codex P2a (below): interpreting return values unconditionally broke the
   byte-identical-off-state invariant for handlers that return a status value.
3. **Added `_coerce_outcome_strength()` helper**, hardened against non-numeric, NaN,
   and ±inf returns (not in the doc; forced by codex P2b/P2c).

**Codex peer review — 3 P2s found & fixed (each with a regression test):**
- **P2a — interpretation not flag-gated.** With the flag off, a pre-A1 handler that
  returns `False`/`0` as a status would get zero relief instead of the promised full
  discharge. Fixed: interpret return values only when the flag is on. Test:
  `test_flag_off_ignores_return_value_full_relief`.
- **P2b — success marked before parse.** `handler_succeeded=True` was set before
  `float(result)`; a non-numeric return raised inside the try, was logged as a
  handler failure, and `outcome_strength` fell back to `1.0` (full discharge). Fixed:
  separate the handler call from the parse; a ran-but-uninterpretable return → `0.0`.
  Test: `test_non_numeric_return_under_honest_does_not_relieve`.
- **P2c — NaN passes the clamp.** `max(0, min(1, nan)) == 1.0` in CPython, so a NaN
  strength fully discharged. Fixed: `_coerce_outcome_strength` rejects non-finite
  values. Tests: `test_coerce_outcome_strength_clamps_and_rejects`,
  `test_nan_return_under_honest_does_not_relieve`.
- Final pass: **clean** ("no actionable defects").

**Tests / files.** New `tests/test_honest_discharge.py`; 99 passed across
honest_discharge + drives + bridge + concerns + recall (byte-identical off-state
locked by regression). Changed: `src/nmem_sym/{drives.py,bridge.py,config.py}`.

**Open questions for the critique.** Does outcome-gated relief change arbiter
dynamics enough (drives that fail to discharge re-fire after cooldown) to warrant a
per-drive "give up / escalate to a goal" path? (Relates to A5.)

---

## Slice A2 — Utility-weighted plasticity ✅  (first falsifiable claim — PASS)

**What shipped.** Procedures now carry a `reward` EWMA (schema.sql + migration 013 +
standalone ALTER). On a goal RESOLUTION the procedures pursued toward it record a
goal outcome via `reward_procedures`: **credit on achievement** (success, utility =
`progress`) and a **failed trial on abandonment** (success=False, utility 0).
`find_matching_procedures` ranks by `reward` (after myelinated, before similarity)
when `NMEM_SYM_UTILITY_PLASTICITY=1`. All off by default → byte-identical.

**The benchmark did its job — it falsified the naive design twice.** The claim is
gated on `benchmarks/utility_plasticity_bench.py` (real Postgres, vLLM-free; N
situations × M tie-similarity candidates, one effective; epsilon-greedy
select→outcome→reward; PASS iff utility-arm final success − epistemic ≥ 0.15). First
two runs FAILED and exposed real bugs mock-pool tests could never catch:
1. **Non-discriminative reward.** Positive-only credit (reward only on achievement)
   made greedy retrieval lock onto the first-rewarded procedure — utility arm ended
   up *worse* than epistemic. Fix: a **failure/trial signal** — `reward_procedures`
   gained `success: bool`; abandonment records a failed trial (utility 0) so `reward`
   tracks the achievement RATE, not cumulative credit. A mis-locked decoy now decays.
3. **Int-inference EWMA bug.** `reward = (1 - $2) * reward + $2 * $3` — Postgres
   inferred `$2` (alpha 0.3) as int4 from the `1 - $2` context and truncated it to 0,
   so the EWMA never moved (`reward` stayed 0.0 while counts updated). Fix: explicit
   `::float8` casts. Only a live DB surfaced this; the mock-pool unit test asserts the
   SQL string but never executes it.
Final run: **PASS, margin +0.478** (utility 0.79 vs epistemic 0.31; visible learning
curve 0.35→0.85).

**Divergences from the design doc:**
1. **Symmetric outcome signal** (credit + failed-trial), not just "reinforce on
   achievement" — required for the utility signal to be a rate (benchmark-forced).
2. **Threshold-before-LIMIT** in `find` when the flag is on (codex P2): apply the
   similarity threshold in SQL before LIMIT so reward-ordering can't fill the result
   set with sub-threshold rows and drop valid matches.
3. **Edge-path LTP deferred to Slice C.** A2 reinforces *procedures* (the
   benchmark-relevant target); goals don't carry an explicit causal edge-path, so
   edge-level utility LTP waits for the Episode object (Slice C), which will.
4. **Benchmark lives in `nmem-sym/benchmarks/`**, not nmem-bench — co-located with
   the code under test, runnable in the nmem-sym venv, real-Postgres, vLLM-free.
   nmem-bench is a heavyweight Claude-Code/vLLM memory harness with no cognition
   surface; a pure procedure-plasticity bench fits better next to nmem-sym.

**Codex peer review — P2s found & fixed (each with a regression test):** LIMIT-before-
threshold; schema.sql out of sync with the new column (manual `psql -f schema.sql`
install path); mirrored (`resolve_on_action`) concern fully resolved on a partial
outcome — now proportional (this is A1-adjacent, surfaced in the combined diff);
standalone `setup()` path missing the column (added `REWARD_MIGRATION_SQL`). v2
(failure signal + float cast + benchmark): **clean**.

**Tests / files.** New `tests/test_utility_plasticity.py` (mock-pool) +
`benchmarks/utility_plasticity_bench.py` (real-DB). 466 passed, 2 skipped. Changed:
`src/nmem_sym/{procedural.py,goals.py,config.py,schema.sql}` + `migrations/013_procedure_reward.sql`.

**Lesson for the critique.** Mock-pool unit tests cannot catch SQL-semantics bugs
(type inference, EWMA math). The benchmark is A2's real-DB gate and should be run in
CI against a Postgres. Consider a `NMEM_SYM_TEST_DSN`-gated integration test for the
reward EWMA specifically.

---

## Slice A3 — Surface intents / drive-state ✅

**What shipped.** The drive system's fired intents were dropped by the MCP tick loop
and its pressures weren't on any agent-facing surface. A3 adds three public bridge
methods — `dominant_drive()`, `peek_ready()` (read-only introspection) and
`on_drive_intent(handler)` (a clean public channel for a host executor to RECEIVE
fired intents, replacing reaching into `bridge._drives`) — plus a read-only
`memory_drive_state` MCP tool exposing the pressure landscape (drives + dominant +
next-to-fire). `on_drive_intent` is precisely the Slice-C executor seam.

**Divergences from the design doc:**
1. **api.py NOT extended.** The doc said "expose via mcp_tools.py and api.py", but
   drives are **bridge-scoped** while `api.py` is the `SymbolGraph` surface. The
   bridge's Python methods (`drive_state`/`inject_pressure`/`dominant_drive`/
   `peek_ready`/`on_drive_intent`) are the host API, and the MCP tool serves agents —
   adding drive state to the graph API would be an awkward layering violation.

**Codex peer review:** clean on the first pass. Tests: `tests/test_drive_surface.py`
(bridge wrappers + MCP tool, graceful-degradation covered) + updated the MCP tool
count test (7 → 8). 473 passed, 2 skipped.

---

## Slice A4 — Joint hypothesis posterior ✅

**What shipped.** `symbol_hypotheses.posterior` (schema.sql + migration 014) holds a
normalised probability over a hypothesis's competing set. A stable `_softmax` +
`renormalize_competitor_posteriors` maintain a LIVE distribution over the still-
`speculative` members of `{self} ∪ competes_with`, re-normalised after each
auto-grounding transition; `posterior` is surfaced in `memory_hypothesis_list` /
`_explain`. Gated by `NMEM_SYM_HYPOTHESIS_POSTERIOR` (off → posterior stays NULL, no
extra queries). This is the substrate Slice C uses to pick experiments that
discriminate between competitors.

**Divergences from the design doc:** the posterior is a live distribution over
**speculative** competitors only — resolved members (grounded/disputed/superseded/
archived) are cleared to NULL. The doc said "softmax over the competitor set"; the
speculative-only refinement was forced by codex (below).

**Codex peer review — 1 P2, fixed:** the renormalization ran right after the
evidence update but *before* the grounding transition, so when a hypothesis grounded
and `supersede_competitors()` marked its rivals `superseded`, those rows kept the
stale posterior just written — `memory_hypothesis_list/explain` would surface
probabilities for hypotheses no longer in the race. Fix: moved the call to *after*
the transition + supersession, softmax over speculative members only, clear resolved
members to NULL. Re-review: **clean**. Tests: `tests/test_hypothesis_posterior.py`
(pure softmax + DB flow incl. the resolved-clearing regression). 484 passed.

---

## Slice A5 — Revive drives → goals ✅  (Slice A complete)

**What shipped.** Reconnected the dead drives→goals edge: revived
`create_goal_from_intent` (now carries the targeting concern's key for dedup), added
the missing `bridge.inject_goal()`, and wired the arbiter (`_handle_drive_intent`)
to `_maybe_create_goal_from_intent` — a **targeted** drive intent (one carrying a
specific festering concern) becomes a durable, decomposable `drive_intent` goal,
deduped per concern. Gated by `DRIVES_CREATE_GOALS` **and** `GOALS_ENABLED` (off →
byte-identical). This closes the A5→A2 loop: a drive-born goal, once achieved,
triggers A2's utility reward on the procedures that achieved it.

**Divergences from the design doc:**
1. **"Chronic" = carries a targeted concern**, not a new N-consecutive-cycles
   counter. A concern already *is* accumulated specific pressure, so targeting is the
   chronic signal — no new state to track. Diffuse pressure keeps discharging inward.
2. **Additionally gated on `GOALS_ENABLED`** (codex P2) — a drive goal is only
   meaningful when the goals subsystem exists to pursue it.

**Codex peer review — 2 P2s:**
- **[valid] Missing goals table / orphan goals when the goals subsystem is off.**
  Fixed by gating on `GOALS_ENABLED`. Test: `test_no_goal_when_goals_subsystem_disabled`.
- **[FALSE POSITIVE] "drive_state `ready` is a bound method".** `Drive.ready` is a
  `@property`, so `to_dict()['ready']` is a `bool` — verified empirically
  (`state()['coherence']['ready'] is False`). No change made; codex agreed on
  re-review. *Lesson for the critique: verify, don't reflexively apply — one review
  finding across the build was wrong.*

**Tests:** `tests/test_drive_goals.py` (10). 494 passed, 2 skipped.

---

## Slice A — summary

Five brain-side rails, all opt-in / defaults-OFF / byte-identical off-state /
codex-reviewed clean; ~13 codex P2s found-and-fixed across the slice (plus one false
positive caught by verification), one falsifiable benchmark (A2) PASS. The rails:
honest outcome-gated discharge (A1), a utility signal that tracks achievement rate
(A2), the drive-state/intent surface + host intent channel (A3), a shared posterior
over competitors (A4), and drives→goals→(A2 reward) reconnected (A5). Next: **Slice
B/C** — the install-agnostic executor contract + reference runner that consumes
`on_drive_intent` and closes the outward loop.

---

## Slice B — `nmem-act`: install-agnostic executor package ✅  (new public repo)

**What shipped.** A **new standalone repo** at `/mnt/nas_projects/apps/nmem-act`
(0.1.0, MIT, **zero runtime dependencies**, git-initialised on `main`), built to
public-repo standards from the start (LICENSE, README, CHANGELOG, .gitignore,
hatchling/src layout, ruff, 32 tests). It defines the "hands" layer:
- **Contract** (`types.py`): `ActionProposal` (propose-only), `Outcome` /
  `OutcomeStatus`, `UtilityVector` (multi-dimensional reward + weighted `.scalar()`),
  `Hypothesis`, `Episode`, `CapabilityClass`.
- **`ActionExecutor`** protocol — the pluggable seam.
- **`AutonomyGate`** — three tiers (`read_only` default / `tiered` / `full`),
  fail-closed approval for high-risk.
- **`ActionRegistry`** + **`ReferenceRunner`** — gate → approval → predict →
  dispatch → outcome → sink; never raises for ordinary failures.
- Read-only built-ins + `default_registry()`.

**Divergences from the design doc:**
1. **The contract types live in `nmem-act`, not nmem-sym** (the doc's B1 put them in
   nmem-sym). Rationale: install-agnostic + the existing no-import-coupling bridge
   philosophy — nmem-sym stays propose-only and emits proposals as *data*; nmem-act
   depends on nothing in the ecosystem. The nmem-sym-side `symbol_episodes` +
   `record_action_outcome` + `experiment.proposed` emission is **Slice C**.
2. **Reference runner ships read-only built-ins only** (`noop`); mutating/high-risk
   actuators are host-registered extensions (safe public default, matches the
   `read_only` autonomy default). DJ-AI's actuators can be lifted in later.
3. **Registered capability class is authoritative** — a proposal may escalate the
   gate but never downgrade it (safety refinement from codex P1).

**Codex peer review — 1 P1 + 2 P2s, each fixed with a regression test:**
- **[P1] capability-downgrade bypass** — the gate checked the *proposal's* declared
  `capability_class` (default `read_only`), so `ActionProposal(action_type="mutate")`
  could slip a registered MUTATING handler past a `read_only` gate. Fixed: resolve
  the action first, gate on the *most-severe* of (registered, declared).
- **[P2] blank proposal ids never filled** → outcomes indistinguishable to the sink.
  Fixed: fill a blank id once at the top of `execute()`.
- **[P2] approval hook that raises escaped** `execute()` (contract says encode
  failures, don't raise). Fixed: wrap and fail closed (BLOCKED), sink still fires.
- Re-review: **clean** ("consistent with the documented safety model").

**Remaining — Slice C:** close the loop end-to-end — nmem-sym builds an
`ActionProposal` from a fired outward intent (via `on_drive_intent`), a thin adapter
drives the nmem-act runner, and the `Outcome` flows back (`outcome.utility.scalar()`
as the A1 discharge strength) into a `symbol_episodes` record + A2's utility reward.

---

## Slice C — Close the loop ✅  (executive experiential loop complete)

**What shipped.**
- **C1 (nmem-act):** `make_intent_handler` + `default_proposal_from_intent` +
  `default_strength` — a **duck-typed** adapter that maps a fired intent →
  `ActionProposal` → any `ActionExecutor` → an `outcome_strength` in `[0, 1]` for the
  drive's honest discharge (A1). `examples/closed_loop.py` + a smoke test. 42 tests.
- **C2 (nmem-sym):** `symbol_episodes` (migration 015 + schema.sql) +
  `record_action_outcome` (persists a first-class Episode and feeds achieved utility
  into procedure plasticity A2; non-success forces utility 0.0) + `bridge.record_action_outcome`
  (fail-open, ensures the table on a standalone graph). 10 tests.
- **End-to-end VERIFIED against real Postgres:** a fired intent → nmem-act gate →
  actuator → outcome → nmem-sym `record_action_outcome` → a persisted
  `symbol_episodes` row (auto-filled id, `source=drive:novelty`, `status=success`,
  utility vector), with the discharge strength returned to the drive.

**Divergences from the design doc:**
1. **Adapter is duck-typed + host-side** (no import coupling either way), and the
   episode is recorded via the runner's **`outcome_sink`** (one recording path), not
   a separate adapter callback.
2. **Two complementary reward paths** now exist — A5 (goal achievement) and C2
   (action outcome). The host controls which fires to avoid double-counting.

**Codex peer review:** C1 clean; C2 fixed 2 P2s (non-success must not raise reward;
bridge boundary must fail open).

**The closed loop:** drive pressure (A1) → outward intent surfaced on
`on_drive_intent` (A3) → adapter builds an `ActionProposal` → nmem-act gates +
executes (autonomy tiers) → `Outcome` → `record_action_outcome` → `symbol_episodes`
+ A2 utility reward + **honest, outcome-proportional discharge** (A1 strength). The
system can now act to learn.

See `executive-experiential-loop-critique.md` for the adversarial assessment of what
this does and does *not* yet achieve.

---

## Evidence #1 — A2 on real embeddings ✅  (critique concern #1)

The critique's top concern: A2's win was a *synthetic-bandit* result (identical
per-situation vectors → similarity gave zero signal, so reward trivially decided). The
first evidence step re-runs the claim with **real MiniLM embeddings** — candidate
procedures carry genuine, graded, noisy similarity, and which candidate is *effective*
is **independent of similarity** (so similarity is a real competing signal that gives
no information about what actually works). Per-situation isolation scopes `find()` to a
matched candidate set (no cross-situation retrieval artifacts).
`benchmarks/utility_plasticity_bench.py --embeddings real`.

**Result: PASS, margin +0.412** (utility 0.716 vs epistemic 0.303; epistemic sits at
chance because similarity is uninformative about effectiveness). The utility arm learns
(0.38 → 0.73) — reward-ranking overrides a misleading real similarity order, validating
the A2 design choice (reward *before* similarity in `find()`). The margin barely
dropped from synthetic (0.478 → 0.412), so the result is robust to real embedding
geometry. Codex-clean.

**What this does and doesn't show.** It shows utility-weighting helps when retrieval
similarity doesn't already tell you which procedure works (the realistic, interesting
regime) — with real embeddings, not toy vectors. It still does **not** use real
compiled procedures, a real task, or an LLM — that's the remaining, heavier evidence
(critique #2: a real actuator on a real scenario; and a full nmem-bench task run).

## Deferred design — pressure-driven heartbeat

Captured `pressure-driven-heartbeat.md` (owner decision: the loop should fire on
accumulated drive pressure, not a fixed clock). The design: event wake +
compute-on-read lazy decay + a self-rescheduling next-deadline timer — need-driven,
homeostasis preserved, no polling. Opt-in (`NMEM_SYM_DRIVES_WAKE_MODE=event`), built
as its own slice after the evidence track.

---

## Evidence #2 — a real action through the loop ✅  (critique concern #2)

The critique's #2: nmem-act shipped only a `noop`; the execution path was never proven
with a real, consequential action. Evidence #2 ships the first real actuator and runs
the loop end-to-end against the live internet + Postgres.

- **nmem-act `actions/web.py`** — `http_get`, a real read-only HTTP actuator
  (zero-dependency: stdlib urllib in a worker thread). NOT auto-registered; the host
  opts in via `http_get_action()`. Because the loop's proposals may be autonomous, it
  is hardened against untrusted params: http/https only; SSRF guard blocks all
  non-global hosts (private/loopback/link-local/reserved/CGNAT) and re-validates every
  redirect hop; `allow_private` is host config (baked into the action), never a
  proposal param; hard caps on `max_bytes` + `timeout`; real non-2xx observed as a
  failed fetch; all malformed input returned as a failed `ActionResult`.
- **nmem-sym `benchmarks/real_action_loop.py`** — the cross-repo evidence harness.

**Verified end-to-end (real HTTPS + real DB write):**
- `https://example.com` → `200` → **success episode persisted**, discharge strength
  **1.0**. Curiosity → gated real HTTP GET → outcome → `symbol_episodes` row. The loop
  performs a real action and learns from it.
- **Safety holds with real actions:** asked to hit the cloud metadata endpoint
  `169.254.169.254`, the SSRF guard **refuses** → honest **failure**, discharge **0.0**
  (the need survives — A1's honest discharge working with a real action), failed
  episode still persisted.
- A real `404` is **observed** (`-> 404`, honest failure), not lost as a transport error.

**Codex peer review — thorough (security-sensitive, untrusted input):** 2 SSRF P1s
(redirect-bypass; CGNAT/non-global via `is_global`) + `allow_private`-as-host-config P1
+ param-hardening P2s (byte cap, timeout cap, non-2xx observation, invalid url/number
handling). All fixed with regression tests. 65 nmem-act tests; re-review **clean**.

**What this shows / doesn't.** The execution path is now proven with a **real,
consequential** action, safely gated. It's a *read-only* action; a **gated mutating
actuator** on a real scenario (behind the approval gate) is the natural next step. And
the remaining big evidence is still the full-task/agent benchmark (critique #2's tail;
future idea: DJ-AI + this loop vs ARC-AGI-3).

## Evidence #3 — the `llm_tool_call` surface ✅  (capability model → the meta-actuator)

The capability-model debate (should we build thousands of actuators?) resolved to
**capability *surfaces*, not a per-tool catalog** (`docs/design/capability-model.md`).
Evidence #3 ships the first and most general surface: `llm_tool_call` — let a
tool-selector (an LLM) drive a multi-tool sequence, with the LLM as *just another
proposer* feeding the same gated loop. This is the seam DJ-AI's Qwen tool-caller plugs
into.

- **nmem-act `tool_calling.py`** — `ToolCallingExecutor` (an `ActionExecutor`): asks a
  `ToolSelector` for the next call → gates + executes it → repeats → returns **one
  composite Episode** with the full step trace. `ScriptedSelector` stands in for a real
  LLM in tests/examples; a host adapter over Qwen/OpenAI is the real thing (same seam).
- **Recording:** a *sink-less* inner runner gates + dispatches each sub-call without
  recording it, so the run is recorded exactly **once** (composite), not per sub-call.

**Safety model (defense in depth):**
- The selector sees only the tools permitted at the current autonomy level (the
  **envelope**), AND every call it makes is **re-gated per-call** by the inner runner —
  an off-envelope or under-declared call is still caught.
- **Delegation is its own privilege.** The composite's *effective* capability is the
  riskiest tool its envelope can actually drive (floored by any declared capability).
  Under `read_only` autonomy the envelope is read-only → the run is read-only and
  permitted; in `tiered` mode, exposing a mutating/high-risk tool requires the host to
  **also allowlist `llm_tool_call`** — allowlisting a tool for direct use does not hand
  it to an autonomous selector.
- Composite surface is itself gated (denylist/capability/approval); approval **fails
  closed** on hook error; selector exceptions are **encoded as ERROR** (never raised).

**Outcome semantics (honest credit):**
- The **selector is authoritative** on goal achievement via an optional `Done` verdict —
  a multi-tool run can legitimately succeed despite an individual failed/blocked probe,
  or conclude success/impossibility with *no* tool call at all. Absent a verdict, a
  coarse fallback applies (any success → SUCCESS).
- Genuine tool-execution **errors are surfaced** (ERROR) even over a success verdict;
  hitting `max_steps` without completion is **incomplete** (FAILURE, no discharge
  credit) — a truncated run is not an achieved one.

**Verified:** runnable example — a stand-in "LLM" investigates "worker is degraded"
under `read_only`: `fetch_status` + `search_runbook` succeed, `restart_service` is
**refused** (mutating under read_only), the whole run is **one** success Episode
(info_gain aggregated). 90 nmem-act tests (envelope scoping, per-call + delegation
gating, verdict authority, error/cap surfacing, one-composite-episode).

**Codex peer review:** iterated through error-masking, verdict authority, immediate-Done
handling, delegation-as-its-own-capability, and max-steps-incompleteness (all fixed with
regression tests); re-review **clean** (no correctness/security/perf issues). One codex
suggestion (penalise *every* imperfect step) was **declined with rationale** — it's
domain-wrong for agentic runs (a real run routinely has a failed probe and still
achieves the goal); the selector's `Done` verdict resolves it correctly instead.

**What this shows / doesn't.** The general meta-actuator exists and is safely gated: an
LLM can now drive tools through the same honest, gated, learning loop. It's exercised
with a *scripted* selector — the next step is wiring a **real LLM selector** (DJ-AI's
Qwen tool-caller) and DJ-AI's existing tools behind this surface, plus a `computer_use`
surface over the VM. That is the "wire this into DJ-AI" work.
