# nmem-agent-core — target architecture & library-hardening plan

**Status:** design-only. **Owner:** follow-up session(s). **Written:** 2026-09-07.
**Companion:** [config-alignment-plan.md](./config-alignment-plan.md) (the settings convention this builds on).

## The principle

> **An agent = data + adapters + config. All logic — and its LLM enrichment — lives in,
> and hardens in, the nmem libraries, toggled by flags.**

michelle-ai was hand-built to get a working autonomous peer fast. Much of that hand-built
cognition **reimplements capabilities the nmem stack already owns** (or should own). Rather than
fork the same logic into every bot, the bot becomes a thin consumer + a **live proving ground**
that forces the nmem repos to maturity. When michelle needs something the library does poorly,
**we fix the library** (validated against her running live), then thin out her code.

**Corollary (LLM enrichment is native too):** anything that calls an LLM to enrich a capability
belongs to the owning nmem system behind a toggle — the pattern already exists
(`NMEM_SYM_PREDICTION_LLM_REASONING`, `NMEM_SYM_PROCEDURE_LLM_ENRICHMENT`, LLM-backed extraction
and hypothesis plausibility). Enrichers call nmem's **own** LLM client (`LLMConfig` /
`vllm_backends`), configured once — so bot-level model plumbing dissolves into config.

This is a **refactor toward the right architecture**, not a fix for something broken — michelle
already works. It can proceed incrementally, validate-then-thin, with no pressure.

---

## 0. Post-sweep re-sequencing (2026-09-07, after Clusters A–F)

The **capability-activation sweep** ([capability-activation-sweep.md](./capability-activation-sweep.md))
ran after this plan was first drafted and **built + live-validated most of the library toggles this
plan proposed.** Updated status per §2:

| §  | Proposed | Status after sweep |
|----|----------|--------------------|
| 2.2 | goal LLM-enrich toggle | ✅ **BUILT** `DRIVES_GOAL_LLM_ENRICH` (nmem-sym 0b22e49); native producer live. `curiosity.py` still runs as SHADOW → **thin-out unblocked**. |
| 2.3 | cold-start drive fix | ✅ **BUILT** `DRIVES_HONEST_DISCHARGE` + `DRIVES_WAKE_MODE=event`; michelle on event-wake so her starvation guard is **already inert** → **delete unblocked**. |
| 2.4 | action-reflection toggle | ✅ **BUILT** `ACT_LLM_REFLECT` (nmem-act ac44822); `tool_learning.py` already thinned to `recall_lessons`. |
| 2.5 | proactive recall | ✅ **BUILT** (Cluster D): autonomy surface + the **`seed_recall` host-seam** (nmem-sym) + `recall.py` consumer + benign-no-match fix (ebc3008). |
| 2.6 | importance | ✅ done (pre-sweep). |

Plus new library hardening the sweep landed (all default-off → DJ-AI byte-identical): skill
canonicalization/dedup/`skill.chronic`, graph-hole bridging (#5), edge-type auto-promotion (C.a),
**self-engineering validated** (E4 recipes distill + inject), **channel-agnostic comms** (B3
`CommsLoop`/`ChannelSink` — built expressly for lift-and-shift, incl. a future nmem-twin voice
channel), concern-persistence (E1).

**The one big thing STILL not upstreamed — §2.1 (flagship).** nmem-act's `ReferenceRunner` only does
`execute(proposal) → Outcome` (single action). The **goal-pursuit lifecycle** — atomic claim,
infra-vs-actuation-failure classification, cancel/error un-claim, startup recovery, salvage-on-
exhaust, merit outcome write — still lives entirely in michelle's `cognition.py` pursuit loop
(~lines 300–370). That is the reference spec to lift into nmem-act.

### EXECUTED 2026-09-07 — Phases 1–3 done; PAUSED before Phase 4 (founder)
- ✅ **Phase 1** (michelle f0aac72): deleted `curiosity.py` (shadow-compare: native A5 producer at
  parity+ — all recent drive_intent goals native, curiosity dormant) + the inert starvation guard.
- ✅ **Phase 2** (nmem-act bd24acd, michelle 787fe49): built **`nmem_act.GoalPursuit`** — owns the
  claim→execute→classify(infra/verified)→resolve/release→startup-recovery lifecycle + optional
  salvage seam; dependency-free (injected `GoalStore` + executor + build_proposal). 9 tests, full
  suite 136 green. michelle rewired to it (−64 LOC in cognition.py); live: goal 229 claimed→sandbox
  →verified→achieved through GoalPursuit, 0 stranded.
- ✅ **Phase 3** (nmem 73fbeda, michelle 7d1e1c4): stood up **`nmem.agent_core`** (subpackage; opt-in
  lazy re-exports so `import nmem` never hits the nmem_sym cycle). Graduated **`SymbolGoalStore`** +
  the **recall consumer** (both proven pure adapters); michelle is consumer #1 (local copies deleted),
  validated live. Remaining graduations (bootstrap, LLM/embedder/DB/executor adapters, channel-agnostic
  CommsLoop, peer glue, data-schema loader) are the roadmap in `agent_core/__init__` — extract
  validate-then-thin, michelle stays live.
- ⏸ **Phase 4** — DJ-AI coordinated rollout: PAUSED (bigger effort; DJ-AI frozen). All changes remain
  default-off / additive, so DJ-AI is byte-identical until the coordinated lib-update + restart.

### Re-sequenced remaining work

**Phase 1 — Validated thin-outs** (low risk, immediate LOC win — the toggles are already proven live):
- **1a. Delete `curiosity.py`** after a short shadow-compare (native A5 producer vs curiosity output
  over N cycles) confirms parity-or-better goals. It's the last consumer forcing the shadow.
- **1b. Delete the starvation guard** in `cognition.py` (event-wake + honest-discharge validated;
  the guard is already inert on michelle).
- **1c.** `tool_learning.recall_lessons` is already thin app-glue — leave as reference or fold into
  `skills.find`/autonomy when Phase 2 lands.

**Phase 2 — FLAGSHIP: nmem-act goal-pursuit runner** (§2.1). Build a lifecycle-owning layer ABOVE
`execute()`: a runner that takes a **goal source** + **executor adapter** and owns claim →
infra-vs-failed → cancel/error un-claim → startup recovery sweep → salvage-on-exhaust
(`ACT_LLM_SUMMARIZE_ON_EXHAUST`) → merit outcome write. michelle's pursuit loop is the spec. Validate
beside michelle's loop, then thin michelle to *register SandboxExecutor + goal source + call runner*.
Biggest LOC + correctness win; best nmem-act stress test; proves the whole thesis.

**Phase 3 — Stand up `nmem-agent-core`** (the package does not exist yet). Extract from michelle:
bootstrap (construct MemorySystem + SymbolGraph, register adapters, apply `capabilities.env`),
adapters (executor / DB / embedder / nmem-LLM-client), data-schema loader (persona / objectives /
KB), peer glue, and the now-proven standard adapters (**recall consumer**, **channel-agnostic
CommsLoop**, viz bridge). michelle becomes consumer #1: config + persona + `SandboxExecutor` + thin
bootstrap. Target §5 shape: a few hundred LOC, mostly not-logic.

**Phase 4 — DJ-AI coordinated rollout.** Once michelle bakes, the currently-**FROZEN** coordinated
nmem-lib-update + DJ-AI restart (NOT a bare restart). Everything is default-off, so DJ-AI stays
byte-identical until per-flag enable.

**Two decisions — RESOLVED (founder, 2026-09-07):**
- **(a) Phase order → Thin-outs first, then flagship.** Do Phase 1 (delete `curiosity.py` +
  starvation guard) before the Phase 2 nmem-act runner — bank the validated LOC win + de-risk first.
- **(b) Packaging → a package INSIDE nmem (`nmem.agent_core`), start light.** No new repo/CI/release;
  ships with nmem (every agent already installs it). Graduate to a standalone repo later only if it
  earns it. So Phase 3 = create the `nmem.agent_core` subpackage, not a new repo.

---

## 1. Current state — michelle-ai custom code (~1848 LOC)

| Module | LOC | Role | Disposition |
|---|---|---|---|
| `cognition.py` | 397 | consolidation/drive/prediction wiring **+ pursuit loop + goal-safety + recall** | wiring stays; **loop/safety → nmem-act**; recall → autonomy |
| `model_backend.py` | 239 | provider-agnostic LLM client (reasoning brain) | **→ config** once enrichers use nmem's LLM client |
| `identity.py` | 209 | objectives, baseline KB, persona assembly, seed routines | **data + thin loader** — keep (parameterize) |
| `viz_bridge.py` | 171 | nmem-viz emit | keep (adapter) — or native to nmem-viz client |
| `memory.py` | 143 | construct MemorySystem + SymbolGraph | **bootstrap** — keep (parameterize) |
| `curiosity.py` | 140 | LLM goal producer (drive_intent follow-ups) | **→ nmem-sym drives→goals (A5) + LLM-enrich toggle** |
| `tool_learning.py` | 138 | reflect on action steps → skills; recall lessons | **→ nmem-act outcome sink (LLM-reflect toggle) + skills** |
| `peer.py` | 126 | nmem-exchange handler | keep — standard peer glue (candidate to graduate) |
| `server.py` | 111 | FastAPI lifespan/bootstrap | **bootstrap** — keep |
| `db.py` | 71 | DB engine | keep (adapter) |
| `sandbox_client.py` | 68 | computer-use actuator HTTP client | keep — **the executor adapter** |
| `config.py` | 27 | config load | keep |

**Genuine residue after upstreaming:** the **executor adapter** (sandbox I/O), **data** (persona,
objectives, KB), **config**, and **thin bootstrap** (construct memory/graph, register adapters, set
flags). Target: a few hundred LOC, mostly not-logic.

---

## 2. Upstreaming map — per capability

For each: **native owner → behavior to harden → LLM-enrich toggle to add → michelle code that is
the reference spec → validation → thin-out.**

### 2.1 Pursuit / actuation loop  →  **nmem-act**  *(FLAGSHIP — most mature native, DJ-AI-exercised)*
nmem-act already has the frame: `ActionProposal → Outcome`, `ActionOutcome` (unit of experiential
learning), `runner`/`executor`, `registry`/`adapters`, an outcome sink (DJ-AI wires it as
`nmem_act_bridge.make_outcome_sink → record_skill/episode`).

**Harden the runner to own the goal-actuation lifecycle** — michelle's `_pursue_cycle` is the spec:
- **Claim/lifecycle:** atomic claim before a long action (`mark_goal_pursuing`), resolve after.
- **Infra vs actuation failure:** distinguish "the actuator never really ran" (busy/unavailable/
  stuck → *retry*, don't burn the goal) from "a completed attempt failed" (→ resolve failed).
- **Cancellation safety:** on task cancel (shutdown), **un-claim** so a goal can't strand in
  `pursuing` (which `get_actionable_goals` excludes).
- **Unexpected-error safety:** any exception post-claim → un-claim + continue, never strand.
- **Startup recovery:** sweep goals stranded in `pursuing` by a hard kill back to actionable.
- **Salvage on exhaustion:** when a step/wall-clock budget is hit without a clean result, do a
  final **LLM summarize → best-effort result** rather than returning nothing.
  → **new toggle `ACT_LLM_SUMMARIZE_ON_EXHAUST`** (mirrors extraction's LLM pass).
- **Merit-based memory of the outcome:** write the episode with `importance=None` + a
  `record_type`/`grounding` derived from the outcome (verified vs couldn't-verify), so importance
  emerges from the native scorer + access — **not a hardcoded value.** (michelle already does this;
  it should be the sink's default.)

**Thin-out:** michelle registers a `SandboxExecutor` adapter (its HTTP calls) + a goal source; the
loop, safety, salvage, and outcome→skill capture come from nmem-act. Removes ~130 LOC and gains
proper episodes + procedure reward michelle's loop doesn't produce.

### 2.2 Goal production  →  **nmem-sym** (drives→goals, A5)
`DRIVES_CREATE_GOALS` turns a fired drive w/ a targeted **concern** into a `drive_intent` goal,
deduped per concern (needs `CONCERNS_ENABLED`). michelle's `curiosity.py` proves you *also* want an
**LLM producer mode**: propose specific, verifiable follow-up questions grounded in recent findings
+ objectives.
- **Harden:** validate A5 actually yields good goals with concerns on.
- **New toggle `DRIVES_GOAL_LLM_ENRICH`** (mirrors `PREDICTION_LLM_REASONING`): when a drive can't
  discharge, the drive system asks nmem's LLM for follow-up objectives → `drive_intent` goals,
  grounded via recall. `curiosity.py` is the reference implementation.
- **Thin-out:** delete `curiosity.py`; enable the flags.

### 2.3 Cold-start drive pathology  →  **nmem-sym** (drives)
michelle needed a **starvation guard** because the novelty drive **pins at max and spins on an
empty graph** (explore → 0 nodes) — a cold-start pathology. The library fixes are
`DRIVES_HONEST_DISCHARGE` (pressure only clears on real work) + `DRIVES_WAKE_MODE=pressure`
(pressure-driven heartbeat, no fixed-tick spin). **Harden these so no bot needs the band-aid**;
michelle's guard is the evidence/repro. **Thin-out:** delete the guard.

### 2.4 Learning from action  →  **nmem-act** outcome sink + **nmem** skills
Skill storage/retrieval (`skills.record/find`) and skill→procedure compilation are **native and
comparatively mature**. michelle's genuinely-new bit is **reflection on an action trace → DO/AVOID
lessons** (nmem doesn't watch actuator steps).
- **New toggle `ACT_LLM_REFLECT_ENABLED`** on the outcome sink (mirrors
  `PROCEDURE_LLM_ENRICHMENT`): given the proposal/step trace + outcome, the sink asks nmem's LLM for
  reusable lessons and records them as skills. `tool_learning.py` is the spec.
- **Thin-out:** delete `tool_learning.py`; recall-of-lessons becomes `skills.find` / autonomy.

### 2.5 Proactive recall  →  **nmem** autonomy
`AUTONOMY.proactive_retrieve` already emits `memory.surfaced` (relevant results + skills) on writes;
`surface_now(query)` is the explicit path. michelle's recall-before-act exercises it live.
- **Harden** the surface quality (youngest native). **Thin-out:** michelle subscribes to
  `memory.surfaced` instead of hand-recalling; keep only the app-specific "inject into the task."

### 2.6 Importance  →  **nmem** (already native ✓)
Done. Findings write `importance=None` + `record_type`/`grounding`; the consolidation scorer +
access velocity promote on merit. Access routes through `memory.recall` (bumps `access_count`), not
raw SQL. No custom code remains — this is the template for §2.1's outcome write.

---

## 3. Sequencing (by native maturity — lowest risk first)

1. **nmem-act ← pursuit loop + salvage + outcome-reflect** (§2.1, §2.4). Most mature, DJ-AI-proven,
   biggest LOC + correctness win. Also the best stress test nmem-act needs.
2. **nmem-sym drives ← cold-start fix + A5 goal creation + LLM-enrich producer** (§2.2, §2.3).
3. **nmem autonomy ← proactive recall** (§2.5). Youngest; michelle matures it, then subscribes.

## 4. Validation methodology (every item)
**Validate-then-thin, never blind-cut.** For each capability: enable the native mechanism, run it
**alongside** michelle's custom version (both producing into the same store, or A/B by flag),
compare what each yields over real cycles, confirm parity-or-better, **then** delete the custom
code. michelle stays live throughout as the integration test. Because the natives are new,
regressions are expected — that's the point: fix them in the library.

## 5. Target nmem-agent-core shape
After upstreaming, the reusable core is:
- **Bootstrap** — construct `MemorySystem` + `SymbolGraph`, register adapters, apply the capability
  config (from `capabilities.env`).
- **Adapters** — actuator executor (into nmem-act's registry), DB, embedder, LLM client (nmem's).
- **Data schema** — objectives, persona, baseline KB (per-agent content).
- **Peer glue** — the standard nmem-exchange handler.
Everything cognitive (drives, goals, pursuit, learning, recall, enrichment) is **native + toggled**.
A new agent is then: a config + a persona + (optionally) a custom executor adapter.

## 6. Risks & coordination
- **New/untested natives** (nmem-act, A1/A2/A5 drives, autonomy): expect to *build* maturity, not
  just consume it. Budget library work, not just wiring.
- **DJ-AI (prod) shares these libraries.** Hardening nmem-act/nmem-sym affects prod — land + bake on
  michelle first, deploy DJ-AI only after a clean bake (same posture as config-alignment).
- **Don't regress importance/merit + access-via-nmem** (§2.6) when moving the outcome write into
  nmem-act's sink — it's the template, keep it.

## 7. First move
Spec the **nmem-act runner hardening** (§2.1) in detail against michelle's `_pursue_cycle`
(claim → infra-vs-failed → cancel/error un-claim → startup recovery → salvage → merit outcome
write), land it behind flags, register michelle's `SandboxExecutor`, run beside the current loop,
compare, then delete michelle's loop. That single move proves the whole thesis.
