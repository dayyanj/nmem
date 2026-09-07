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
**Reviewed 2026-09-07.** A 3-stage pipeline with real deps: B1 generates surprise → B2 (auto-subscribed
to the `outcome.surprising` drive-event bus) stores candidate utterances → B3 is the drive to actually
say them. B1/B2 fire from what michelle already produces; B3 needs a host handler (+ is sensory-oriented).
- ✅ **B1 `NMEM_SYM_OUTCOME_SURPRISE_ENABLED`** — DONE + live-verified. `symbol_outcome_expectations`
  building (`drive:novelty|pursue_knowledge|n=1|pred_ewma=1.0`); `outcome.surprising` will fire on the
  first deviation (a failed pursuit → converges with A-i.1). GENERATOR. `episodes.record_action_outcome` →
  `surprise.appraise_outcome` (complete): appraises each outcome vs a per-`(source,action_type)` EWMA in
  `symbol_outcome_expectations` (lazily created), emits `outcome.surprising` on a large gap. Fires on
  michelle's pursuits. Clean enable, FIRST. *Validate:* `symbol_outcome_expectations` baselines build;
  `outcome.surprising` events emit on deviation (early on, before baselines settle, expect some).
- ✅ **B2 `NMEM_SYM_PENDING_UTTERANCES_ENABLED`** — ENABLED + wiring-verified ("Pending-utterance
  consumer wired to outcome.surprising" in log). Awaiting the first surprise event → `symbol_pending_
  utterances` (same gate as A-i.1/B1-surprise: michelle's next deviating/failed pursuit). CONSUMER (needs B1). `bridge` auto-subscribes
  `_on_pending_utterance_event` to `outcome.surprising` → `pending.consider_utterance` → row in
  `symbol_pending_utterances` (complete module; optional LLM phrasing/worth-threshold). Clean enable,
  after B1. *Validate:* `symbol_pending_utterances` candidates appear after surprising outcomes.
- ✅ **B3 `NMEM_SYM_COMMUNICATION_DRIVE_ENABLED`** — BUILT + wired + enabled (Option A, channel-agnostic
  + comms-learning; founder-directed). Design: nmem/docs/agent-comms-channel-agnostic.md. **Stage 1**
  (michelle f1c89cc): `service/communication.py` — the channel-agnostic `CommsLoop` (intent → select
  pending utterance → deliver via injected `ChannelSink` → LLM-assess reply → learn comms-skill via the
  #2 skill loop → discharge). 5 unit tests green; extracts verbatim to nmem-agent-core. **Stage 2**
  (michelle 898dc46): `PeerExchangeSink` (delivers to DJ-AI as a `challenge`, correlates the threaded
  reply in `peer._handle`) + wired as a drive-intent handler + flag on. "B3 comms loop wired", healthy.
  **Live round-trip data-gated** (needs a surprise→pending-utterance→drive-fire→DJ-AI-reply; verified
  streak + DJ-AI frozen), converges with A-i.1/B2. Rich `CommsAssessment` (engagement/valence/usefulness
  /lesson), NOT binary. nmem-twin later ships a `VoiceSink` — same core.

**Cluster B COMPLETE (built/wired):** B1 verified · B2 wired · B3 built+wired. The surprise→utterance→
comms→assess→learn→discharge chain is in place; its live data-validation lands on michelle's next
surprising/failed pursuit (same gate as A-i.1). Channel-agnostic core proven by unit tests.

### Cluster C — Richer hypotheses  [REVIEWED 2026-09-07 — it's a SUBSTRATE problem]
**Key finding:** the richer hypothesis shapes are *already dormant for lack of graph substrate*, not
for lack of flags. abductive/mechanistic/exception/analogical/competition are **default-ON** yet
michelle's hypotheses are all `graph_hypothesis` (similarity-gap). Her graph (397 nodes / 1679 edges /
938 hyps) is associative+structural but **causally sparse (~22 causal edges, 1.3%:** causes 14,
prevents 4, triggers 4). mechanistic/counterfactual/abductive NEED causal/world edges → nothing to work
on. And BL-1 is quantified: world relations michelle extracted (`contains, offers, uses, sells,
has_price, is_licensed_under, hosts…`) sit REJECTED in `symbol_edge_type_proposals`. So Cluster C =
**enrich the graph, then turn the shapes on.** Ordered:

- ✅ **C.a — BL-1 fix (DONE + live-verified; nmem-sym a506b41, michelle 8657e9c).** Turned the EXISTING
  manual promotion CLI (proposals.py) autonomous: `auto_promote_edge_types` promotes proposed relations
  recurring across ≥`min_sources`(=3) distinct sources → replays their held triples into edges + registers
  canonical (forward-accept via `_load_canonical_edge_types`) + marks `promoted_to`. Runs in dreamstate,
  gated `EXTRACT_AUTOPROMOTE_EDGE_TYPES_ENABLED` (default off → DJ-AI byte-identical/frozen-safe). No
  schema change (reused promoted_to/promoted_at + symbol_canonical_edge_types). LIVE: promoted `contains`+
  `offers` → edges; `uses`/`property` (2 srcs) correctly held. Design: nmem-sym/docs/edge-type-autopromotion.md.
  **BL-1 RESOLVED** (see backlog).
- ☐ **C4 `NMEM_SYM_EXTRACT_MULTI_TURN_ENABLED`** (clean enable) — chunked extraction of large findings →
  more nodes/edges (more substrate). *Validate:* large LTM entries yield more triples.
- ☐ **C3 `NMEM_SYM_PREDICTION_GROUNDING_LLM_ENABLED`** (clean enable, independent) — LLM judges whether a
  prediction's outcome occurred (broadens grounding candidates beyond embedding match; `_llm_judge_
  grounding` complete). *Validate:* more predictions grounded/disputed via LLM.
- ☐ **C1 `NMEM_SYM_HYPOTHESIS_POSTERIOR_ENABLED`** (enable) — A4: re-normalise a competing hypothesis
  set's posterior on evidence change. Competition is on + she has 938 hyps, so may act now. *Validate:*
  posteriors re-normalise on evidence.
- ☐ **C2 `NMEM_SYM_HYPOTHESIS_COUNTERFACTUAL_ENABLED`** (enable, substrate-gated) — counterfactual shape;
  self-activates as causal structure grows (like A-iii). Enable + note the gate.
- **Honest caveat:** CAUSAL sparsity is deep — research text yields few cause/prevent/trigger relations,
  so mechanistic/counterfactual stay thin near-term regardless; C.a+C4 enrich WORLD/associative
  structure (the achievable near-term win), and C1/C2 are positioned for when causal density grows.

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

## Flag dependency map (what each enabled flag REQUIRES)
Captured as we go — prerequisites (other flags), external substrate, and any host-side wiring.
**This map (in the doc) is the ONLY place to record deps.** ⚠️ Do NOT put inline `#` comments on
ENABLED lines in `capabilities.env` — env-file parsing keeps everything after `=` as the value, so
`FLAG=true # requires …` parses as a non-boolean and crashes startup (learned the hard way 2026-09-07).
Whole-line `#` comments (above the flag) are fine.

| Flag | Requires (flags) | + substrate / host wiring |
|---|---|---|
| `SYM_CONCERNS_ENABLED` | `DRIVES_ENABLED` | — |
| `SYM_CURIOSITY_CONCERNS_ENABLED` | `CONCERNS_ENABLED` + nmem connected | curiosity signals ≥ `CURIOSITY_CONCERN_MIN_COMPOSITE` (lowered 0.5→**0.4** for michelle) |
| `SYM_DRIVES_CREATE_GOALS` | `GOALS_ENABLED` (+ concerns for a *targeted* intent) | — |
| `SYM_DRIVES_GOAL_LLM_ENRICH` | `DRIVES_CREATE_GOALS` | `vllm_backends` + `bridge.set_goal_enrichment_context(...)` (michelle wires objectives/entities/findings) |
| `SYM_DREAMSTATE_BRIDGE_HOLES` | dreamstate running | `vllm_backends` for the LLM judge (else similarity-heuristic fallback) |
| `SYM_DRIVES_OUTWARD_ACTIONS=explore` | `DRIVES_HONEST_DISCHARGE` | host calls `bridge.discharge_drive` on real outcome (michelle sink) |
| `SYM_FAILURE_MEMORY` / `SELF_CAPABILITY` / `WORLD_MODEL` | — (each independent) | `record_action_outcome` flowing = the nmem-act actuation loop |
| `SYM_CONSOLIDATION_ENABLED` | — | episodes present (from the actuation loop) |
| `SYM_UTILITY_PLASTICITY_ENABLED` | — | `record_action_outcome(procedure_ids=…)` wiring (michelle A-ii) + procedures to credit |
| `SYM_STRATEGY_MEMORY_ENABLED` | (`UTILITY_PLASTICITY` for meaningful reward) | **multi-edge procedures** (BL-3) — dormant until they exist |
| `SYM_OUTCOME_SURPRISE_ENABLED` (B1) | — | `record_action_outcome` with `source` set (actuation loop) |
| `SYM_PENDING_UTTERANCES_ENABLED` (B2) | **`OUTCOME_SURPRISE` (B1)** | auto-subscribes to the `outcome.surprising` bus |
| `SYM_COMMUNICATION_DRIVE_ENABLED` (B3) | `DRIVES_ENABLED` | host handler for the `communicate` intent + (sensory vocabulary) |

## Issues surfaced (backlog — address later)
Found during a broad michelle log scan 2026-09-07 (no tracebacks/crashes — fail-open holding).

- ✅ **BL-1 — RESOLVED 2026-09-07 via sweep C.a** (autonomous edge-type promotion in dreamstate). Kept
  the vocab controlled but data-driven-extensible: recurring proposed relations auto-promote. Original:
  **controlled edge-type vocab drops WORLD relations (graph-richness limiter; sweep-relevant).**
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
