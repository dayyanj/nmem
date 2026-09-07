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
**Reviewed 2026-09-07.** michelle already emits the trigger: every pursuit calls
`bridge.record_action_outcome(status=…)` (#1/#3), and she has 422 procedures + 40 episodes. Findings
reorder A by effort+dependency into three sub-groups:

**A-i — outcome/episode CONSUMERS (fire immediately from what michelle already produces; clean
enables, do first).** Each writes its own table, so enable + validate one at a time:
- ✅ **A-i.1 `NMEM_SYM_FAILURE_MEMORY_ENABLED`** — ENABLED + wired + code-path-confirmed; awaiting first
  post-enable failure. michelle already emits `status="failure"` on verified=False pursuits (she had
  failures at 07:18/07:19, but FAILURE_MEMORY went live at the 07:28 restart — so all failures predate
  the enable; verified streak since). NOT a firing bug — `symbol_failures` will populate on her next
  failed pursuit (poll watching). Zero michelle-side risk (she already passes status; the path is
  nmem-sym's own, covered by its tests).
- ✅ **A-i.2 `NMEM_SYM_SELF_CAPABILITY_ENABLED`** — DONE + live-verified. `self_capability_stats` row for
  `pursue_knowledge` (success_count=1, sample_size=1, mean_task_success=1) accrued from the first pursuit.
- ✅ **A-i.3 `NMEM_SYM_WORLD_MODEL_ENABLED`** — DONE + live-verified. `symbol_transitions` row
  `drive:novelty → pursue_knowledge → success (count=1)` from the first post-enable pursuit.
- ✅ **A-i.4 `NMEM_SYM_CONSOLIDATION_ENABLED`** — DONE + live-verified. `/admin/dreamstate` ran
  `consolidate_episodes` → **45** `symbol_consolidated_patterns` from her episodes.

**A-ii — procedure REWARD (needed a wiring fix, done):**
- ✅ **A-ii.1 `NMEM_SYM_UTILITY_PLASTICITY_ENABLED`** — DONE + live-verified (michelle 9984ba7). Wired
  `recall_lessons`→proposal→observations→`record_action_outcome(procedure_ids)`. After a pursuit,
  procedures 93/376/160 moved reward 0.0→0.300 (EWMA) + trial_count++. nmem-sym unchanged (reward path
  was complete; only host wiring missing). — reward procedures by achieved utility (EWMA).
  Two paths: goal-resolution credit (goals.py:399/417) + episode reward (episodes.py:105, needs
  `pids`). **GAP:** michelle's `record_action_outcome` passes NO `procedure_ids`, so the episode path
  can't fire — WIRE the procedures she recalled/used into the pursuit outcome (assess the goal path
  too). *Validate:* procedure reward EWMA moves after a verified pursuit; ranking favors what worked.
  *Side-issue:* 422 procedures vs 12 skills — #2's dedup didn't propagate to procedures; A-ii/A-iii
  may need a procedure-consolidation pass too.

**A-iii — STRATEGY induction (needs rewarded procedures; do after A-ii):**
- ✅ **A-iii.1 `NMEM_SYM_STRATEGY_MEMORY_ENABLED`** — ENABLED + wired + correct; `induce_strategies` ran
  clean via `/admin/dreamstate` and promoted **0** strategies — CORRECTLY: it mines recurring
  `edge_type_sequence` shapes (len≥2, ≥2 instances), but michelle's 12 active procedures are all
  `edge_type_sequence` len 0 (flat, skill-derived — no multi-edge sequences). So it self-activates once
  she forms multi-edge procedures. **Left ON** (harmless no-op until substrate exists). *Downstream dep
  (backlog BL-3):* michelle's skill→procedure path yields FLAT procedures; nothing forms sequential
  (multi-edge) procedures yet — that's the substrate strategy induction (and richer procedural reuse)
  needs. Correction: active procedures = 12 (matches skills); the "422" was total incl. superseded — no
  active-procedure sprawl.

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

## Issues surfaced (backlog — address later)
Found during a broad michelle log scan 2026-09-07 (no tracebacks/crashes — fail-open holding).

- ☐ **BL-1 — controlled edge-type vocab drops WORLD relations (graph-richness limiter; sweep-relevant).**
  `extract.py:681` rejects any edge_type not in `config.DEFAULT_EDGE_TYPES` (heavily causal/self-model/
  goal: causes, part_of, self_*, achieves…) and parks it in `symbol_edge_type_proposals` "for later
  review/promotion" (`extract.py:1084`) — but nothing promotes them. So legit world relations michelle
  keeps extracting (`sells`, `hosts`, `contains`, `uses`, `is_licensed_under`, `confirms`) never become
  edges → her graph stays causal/self-focused and misses world structure. This starves exactly the
  graph richness Clusters A/C + #4/#5 depend on. *Fix options:* add a world/associative edge-type tier
  to DEFAULT_EDGE_TYPES, and/or have schema-induction auto-promote frequently-proposed types from
  `symbol_edge_type_proposals`. Belongs near Cluster C.
- ☐ **BL-3 — no multi-edge procedure formation (blocks strategy induction + richer procedural reuse).**
  michelle's procedures come only from the skill→procedure mirror = FLAT (`edge_type_sequence` len 0).
  Strategy induction (A-iii), and portable multi-step procedures generally, need SEQUENTIAL procedures
  (edge-type shapes len≥2). Nothing currently compiles those — candidates: derive them from
  goal-decomposition paths (`decompose_goal`), from the sandbox step traces (#3 reflection could emit an
  ordered edge sequence, not just flat lessons), or from recurring episode chains. Surfaced by A-iii.
- ☐ **BL-2 — extractor JSON parse not fence/extra-data tolerant (minor robustness).**
  `extract.py` occasionally hits `JSON parse failed … Extra data` when the LLM wraps JSON in ```json
  fences or emits trailing data → that entry's triples are lost. Same failure mode we already fixed in
  `_judge_hole` (#5) and `_enrich_goal_objective` (#4): strip ``` fences before `json.loads`. Small,
  self-contained.

**Validation confirmations (not issues):** #2 Layer 5 `skill.chronic` escalation observed FIRING live
(michelle wrote strategy lessons for recurring lessons — "wait for the loading state", "close overlay
pop-ups"), confirming the escalation path end-to-end. (`HF_TOKEN` unauth warnings are benign; optional
`HF_HUB_OFFLINE=1` silences them.)

## Tracker notes
- Update the ✅/▶/☐ marks + a one-line result as each lands; mirror the headline into the topic
  memory [[second-djai-peer-diverse-priors]].
- Many of these are complete features (just enable + validate); expect some to be half-baked and need
  a fix + upstream, exactly as #2/#4 did.
