# nmem capability-activation sweep — plan & tracker

**Status:** SWEEP COMPLETE 2026-09-07 — Clusters A–E enabled+validated on michelle; F assessed &
correctly deferred (role-substrate michelle lacks; each F capability already proven on a sibling
agent or belonging to the twin). Every capability michelle's role can exercise has been swept.
**Started:** 2026-09-07. **Companions:**
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
- ✅ **C4 `NMEM_SYM_EXTRACT_MULTI_TURN_ENABLED`** — ENABLED (complete paragraph-chunking). Fires on
  content >6000 chars; her research notes are short → rare, but active for large findings.
- ✅ **C3 `NMEM_SYM_PREDICTION_GROUNDING_LLM_ENABLED`** — ENABLED (complete `_llm_judge_grounding`).
  Active in the dreamstate/prediction cycle; broadens grounding beyond embedding match.
- ✅ **C1 `NMEM_SYM_HYPOTHESIS_POSTERIOR_ENABLED`** — ENABLED (`renormalize_competitor_posteriors` on
  evidence change in auto_ground_hypotheses). Active; acts as competing hypotheses gain/lose evidence.
- ✅ **C2 `NMEM_SYM_HYPOTHESIS_COUNTERFACTUAL_ENABLED`** — ENABLED (real dreamstate shape generator).
  Substrate-gated on causal density; self-activates (like A-iii). Cycle runs it clean.

**Cluster C COMPLETE.** C.a (build) live + C1-C4 enabled; a dreamstate cycle ran CLEAN (210s, 0 errors)
producing **47 rich, world-directed hypotheses** (e.g. AGPL-§13 → UEN corporate-liability chain), not
bare similarity-gaps — C.a's edge enrichment + the shapes combining. The richer-hypothesis payoff is
visibly starting; it deepens as the graph accrues causal density (C4/promotions feed it). (Ops note:
`/admin/dreamstate` client timeout should be >210s or run async — the cycle now includes LLM grounding.)
- **Honest caveat:** CAUSAL sparsity is deep — research text yields few cause/prevent/trigger relations,
  so mechanistic/counterfactual stay thin near-term regardless; C.a+C4 enrich WORLD/associative
  structure (the achievable near-term win), and C1/C2 are positioned for when causal density grows.

### Cluster D — Recall drive  [✅ COMPLETE 2026-09-07 — full loop live-validated]
**Shipped:** the recall drive had NO pressure source (confirmed: no `recall` entry in
`EVENT_CONCERN_MAP`; diffuse `inject_pressure` gives it a target-less intent → `_do_recall(None)`
raises → never discharges). Built the two seams the drive's design left for the host:
1. **nmem-sym `SymbolBridge.seed_recall(query, *, ref, pressure)`** (bridge.py) — the "recall-routed
   concern" injection. Uses concern `mirror` semantics (idempotent + `resolve_on_action`) so it fires
   ONCE per seeded query then self-clears (no storm). Gated: no-op unless recall drive + concerns on.
   Design doc: `nmem-sym/docs/recall-drive-host-seam.md`.
2. **michelle `service/recall.py`** — the `memory.surfaced` consumer (rolling buffer +
   `context_for(query)` token-ranked injection block). Wired in `init_cognition` (gated on
   `recall_drive_enabled`); the pursuit loop auto-seeds `seed_recall(objective)` and injects
   `context_for(objective)` alongside `recall_lessons`/prior. Ops hook `POST /admin/seed_recall`.
- ✅ **Config:** `NMEM_AUTONOMY__ENABLED=true` + `PROACTIVE_RETRIEVE=true` + `RECALL_DRIVE_ENABLED=1`
  + `RECALL_AGENT_ID=michelle` (must match her agent_id or surface searches the wrong store).
  `AUTO_CAPTURE_SKILLS` left OFF (Test-3: fires on 0 of her entry types).
- ✅ **VALIDATED end-to-end (real data, not gated):** `seed_recall('CocosBotanica…')` → drive fired
  (`intent: recall, target='CocosBotanica…'`) → `request_surface` → autonomy `surface_now` found 2
  matching LTM entries → emitted `memory.surfaced` → consumer stashed (buffer 0→1) → `context_for`
  returned an injection block with the REAL findings (goals #194/#200) → drive discharged
  (`resolve_on_action`, no re-fire). Honest no-op path also confirmed: a hyper-specific query that
  matched nothing → "nothing surfaced" → deferred relief (drive correctly does NOT discharge). Recall
  fires 1:1 with novelty — not starving intrinsic drives.
- **Dep-map:** `RECALL_DRIVE` requires `AUTONOMY__ENABLED` (surface_now is autonomy-gated) + a host
  seed source (`seed_recall`) + a host `memory.surfaced` consumer. All three now present.
- **Future (library, deferred):** route `self_model.coverage_gap`/`limitation_discovered` to ALSO
  seed a recall concern → fully autonomous recall with no host seed (needs `EVENT_CONCERN_MAP`
  multi-routing; single-spec today). Noted in the design doc.

**Post-build review (2026-09-07)** — adversarial pass on the shipped D code:
- ✅ **FIXED (nmem-sym ebc3008): benign no-match was recorded as a bridge error.**
  Pre-existing `_do_recall` raised on "nothing surfaced"; `_safe` then counted it
  (`errors++`, `errors_by_subsystem`, `last_error_at`) + tracebacked, and deferred-relief
  raised a second traceback. A no-match is the NORMAL outcome on michelle's sparse store,
  so it corrupted the error-count health signal + flooded logs. `_do_recall` now returns
  bool; the handler reports strength 0.0 for a no-op (still no discharge) without counting
  it as an error. Verified: no-match recall silent (0 tracebacks/0 errors), match unchanged.
- **Accepted (not fixed — evidence says fine):** (a) auto-seeding recall for the FULL
  specific objective has a low surface hit-rate (many no-ops), but fires stay bounded (recall
  ~1:3 vs novelty over 20min; concerns decay below threshold within the 60s cooldown so they
  don't re-fire/storm — the 0.92 auto-seed decays under θ in ~5s). Not starving intrinsic
  drives. Could seed broader entity terms for better yield — optional. (b) the surfaced block
  and the inline `prior = memory.recall(obj)` overlap (two "what you already know" blocks in
  the pursuit prompt); benign — distinctly labelled, `context_for` dedups within itself. (c)
  `PROACTIVE_RETRIEVE` also feeds the consumer independently (journal-triggered surface) — bonus
  coverage, distinguished by `reason`. Both are fine.

<details><summary>Original D review (pre-build) — kept for provenance</summary>

- ☐ **D1. `NMEM_SYM_RECALL_DRIVE_ENABLED`** — a `recall` drive that, on recall pressure carrying a
  TARGET, asks nmem to proactively surface memory for it (`bridge._do_recall` → `mem.request_surface`).
  **Two hard findings from review — D is NOT a clean solo enable:**
  1. **DEP on E-autonomy:** `request_surface` (memory.py:220) → `autonomy.surface_now`, which **returns
     False unless `NMEM_AUTONOMY__ENABLED`** (+ `PROACTIVE_RETRIEVE`). So `_do_recall` gets "nothing
     surfaced" → RuntimeError → pressure never discharges → drive **inert** without autonomy. **Enable
     E3 autonomy FIRST/with D.** (Dep-map: `RECALL_DRIVE` requires `NMEM_AUTONOMY__ENABLED` + `PROACTIVE_RETRIEVE`.)
  2. **No consumer (Test-3 gap, confirmed):** surfacing emits `memory.surfaced`, which **nothing in
     michelle consumes** (viz_bridge subscribes journal/ltm/shared only). So recall would surface into
     the void. **The real build = wire a consumer** that injects surfaced memory into pursuit context
     (a natural companion to #2 `recall_lessons` / #3 — recall-before-act on the *drive's* target). Same
     "wire a consumer" pattern as B3. This ALSO makes E3's `proactive_retrieve` useful (resolves the
     Test-3 finding), so **do D + E3 + the consumer together.**
  3. **Pressure source:** recall pressure needs a source — host `inject_pressure()` or a recall-routed
     concern/competence signal. *Assess on resume:* does michelle generate recall pressure (via concerns/
     competence)? If not, the drive never fires — may need a concern→recall route or is inherently quiet.
- **D plan (resume):** (a) enable `NMEM_AUTONOMY__ENABLED` + `PROACTIVE_RETRIEVE` (E3); (b) enable
  `RECALL_DRIVE`; (c) BUILD a `memory.surfaced` consumer in michelle (subscribe → stash surfaced
  items → inject into the next pursuit's context, alongside `recall_lessons`); (d) confirm/route recall
  pressure; (e) validate: recall fires → `memory.surfaced` → consumer injects → pursuit uses it +
  drive discharges. Skip E3's `auto_capture_skills` (Test-3: fires on 0 of her entry types — leave off).

</details>

### Cluster E — Self-improvement / meta (has caveats)
**Cluster E — DONE 2026-09-07** (michelle 1c695e0; all self-provision their tables via
`create_all` / `CREATE TABLE IF NOT EXISTS` on restart — no migrations). Both E2 + E4 ride the
**nightly** path (`run_nightly_synthesis`), NOT `run_full_cycle` — added `/admin/nightly` to
trigger it on demand (+ `/admin/probe_recipes` read-only).
- ✅ **E1. `NMEM_SYM_CONCERN_PERSISTENCE_ENABLED`** — ENABLED; `symbol_concerns` auto-created;
  flush/load wired each tick. **Mechanism verified + correct** (persists NATIVE concerns only;
  `resolve_on_action` mirrored concerns — nmem curiosity + recall seeds — are deliberately EXCLUDED,
  re-derived from their durable source). **Population time-gated**: michelle's live concerns are
  mostly curiosity-mirrors + recall-seeds (not persisted by design); native concerns need an
  `EVENT_CONCERN_MAP` event (hypothesis.disputed / extraction.contradiction / self_model.limitation)
  — rare on her current workload. Ready for when it matters. (nmem-sym's own suite covers the
  persist/revive round-trip.)
- ✅ **E2. `NMEM_COMMITMENT_DETECTION__ENABLED`** — ENABLED; `nmem_commitments` present. Runs clean
  (1 bounded LLM pass/nightly) → **0 detected**. Substrate-gated: her journal is research findings,
  not dated promises-with-requesters (same shape as `auto_capture_skills`). Live + ready if she ever
  makes commitments (e.g. via peer exchange).
- ✅ **E3. `NMEM_AUTONOMY__ENABLED`** — DONE with Cluster D (surface_now feeds the recall consumer +
  `PROACTIVE_RETRIEVE` journal path also feeds it). `auto_capture_skills` left OFF (Test-3).
- ✅ **E4. `NMEM_SELF_ENGINEERING__ENABLED` (+ `INCLUDE_IN_PROMPT`)** — ENABLED, staged
  (distill→inspect→inject). michelle HAS the substrate (reliable canonicalized skills). **Fully
  validated:** nightly distilled **2 high-quality recipes** from her real skills (#133 "Snippet-First
  Search Triage", #93 "Efficient Browser Research Tactic" — check AI Overview/snippets first, pivot
  keyboard→mouse on failure), `status=active` (passed the acceptance gate, no policy-override
  language). Injection gate PROVEN: a matching research query injects both; "weather in Paris"
  injects none (near-exact-match ≥0.6). **Safety: edits its OWN injected prompt context, NEVER host
  code/config** — bounded (3 LLM/run + size caps), acceptance-gated, host-vetoable, staleness-decayed.
  DJ-AI keeps it OFF (byte-identical). `PROPOSE_SUBAGENTS` left off (deferred — needs a spawn host).

### Cluster F — ASSESSED 2026-09-07 (boundary of michelle's role; confirmed in code, not assumed)
The sweep's honest edge: F is where michelle's ROLE (isolated, self-directed research peer — no
senses, no external delegator) genuinely lacks the substrate. Each verdict is grounded in code, and
the on-by-default subsystems here are confirmed LIVE (so F is *swept*, not skipped).
- ✅ **RECOGNITION / ENTITY / CLUSTERING — already live.** No `*_enabled` master switch in nmem
  config (EntityConfig/ClusteringConfig/RecognitionConfig are tuning-only) → always-on subsystems.
  Active on michelle as baseline; nothing to enable.
- ✅ **EXCHANGE peering — already live.** No `ExchangeConfig` in nmem config; michelle peers with
  DJ-AI (`dm:michelle:djai`) via her own `service/peer.py` + nmem-exchange. No "depth" switch exists.
- ⚪ **IMMUNE (beyond skeptic) — N/A.** Skeptic already baseline-on; `NMEM_IMMUNE_DB_DSN` is UNWIRED
  in-repo (read nowhere) — there is no immune-beyond-skeptic wired to enable.
- ⏸ **`NMEM_SYM_OBLIGATIONS_ENABLED` (+persistence) — DEFER (founder-decided 2026-09-07).** Source is
  `commitments.py:249` (a recorded commitment forwards to the backend as an obligation). michelle has
  ZERO obligation source: E2 commitment-detection yields 0 (no dated promises in her journal) and
  there are no `impose_obligation` / `commitments.record` / `register_requestor` calls in her code —
  she has no external delegator (by design; unlike DJ-AI's founder + Redis delegation). Worse,
  enabling it switches her drive loop from event-wake to the timer-stepped **meta-arbiter** path (a
  responsiveness regression) for zero benefit. The capability is **already proven in production on
  DJ-AI** (nmem 0.9.x commitments/obligations, live since 2026-08-16). Revisit only if michelle gains
  a delegator (e.g. DJ-AI delegating to her).
- ⏸ **`NMEM_SYM_SENSORY_CONTEXT_ENABLED` (+`SENSORY_DB_DSN`) — DEFER.** Needs the nmem-sym-sensor DB
  + a live sensory stream. michelle has no audio/camera; that sensor DB is the twin's TABULA2 voice
  embedder, not hers. Genuinely external.
- ⏸ **`NMEM_IDENTITY` (voice recognition) — DEFER.** Needs audio input michelle doesn't have — twin
  territory (`nmem-identity` lives in the twin/DJ-AI voice stack).

**Sweep conclusion:** Clusters A–E enabled+validated on michelle (each either live-proven or honestly
substrate-gated with the capability confirmed correct). F is correctly deferred: its three genuine
capabilities (obligations/sensory/voice) require substrate michelle's role lacks, and each is either
already proven on a sibling agent (obligations→DJ-AI) or belongs to a different agent (sensory/voice
→ twin). The on-by-default F subsystems are confirmed live. **Every capability michelle's role can
exercise has been swept.**

## Flag dependency map (what each enabled flag REQUIRES)

> **⚙️ MACHINE-READABLE SOURCE OF TRUTH:** this map is now code —
> `nmem/src/nmem/agent_core/capabilities.py` (`CAPABILITIES` + `validate`/`check_env`, commit a4e049d).
> `AgentRuntime.start()` runs `check_env()` and WARNS on any flag ON with a required flag OFF (it would
> silently no-op); `strict_capabilities=True` escalates to raise. `requires` = hard flag→flag deps
> (validated); `substrate` = non-flag prereqs (LLM endpoint / host sink / data / a running loop / a
> matching id — advisory). The table below is the human-readable mirror — keep it in sync with the code,
> which is authoritative. Validated against michelle's live merged env → 0 issues (dependency-complete).
> **NB the enabled set is the MERGED process env (all EnvironmentFiles), not one file** — michelle's base
> flags live in `michelle.env`, her opt-ins in `capabilities.env`.

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
| `SYM_RECALL_DRIVE_ENABLED` (D1) | `DRIVES_ENABLED` + `CONCERNS_ENABLED` + **`AUTONOMY__ENABLED`** (surface_now is autonomy-gated; `PROACTIVE_RETRIEVE` only needed for the passive journal path) + `RECALL_AGENT_ID`=agent | ✅ recall-pressure source = `bridge.seed_recall()` (michelle seeds per pursuit) + ✅ `memory.surfaced` consumer = `service/recall.py` |
| `AUTONOMY__ENABLED` (E3) | — | ✅ consumer = michelle `service/recall.py`; skip `AUTO_CAPTURE_SKILLS` (Test-3: 0 michelle entry types) |
| `SYM_EXTRACT_AUTOPROMOTE_EDGE_TYPES_ENABLED` (C.a) | — | dreamstate running; reuses proposals/canonical ledger |
| `SYM_HYPOTHESIS_POSTERIOR/COUNTERFACTUAL`, `PREDICTION_GROUNDING_LLM`, `EXTRACT_MULTI_TURN` (C1-C4) | — (shapes benefit from graph causal density) | vllm_backends for LLM grounding/shapes |

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
