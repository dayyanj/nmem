# nmem capability-activation sweep — plan & tracker

**Status:** in progress. **Started:** 2026-09-07. **Companions:**
[native-vs-bespoke-test-plan.md](./native-vs-bespoke-test-plan.md),
[nmem-agent-core-plan.md](./nmem-agent-core-plan.md).

## Goal
Conclusively test, enhance, and validate **every** nmem-family capability by activating the
disabled flags **one at a time** on the live proving-ground agent **michelle-ai**, validating each
on fresh data as her cognition evolves. This continues the native-vs-bespoke work: each capability
is either "enable + validate it works" or, where half-baked (as several were), "fix + upstream" —
so the win lands in the nmem libraries, not the bot.

## Method (per capability)
1. **Assess** — read the code; is it complete, half-baked, or does it need a host seam? Write a short
   design/assessment doc when there's a real design choice (like #1-5); skip the doc for a clean
   enable.
2. **Prereqs** — enable any dependency flags first; note external deps michelle lacks.
3. **Enable** — flip the flag in michelle's manifest (`config/capabilities.env`) or `michelle.env`.
4. **Validate on fresh data** — restart, exercise it (trigger loops / `/admin/*`), confirm it
   activates AND produces sensible output on michelle's live store. Capture before/after.
5. **Fix if needed** — if broken/underpowered, fix in the lib (default-off/additive), re-validate.
6. **Document** — commit; update this tracker + the topic memory.

**Guardrails:** default-off/additive so DJ-AI stays byte-identical; never pollute michelle's live
store with fake data; batch to one restart where possible; watch the first outputs before letting a
loop run wide.

## Precedent — the 5 upstream items already shipped this way (all live-verified)
#1 honest_discharge · #2 skill capture→surface→apply loop · #3 ACT_LLM_REFLECT · #4
DRIVES_GOAL_LLM_ENRICH (native producer) · #5 graph-hole bridging. Each has a design doc + live proof.

## Baseline (2026-09-07): already ON
Base cognition (drives, emotion, goals, prediction + LLM reasoning, schemas, analogy, self-model,
procedures, temporal), memory tiers, skills (+#2), concerns + curiosity-concerns (#4), hole-bridging
(#5), reflective capture (#3), honest-discharge (#1); on-by-default nmem features (knowledge-links,
consolidation, importance, belief, retrospective, policy-alignment, immune-skeptic, most hypothesis
shapes). NOT counted here: tuning params, and `IMPORTANCE__LLM_RESCORE` (unimplemented placeholder).

## Sequence (dependency-ordered; ✅ done / ▶ active / ☐ todo / ⏸ deferred)

### Cluster A — Experiential loop (Slice D+)  [closes the act→learn loop we built]
- ☐ **A1. `NMEM_SYM_UTILITY_PLASTICITY_ENABLED`** — procedures reinforced by achieved goal utility
  (reward EWMA). *Validate:* after pursuits, `symbol_procedures` reward EWMA moves; ranking favors
  procedures that worked. **First — it closes directly on the episodes/`record_action_outcome` from
  #1/#3.**
- ☐ **A2. `NMEM_SYM_CONSOLIDATION_ENABLED`** — experiential consolidation (episodes → patterns).
- ☐ **A3. `NMEM_SYM_FAILURE_MEMORY_ENABLED`** — distil failing outcomes into analogically-retrievable
  lessons (`symbol_failures`). *Pairs with* the verified=False pursuits from #1.
- ☐ **A4. `NMEM_SYM_STRATEGY_MEMORY_ENABLED`** — induce strategies from experience.
- ☐ **A5. `NMEM_SYM_SELF_CAPABILITY_ENABLED`** — self-capability tracking (what she's good/bad at).
- ☐ **A6. `NMEM_SYM_WORLD_MODEL_ENABLED`** — world model.
- (defer `DREAMSTATE_GAIN_BUDGET` — an ops optimization, do last of A.)

### Cluster B — Surprise → communication ("worth saying" pipeline)
- ☐ **B1. `NMEM_SYM_OUTCOME_SURPRISE_ENABLED`** — endogenous prediction-error surprise (already
  half-wired: `record_action_outcome` appraises surprise when this is on). The generator.
- ☐ **B2. `NMEM_SYM_PENDING_UTTERANCES_ENABLED`** — the "worth saying" consumer (needs B1).
- ☐ **B3. `NMEM_SYM_COMMUNICATION_DRIVE_ENABLED`** — drive to actually communicate (needs a host
  handler via `drives.on_intent` — michelle could surface to the peer channel / logs).

### Cluster C — Richer hypotheses (feeds #4 concerns + #5 holes)
- ☐ **C1. `NMEM_SYM_HYPOTHESIS_POSTERIOR_ENABLED`** — posterior hypothesis shape.
- ☐ **C2. `NMEM_SYM_HYPOTHESIS_COUNTERFACTUAL_ENABLED`** — counterfactual shape (world-directed).
- ☐ **C3. `NMEM_SYM_PREDICTION_GROUNDING_LLM_ENABLED`** — LLM-judged prediction grounding.
- ☐ **C4. `NMEM_SYM_EXTRACT_MULTI_TURN_ENABLED`** — multi-turn triple extraction (richer graph).

### Cluster D — Recall drive
- ☐ **D1. `NMEM_SYM_RECALL_DRIVE_ENABLED`** — drive-initiated proactive memory surfacing before
  acting (complements #2 recall/#3 capture on the surface side).

### Cluster E — Self-improvement / meta (has caveats)
- ☐ **E1. `NMEM_SYM_CONCERN_PERSISTENCE_ENABLED`** — persist concerns across restart (rumination);
  low-risk, pairs with #4/#5.
- ☐ **E2. `NMEM_COMMITMENT_DETECTION__ENABLED`** — detect commitments/obligations from text.
- ☐ **E3. `NMEM_AUTONOMY__ENABLED`** (proactive_retrieve / auto_capture_skills) — **revisit the Test 3
  caveat**: auto_capture fires on entry types michelle doesn't produce, proactive_retrieve emits
  `memory.surfaced` nothing consumes. Enable only if we wire a consumer or it earns its keep.
- ☐ **E4. `NMEM_SELF_ENGINEERING__ENABLED`** — self-improvement loop (DJ-AI has it gated; assess risk).

### Cluster F — Deferred (external deps michelle lacks)
- ⏸ `NMEM_SYM_SENSORY_CONTEXT_ENABLED` — needs a sensory DB (`sensory_db_dsn`).
- ⏸ `NMEM_SYM_OBLIGATIONS_ENABLED` (+`OBLIGATION_PERSISTENCE`) — extrinsic motivation; needs a
  delegator/obligation source michelle doesn't have yet.
- (Other prefixes to sweep later once these land: NMEM_IMMUNE layers beyond skeptic, NMEM_EXCHANGE
  peering depth, NMEM_IDENTITY (voice — needs audio), NMEM_RECOGNITION, NMEM_ENTITY, NMEM_CLUSTERING
  tuning.)

## Tracker notes
- Update the ✅/▶/☐ marks + a one-line result as each lands; mirror the headline into the topic
  memory [[second-djai-peer-diverse-priors]].
- Many of these are complete features (just enable + validate); expect some to be half-baked and need
  a fix + upstream, exactly as #2/#4 did.
