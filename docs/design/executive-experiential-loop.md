# The Executive Experiential Loop: Learning from Consequences

A design for the layer above nmem + nmem-sym — the one that closes the gap between a
system that *remembers and reasons about* experience and one that *deliberately acts
in order to learn, measures whether its behaviour worked, and changes future
behaviour accordingly.*

## The problem

nmem gives an agent memory. nmem-sym gives it associative and causal cognition on
top of that memory. Together they cover a large fraction of the "real learning
cognition" stack: persistent episodic + semantic memory, consolidation, belief
revision, spreading activation, causal prediction with grounding, LTP/LTD
plasticity, procedure compilation, a self-model, and — critically — an **autonomous
drive system** that accumulates pressure and, on a 1-second tick, fires a
self-initiated intent to discharge it.

But the drives can only ever discharge *inward*. When the arbiter fires, its intents
are `dreamstate`, `explore`, `verify`, `extract`, `ground`, `recall` — every one of
them reorganizes knowledge the system already has. None can gather **new** evidence
from the world. So competence/coherence/novelty/uncertainty pressure is relieved by
introspection, never by acting and learning from consequences.

Worse, the two intents that *are* outward-facing — `communicate` and
`ground_sensory` — are stubs that **fake their own discharge**:
`_do_sensory_grounding` (nmem-sym: `bridge.py:1800`) logs *"would trigger sensory
grounding (external system)"* and returns; because it doesn't raise, the deferred-
relief contract treats it as a completed action and drops the drive's pressure
**with zero work done**. The system marks the need met while doing nothing.

This is the structural gap. It is not a memory gap and not a reasoning gap. It is an
**agency** gap: pressure accumulates and has no honest avenue to become action.

## What already exists (the reframe)

The executive loop is roughly 70% built and dead-ended on both sides *by design*.
The brain half is in nmem-sym; the hands half is in host applications such as DJ-AI.
The verified state of the seam:

```
   nmem-sym  (the BRAIN, propose-only)          SEAM             HOST (the HANDS)
 ┌────────────────────────────┐                          ┌───────────────────────────┐
 │ drives accumulate pressure  │                          │ long-running agent loop    │
 │  coherence/novelty/         │                          │  (tight / deep / strategic)│
 │  uncertainty/competence/    │                          │ typed action registry:     │
 │  integration (+comm/recall) │   commitment.acting      │  delegate / code_fix /     │
 │            │ 1s tick        │ ───────  LIVE  ────────► │  computer_use / communicate│
 │            ▼                │                          │ real actuators (shell,     │
 │ winner-take-all arbiter     │   subagent.proposed      │  git-deploy, VM, web,      │
 │ fires ONE intent            │ ──── SUBSCRIBED, ──────► │  delegate)                 │
 │            │                │        INERT             │ tiered gates (policy +     │
 │            ▼                │      (logs only)         │  fail-closed approval)     │
 │ _handle_drive_intent        │                          │ predict→ground outcome     │
 │  inward: dreamstate/explore │                          │  (calls nmem-sym           │
 │   /verify/extract/recall ✅ │                          │   predict_causal!) ✅       │
 │  outward: communicate /     │   ◄── NO OUTCOME PATH ── │ records skills worked/fail │
 │   ground_sensory  ❌ STUB   │       BACK TO BRAIN      │                            │
 └────────────────────────────┘                          └───────────────────────────┘
```

The five verified breaks, with anchors:

| # | Break | Anchor |
|---|-------|--------|
| 1 | **Outward intents fake-discharge** — no-op handlers still `relieve()` pressure. | nmem-sym `bridge.py:1800`, `:1807` |
| 2 | **Intents are dropped + unexposed** — sole caller ignores returned `Intent`s; `drive_state`/`peek_ready` exist but aren't on the MCP/API surface; 0 ACT tools across 32. | nmem-sym `mcp_integration.py:323` |
| 3 | **Utility never reinforces** — plasticity is epistemic-only; `goal→achieved` does not LTP the causal path / `procedure_ids` that achieved it; no reward model. | nmem-sym `goals.py:340` |
| 4 | **The drive→goal edge is dead code** — `create_goal_from_intent` defined & never called; `bridge.inject_goal()` referenced but absent; procedures never executed. | nmem-sym `goals.py:186` |
| 5 | **The host's proposal channel is off** — host subscribes to `subagent.proposed` but only logs it; `self_engineering.enabled=False`. | host `nmem_bridge.py:2416` |

The corollary that shapes everything below: because the brain is deliberately
**propose-only** ("nmem never runs them; the host instantiates and runs"), "the
system prompts itself to take actions" cannot mean the brain running shell/HTTP. The
arbiter *already* prompts itself. What's missing is a **contract** that lets a
self-generated intent become a real, gated, external action and return as an
attributed outcome — plus an **install-agnostic executor** that anyone can run.

## Decisions locked with the user

1. **Sequencing → quick wins first, then the loop.** Land the brain-side rails
   (honest discharge, utility→plasticity, intent surfacing, hypothesis posterior,
   drive→goal) before building the outward loop on top.
2. **Executor → install-agnostic + pluggable.** Ship a **standalone reference
   runner** (a new sibling package; lift the reusable parts of DJ-AI's execution
   code — typed dispatch, tiered gates, predict→ground, outcome recording). DJ-AI
   becomes *one* adapter implementing the same `ActionExecutor` contract. Anyone can
   install nmem and choose which agent is their executor. **The brain stays
   propose-only; the executor is always a separate, external component.**
3. **Autonomy → a three-level config flag** on the executor, default safest:
   - `read_only` (default) — may read the world (search, fetch, read), **no action
     that changes external state or leaves a persistent outbound presence.**
   - `tiered` — an explicit allow/deny list naming exactly which action types are
     permitted; greylist items can hold for approval.
   - `full` — no restrictions; the executor acts as it sees fit.
4. **First falsifiable claim → utility-weighted learning.** On nmem-bench:
   *utility-weighted plasticity (QW2) improves task-success / procedure reliability
   versus epistemic-only reinforcement.* If it doesn't, stop and reconsider.

## The layered model

Each layer is independently valuable and makes the ones above it more capable — the
same discipline as the nmem / nmem-sym split.

```
┌──────────────────────────────────────────────────────────────────┐
│  Layer 4  EXECUTIVE — turn pressure into gated action, learn from  │
│           the outcome. Propose (brain) → execute (external) →      │
│           attribute → utility-weighted learning → honest discharge │
├──────────────────────────────────────────────────────────────────┤
│  Layer 3  COGNITION (nmem-sym) — association, causality, drives,   │
│           prediction, procedures, self-model                       │
├──────────────────────────────────────────────────────────────────┤
│  Layer 2  MEMORY (nmem) — storage, retrieval, consolidation,       │
│           belief revision, grounding                               │
├──────────────────────────────────────────────────────────────────┤
│  Layer 1  LLM — raw intelligence                                   │
└──────────────────────────────────────────────────────────────────┘
```

The closed loop the executive layer runs:

```
   drive pressure (retained — QW1: not yet relieved)
        │
        ▼
   arbiter selects an OUTWARD intent  →  builds an ActionProposal        [brain]
        │   { drive, goal, concern target, competing hypotheses (posterior),
        │     predicted_outcome + confidence, candidate action_spec,
        │     expected_info_gain, est_cost, est_risk, capability_class }
        ▼
   emit `experiment.proposed`  +  expose via MCP (QW3)      ─── SEAM (propose-only) ───
        │
        ▼
   executor consumes it, gates by autonomy_level, runs the action        [external]
        │   (reference runner OR DJ-AI OR any ActionExecutor impl)
        │   predict→ground before acting; execute; observe
        ▼
   record_action_outcome(proposal_id, Outcome)  →  first-class Episode    [brain]
        │   { state_before, chosen_action, alternatives, actual_outcome,
        │     utility_vector, causal_attribution, confidence Δ, lessons }
        ▼
   LEARN:  outcome-gated relief (QW1)  +  utility-weighted plasticity (QW2)
           +  hypothesis posterior update (QW4)  +  self-model calibration
```

## Verified seams (proven templates)

These are the exact attachment points the build reuses — no new coupling:

- **Heartbeat + self-initiation:** the drive tick loop `mcp_integration._drive_tick_loop`
  (nmem-sym `mcp_integration.py:311`) → `SymbolBridge.tick_drives` (`bridge.py:923`)
  → `_handle_drive_intent` (`bridge.py:1627`, *"this is where drive pressure becomes
  action"*). Register the executor as the outward handler via
  `drives.on_intent(...)` or add an `act`/`experiment` case beside the existing ones.
- **Propose→host contract precedent (live):** `commitment.acting` relays up to the
  host, which executes the deliverable — host `nmem_bridge.py:2155`, brain
  `bridge.py:1306`. The experiment channel mirrors this exactly.
- **Propose→host contract precedent (inert, ready):** `subagent.proposed` +
  `resolve_proposal` (nmem `self_engineering.py:462`, `:658`; host handler
  `nmem_bridge.py:2416`). Same lifecycle shape; the experiment proposal is its
  action-bearing sibling.
- **Predict→ground already spans the seam:** the host calls nmem-sym
  `PredictionPlugin.predict_causal(store=True)` *before* acting and harvests
  prediction errors overnight (host `nmem_bridge.py:918`, `:1001`). The Episode's
  `predicted_outcome`/`confidence` come straight from here.
- **Plasticity primitives to reuse:** `apply_ltp`/`apply_ltd` (nmem-sym
  `plasticity.py:57`, `:109`), `reinforce_procedure` (`procedural.py:815`), the
  `somatic_marker` EWMA pattern (`prediction.py:1673`) that the `reward` EWMA mirrors.
- **Competition already modelled:** `competes_with` links + supersession
  (`hypothesis.py:2358`, `:2482`); the posterior is a normalization on top.
- **Owning the loop:** the MCP server `lifespan` co-locates `mem` + `sym_state`
  (nmem `mcp/server.py:114`) and already spawns the consolidation + drive tasks — the
  natural place to spawn an in-process executor when one is configured.

## The build

### Slice A — Quick wins (nmem-sym only; no host, no executor)

All five are brain-side, safe (no outward action), each independently valuable, and
together they lay the rails the loop needs. All opt-in, defaults-OFF.

**A1 — Honest, outcome-gated discharge.** Make stub handlers `raise` when they do no
real work (mirror `_do_recall` at `bridge.py:1686`, which already does), so pressure
is *retained*. Extend `Drive.relieve()` (`drives.py:91`) with an
`outcome_strength: float = 1.0` so relief becomes proportional to achieved outcome,
threaded from the intent handler through `_handle_drive_intent`. Real internal ops
keep `outcome_strength=1.0` → byte-identical behavior. *Tests:* `_do_sensory_grounding`
raises → pressure retained; relief scales with `outcome_strength`.

**A2 — Utility → plasticity wire (the first benchmark claim).** On `goal.status →
achieved` (`goals.py:340` / `update_goal_progress:311`), call the existing
`apply_ltp` on the goal's causal-path edges and `reinforce_procedure` on its
`procedure_ids`, strength scaled by `progress`. Add a `reward` EWMA column to edges
and procedures (migration + `schema.sql`), mirroring `somatic_marker`, updated on
outcome. Config `UTILITY_PLASTICITY_ENABLED=False`. *Benchmark:* on nmem-bench,
epistemic-only vs utility-weighted plasticity on task-success / procedure
reliability — drivable by **replayed/recorded outcomes**, so it's testable *before*
the outward loop exists. *Tests:* an achieved goal raises its procedures'
`success_count`/`reward`; unrelated procedures unchanged.

**A3 — Surface intents + drive-state.** Expose `drive_state()` (`drives.py:854`),
`dominant_drive()` (`:874`), `peek_ready()` (`:883`) via new **read-only** MCP tools
in `mcp_tools.py` and `api.py`; make the arbiter emit an `intent.fired` /
`experiment.proposed` event so a host can subscribe (fixes the dropped-intent gap).
*Tests:* MCP tool returns state; event fires on intent.

**A4 — Joint hypothesis posterior.** In `auto_ground_hypotheses` (`hypothesis.py:984`),
for each `competes_with` set compute `posterior = softmax(evidence_score)` and
re-normalize all siblings whenever any one gets evidence; persist `posterior`. Turns
independent beliefs into a real A=.54/B=.31/C=.15 distribution — the substrate for
experiment-selection-by-discrimination. *Tests:* a competitor set sums to 1.0;
evidence on one re-normalizes the rest.

**A5 — Revive drives→goals.** Call the dead `create_goal_from_intent` (`goals.py:186`)
from the arbiter path when a drive stays above threshold across N cycles (a *chronic*
drive becomes a decomposable goal with `source_type="drive_intent"`); add the missing
`bridge.inject_goal()`. Config `DRIVES_CREATE_GOALS=False`. A2's goal-achievement
reward then closes on these goals. *Tests:* a persistent drive creates a goal;
achieving it triggers A2's reinforcement.

**Exit criteria (A):** all opt-in with defaults-OFF and byte-identical off-state
behavior; the utility-weighted-learning benchmark runs and reports a number
(pass/fail against the claim); no outward action anywhere yet.

### Slice B — The executor contract + reference runner

**B1 — The contract (nmem-sym; the brain owns the objects it emits and learns from).**

- `ActionProposal` — `{ id, drive_name, goal_id?, concern_target, hypotheses:
  [{id, statement, posterior}], predicted_outcome, confidence, action_spec:
  {type, params}, expected_info_gain, est_cost, est_risk, capability_class }`.
- `Outcome` / `Episode` (new table `symbol_episodes`) — `{ proposal_id, state_before,
  goal, hypothesis, predicted_outcome, chosen_action, alternatives_considered,
  actual_outcome, utility_vector, causal_attribution, confidence_before,
  confidence_after, lessons }`.
- `utility_vector` — `{ task_success, cost, latency, risk, side_effects, info_gain,
  goal_progress }` (start by populating `task_success` + `info_gain` + `goal_progress`;
  the rest are wired but may be zero at first).
- `ActionExecutor` **Protocol** — `async execute(proposal: ActionProposal) -> Outcome`.
- Out: event `experiment.proposed`. In: MCP tool + API `record_action_outcome(
  proposal_id, outcome)`, which writes the Episode and feeds `utility_vector` into
  A2's plasticity (A2 generalizes from scalar `progress` to the vector).

**B2 — The reference runner (new sibling package, e.g. `nmem-act`).** Install-
agnostic, dependency-light, lifts the reusable parts of DJ-AI's execution code:

- **Typed action registry** — each action = `{ name, capability_class:
  read_only | mutating | high_risk, handler, cost/risk estimate }`. Built-in
  read-only actions: web search / fetch, `memory_search`, sandboxed read-only shell.
  Mutating/high_risk actions are registered but gated.
- **Autonomy gate** — `autonomy_level = read_only (default) | tiered | full`,
  enforced at dispatch against each action's `capability_class`; `tiered` reads an
  allow/deny list and can hold greylist items for approval.
- **Predict→ground** — call nmem-sym `predict_causal(store=True)` before acting;
  attach the prediction to the Episode (mirrors host `nmem_bridge.py:918`).
- **Outcome recording** — after execution compute the `utility_vector` and call
  `record_action_outcome` back to the brain.
- **Heartbeat** — subscribe to `experiment.proposed`, or poll the A3 drive-intent
  MCP tool on an interval.
- Config `enabled=False`, `autonomy_level="read_only"`, allow/deny lists for tiered.

**DJ-AI adapter** — a thin shim so DJ-AI's existing deep-cycle dispatch implements
`ActionExecutor` (flip `self_engineering.enabled`, route `experiment.proposed` into
`_execute()` exactly like `commitment.acting`, reuse its policy + approval gates).

**Exit criteria (B):** a read-only action runs under `read_only`; a mutating action
is blocked under `read_only`, allowed under `tiered`-if-whitelisted and under `full`;
an outcome flows back and writes an Episode; the brain still runs standalone with no
executor installed.

### Slice C — Close the outward loop

Wire it end to end: arbiter outward intent → `ActionProposal` → `experiment.proposed`
→ executor runs (gated) → `record_action_outcome` → Episode → **outcome-gated relief
(A1) + utility-weighted plasticity (A2 vector) + posterior update (A4)**. Add
**info-gain experiment selection**: when hypotheses compete (A4), choose the action
that maximizes expected discrimination between them. *Exit:* a live demo — a drive
fires, a read-only experiment runs, an outcome returns, pressure discharges honestly,
plasticity and posteriors update, and the drive that fired measurably drops.

### Slice D+ — Enrichments (roadmap)

Each earns its place on a benchmark before the next starts:

- **Structured failure memory** — `symbol_failures` (`failure_signature`,
  `attempted_strategy`, `reason_failed`, `recovery_action`, `preventative_rule`) +
  `find_analogous_failures()` used in `predict()` and context injection. Negative
  transfer protection.
- **Conditional self-model** — a `self_capability_stats` record with `success_rate`,
  `sample_size`, `confidence`, a known-vs-unfamiliar bucket, `best_strategy`, and
  `failure_modes`. Enables "I am weak at this / I should test rather than assume."
- **Strategy memory above procedures** — promote portable meta-patterns over
  `symbol_procedures` via the schema-induction machinery. Transfer across tasks.
- **Dreamstate expected-gain budgeting** — today nearly every dreamstate op runs
  unconditionally; give each a measurable expected gain and spend compute on the
  highest return (attention allocation across offline cognition).

**Deferred (confirmed by research — you were right to hold):**

- **Typed world-model + P(S′|S,A)** — high effort; smallest first step is a
  `symbol_transitions` table `(state, action, next_state)` accumulating observed
  counts at grounding time. Not on the critical path.
- **Neural / fast-weight plasticity** — the external-plasticity advantage
  (inspectable, reversible, cheap, safe) holds far longer; revisit only after the
  external loop is saturated.

## Cross-cutting: safety

- **Autonomy gate** — `read_only` default; nothing that changes external state or
  leaves a persistent outbound presence runs unless the level is raised and (in
  `tiered`) the action is explicitly whitelisted.
- **Opt-in, defaults-OFF** — every flag defaults off; off-state behavior is
  byte-identical (regression-locked), exactly as skills/autonomy/self-engineering.
- **Propose-only preserved** — the brain never executes; the executor is always a
  separate, external component. Standalone-safe: the brain runs with no executor.
- **Bounded** — per-cycle action caps, per-action cost/risk ceilings, and the
  existing LLM call/size caps carry over.
- **Reversible + auditable** — every Episode is logged with prediction, action,
  outcome, and attribution; `high_risk` actions can hold for approval in `tiered`.

## Open risks (adversarial check)

In the spirit of `symbolic-cognition-critique.md` — assume it's wrong until a
benchmark says otherwise:

- **Reward hacking / fake satisfaction.** The whole motivation is that the system
  currently fakes discharge. If relief isn't tied to *verified* outcome, the loop
  just learns to fake faster. → A1 (honest, outcome-gated relief) is a prerequisite,
  not an enhancement; outcomes must be grounded, not self-reported.
- **Utility mis-specification.** A wrong `utility_vector` optimizes the wrong thing.
  → Start with `task_success` + `info_gain` + `goal_progress`; benchmark-gate; keep
  the vector small until each axis earns inclusion.
- **Does closing the loop actually beat internal-only?** This is the real question,
  and it's exactly the first benchmark claim (utility-weighted learning). If it
  fails, stop — don't build C/D on faith.
- **Cost + blast radius.** Real actions cost money/time and can change the world. →
  `read_only` default, `tiered` allowlist, per-action cost ceilings, audit trail.
- **Complexity.** A new package + cross-repo contract is real surface area. → The
  contract is small (`ActionProposal`, `Outcome`, one Protocol, two events); the
  reference runner reuses proven code; the brain changes are the five quick wins.

## Critical files

**New:** `nmem-act/` (reference runner — registry, autonomy gate, predict→ground,
outcome callback, heartbeat); nmem-sym `executive.py` (`ActionProposal`/`Outcome`/
`ActionExecutor` + emission/recording); `symbol_episodes` table + migration.

**Modify (nmem-sym):** `drives.py` (A1 relieve contract, A5 chronic→goal),
`bridge.py` (A1 stub handlers raise, A3 event, A5 `inject_goal`, C outward dispatch),
`goals.py` (A2 reward on achieve, A5 revive `create_goal_from_intent`),
`plasticity.py` + `procedural.py` + `schema.sql` (A2 `reward` EWMA),
`hypothesis.py` (A4 posterior), `mcp_tools.py` + `api.py` (A3 tools, B1
`record_action_outcome`), `config.py` (all new flags).

**Modify (host, optional adapter):** `nmem_bridge.py` (flip `self_engineering.enabled`,
route `experiment.proposed` → `_execute`, implement `ActionExecutor`).
