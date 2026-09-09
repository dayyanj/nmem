# nmem Continuity Layer — Design

**Status:** design-only. No code, no schema, no DB touched by this doc.
**Companion:** `path-b-hive-scoping-design.md` (hive scoping — this doc is deliberately built to not
collide with it), `nmem-agent-core-migration.md` (refinery cognition migration, §6.1 already equates
the shared conscious-memory hive with "continuity").
**Provenance:** derived from an external proposal + critique written against the **public GitHub**
repos, then **validated against the local v1.0.0-prep trees** (nmem 1.0.0 schema v5, nmem-sym 1.0.0,
2026-09-08). The validation substantially changed the design — see §1.
**Scope decisions (founder, this session):** per-agent hive-aware · v1 living assembler first · pilot
on the isolated **michelle-ai**.

---

## 0. Why now, and the one-line thesis

Query-driven RAG answers *"what memories are relevant to this query?"* Continuity answers *"what must
this process know to experience the present moment as a continuation of its own prior state?"* The
failure case is a session opening with `"Morning."` — nothing for conventional retrieval to key on.

> **Continuity is a live projection across the existing six tiers + nmem-sym — assembled fresh every
> turn — plus one durable, re-grounded autobiographical narrative. It is not a seventh storage tier.**

Two founder constraints shape everything below:
1. **Living** — every prompt gets current state, not only a post-dreamstate refresh.
2. **Hive-compatible** — must not collide with the in-flight Path B work (two live sessions).

---

## 1. Validation: most of the proposal already exists

Key seam: **nmem owns the durable "conscious record"; nmem-sym owns the live "cognitive ledger"**,
linked by `sym_*_id` columns. The *only* coupling point is `SymbolBridge`
(`nmem-sym/src/nmem_sym/bridge.py`).

| Proposal concept | Reality | Home |
|---|---|---|
| Six tiers + hybrid retrieval | **Exists, complete** | `nmem/tiers/*`, `search.py`, `memory.py` |
| Dreamstate (decay/promote/dedup/conflict) | **Exists both sides** | `nmem/consolidation.py`, `nmem_sym/dreamstate.py` |
| Commitments/obligations (open→fulfilled/breached/abandoned, decay, API) | **Exists, complete** | `nmem_commitments` + `commitments.py`; `symbol_obligations` (live pressure) |
| "Open Loop Tracking" | **Exists but split/renamed** | folded into commitments; plus `nmem_curiosity_signals` (decay + composite salience). No *unified* open-loop object |
| Self-model (capabilities/limitations/calibration) | **Exists — nmem-sym only** | `self_model.py`, `self_model_snapshots`, `self_capability_stats` |
| Drives (coherence/novelty/uncertainty/competence/integration) | **Exists — nmem-sym, exact 5 scalars, REAL gating state** | `drives.py`, `EVENT_PRESSURE_MAP`, `drive_history` |
| Goals / objective tracking | **Exists** | `symbol_goals`; `agent_core/goal_store.py`, `persona.py` |
| Session-boot warm-up | **Partial** | `Memory.briefing()` (`memory.py:617`), `AgentRuntime.start()`, `seed_persona()`, `recall.context_for()` |
| **Unified wake-snapshot / continuity-state** | **NEW** | nothing fuses the above into one boot artifact |
| **`narrative_self` (autobiographical running story)** | **NEW** | 0 hits repo-wide; closest is `self_model_snapshots`/`drive_history` (trend data, not narrative) |
| **`sleep_delta`** | **NEW** | closest is `DreamstateStats`, `dreamstate_op_gain` |

**Every *input* the wake state needs already exists.** Only three things are new: the **assembler**,
the **narrative_self**, and the **delta**. Two of the critique's "gaps" are already handled in code —
commitments have a full lifecycle, and drives are real control state, not decoration.

---

## 2. The "living" question → a two-speed model (the core decision)

"Living" resolves into splitting continuity by **rate of change**:

- **Volatile projection — assembled live, every turn, NO new storage.** Open commitments, actionable
  goals, unresolved curiosity signals, working-memory slots, elapsed-time/staleness, last-interaction.
  These already live in indexed source-of-truth tables; re-reading them per turn is cheap. This *is*
  the living part — always current because never cached. **Implementation: extend `Memory.briefing()`**
  (`memory.py:617`) — already parallel, token-budgeted, per-agent, recognition-tagged
  (KNOWN/FAMILIAR/UNCERTAIN) — into `Memory.wake()`/`Memory.continuity()` adding the
  commitment/goal/curiosity/self-model lanes.

- **Durable narrative — written by dreamstate, persisted, re-grounded.** The autobiographical running
  story ("over the last weeks I've been…") cannot be re-derived cheaply per turn and must survive
  process death. The only genuinely new persisted artifact.

- **Immediate-continuity checkpoint — advanced every turn.** last_interaction_summary, last_action,
  interrupted_work. Persist one compact row per agent so "returning after a gap" survives restarts.
  **Written per-turn, not per-session** (see §6.1): the hosts do not emit a reliable session-close, so a
  session-end-only write never fires — the turn loop (`agent_core.chat.converse`) is the durable cadence.
  `Memory.end_session()` still writes a checkpoint opportunistically when a host does close a session.

**Living table vs pure JSON → hybrid, and mostly neither.** The living part is *computed, not stored*.
Only two small durable artifacts persist (§3). A single fat JSON `continuity_state` blob is rejected —
it loses per-field provenance, drift-auditability, and concurrency safety, and goes stale between
dreamstate cycles (the opposite of living).

---

## 3. Storage: two thin per-agent tables, everything else live-assembled

- **`nmem_narrative_self`** (new, nmem core) — **append/versioned**, `agent_id`-scoped:
  `current_period`, `longer_trajectory`, `provenance` (JSONB: source journal/LTM ids each claim traces
  to), `grounded_at`, `last_full_reconstruction_at`, `token_len`, `version`. Versioned rows make drift
  auditable — diff narrative@t against episodic ground truth (this *is* Gap 2's eval).
- **`nmem_continuity_checkpoint`** (new, nmem core) — **single upserted row per `agent_id`**:
  `last_interaction_summary`, `last_action`, `interrupted_work`, `expected_next_action`, `updated_at`.
- One schema migration in the **nmem library** (`CURRENT_SCHEMA_VERSION` 5→6) — the library's normal
  path. Run **codex adversarial review before it lands**.

---

## 4. Per-agent, hive-aware scoping — why this sidesteps Path B entirely

- Both new tables are **`agent_id`-scoped** — they ride the **conscious-memory hive**, which `path-b`
  §1 confirms is *already hive-ready and needs nothing* ("This IS the founder's hive").
- The **narrative is written by the agent's own per-agent nmem consolidation** (`run_nightly_synthesis`
  / `consolidation.py`), **NOT** the graph-global nmem-sym dreamstate. Critical move: narrative writing
  **never needs the keeper lock** and never contends on `symbol_*` tables — it entirely avoids the
  Path-B keeper-singleton problem (that doc's §4).
- **Hive awareness is read-only.** Continuity may *peek* at the shared world-model via existing
  `SymbolBridge.augment_search()` and read the shared tier — but **never writes agency tables**
  (`symbol_goals`/`concerns`/`obligations`/`episodes`/`pending_utterances`), which are mid-migration to
  `owner_agent` scoping, co-owned by the two live Path-B sessions, with an open leak backlog + "fleet
  caveat." Continuity **consumes** them, never mutates them.
- New nmem→nmem-sym reads go through **one new read-only method**,
  `SymbolBridge.continuity_inputs(agent_id)`, preserving "the bridge is the only coupling point." No
  direct cross-package table access.

**Package placement:** assembler → nmem `Memory.wake()`/`continuity()` (extends `briefing()`);
narrative writer + delta → nmem `consolidation.py` (per-agent nightly synthesis); self-model/drives/
goals inputs → read-only `SymbolBridge.continuity_inputs()`. michelle-ai calls `wake()` at boot, ahead
of `augment_search()`.

---

## 5. How the critique's 8 gaps resolve against real code

1. **Open-loop lifecycle** — *mostly solved.* Don't build a new lifecycle; **unify existing ones.**
   Commitments have status + decay; `nmem_curiosity_signals` have decay + composite salience. New work
   = a thin **unified open-loop view** ranking across both by salience with a hard `k` into the wake
   state, reusing existing closure predicates.
2. **Narrative drift / re-grounding** — *the real new hard part.* Mandate: (a) periodic **full
   reconstruction from raw episodic source** (journal/LTM), not incremental-only; (b) every claim
   carries `provenance` back to source ids; (c) a consistency check flags claims no longer supported by
   episodes; (d) **bounded `token_len`** so compression is explicit. The versioned table makes (a)/(c)
   measurable. Follow the `self_model_snapshots` time-series pattern.
3. **First-person framing vs provenance** — reuse existing **groundedness**
   (`symbol_nodes.groundedness`), **recognition levels** (KNOWN/FAMILIAR/UNCERTAIN in `search.py`), and
   confidence fields. First-person framing applies **only to the grounded/KNOWN tier**; everything else
   surfaces with explicit epistemic status. Plus a write-path threat model for what may enter narrative.
4. **Precedence when continuity contradicts input** — present evidence wins; on contradiction, correct
   the checkpoint immediately. Add a **staleness function** keyed on `checkpoint.updated_at` /
   `narrative.grounded_at` downweighting volatile state as elapsed time grows, and a distinct
   **"returning after a long gap" boot mode** (gives `elapsed_time` a consumer).
5. **Concurrency** — the michelle pilot is single-writer/isolated → free at pilot. Generalization:
   narrative has one writer per `agent_id` (no CAS); the single-row checkpoint needs a
   version/last-write-wins-safe upsert because the refinery worker (`MAX_CONCURRENT_TASKS=5`) can run
   two tasks for one `agent_id`. A generalization gate, not a pilot blocker.
6. **Drives are NOT decoration** — *already solved.* `drives.py` scalars are real homeostatic control
   state gating the arbiter. So invert the critique's worry: **do not re-inject raw scalars as a
   dashboard.** Surface drive state as **prose consequence** ("an unresolved contradiction between A and
   B is pulling attention"), never the numbers.
7. **External delta** — `sleep_delta` covers only introspective change. Add an **external delta** (files,
   other agents' shared-tier writes, elapsed real time) merged with the introspective one at boot. In
   the hive, "what other agents did" comes from the shared tier — already available.
8. **Evaluation** — build the harness **alongside v1**: static hand-authored 500-token preamble as
   baseline; measures = unprompted "what were you doing" accuracy across a gap, commitment-honouring
   rate, planted-contradiction detection, 30-day-gap behavior, and narrative-vs-episodic drift (tests
   Gap 2 directly).

**Framing note:** the global-workspace analogy earns its place; the "persistent consciousness" label
does not — it makes Gaps 2 and 6 harder to see. This design uses "continuity layer."

---

## 6. Phased plan

**Phase 1 — v1 living assembler + baseline (pilot: michelle-ai, isolated)**
- Extend `Memory.briefing()` → `Memory.wake()`/`continuity()`: add commitment, actionable-goal,
  unified-open-loop (commitments ∪ curiosity), self-model, drive-as-prose lanes; priority order
  identity → trajectory → commitments → goals → relationships → self-model → unresolved → internal
  state; token-budgeted like `briefing()`.
- Add read-only `SymbolBridge.continuity_inputs(agent_id)`.
- Wire the snapshot into the **turn loop** (see §6.1), not boot-only.
- Build the eval harness + static-preamble baseline in parallel; capture baseline numbers.

### 6.1 Caller topology — where continuity is read and written (corrects the original boot-only plan)

The first cut of this plan said "call `wake()` at boot" and "checkpoint at session end." Both were
wrong in the same way: **boot-only is not living, and session-end never fires** (the hosts — michelle,
the twin, studio chat — do not emit a reliable `MemorySystem.end_session()`). A snapshot produced but
never read, plus a checkpoint whose only writer is uncalled, is how the layer looked "shipped" while
being inert in every real turn. The correct home is the **turn loop in agent_core** — the source — so
every target inherits it:

- **READ — `agent_core/chat.py::converse()`** injects `continuity_block()` (= `mem.wake()`) into the
  system prompt *every turn*, ahead of the query-driven `# Memory` block. Continuity answers "where am
  I"; memory answers "what's relevant to this query" — reorientation first. Flag-gated (`continuity=`),
  token-budgeted, fail-open.
- **WRITE — `converse()` advances the checkpoint after every turn** (`record_turn_checkpoint()`): a
  per-turn upsert of `last_interaction_summary` / `last_action`. This is the *living write* — progressive
  (turn granularity, not nightly, not per-session) and restart-durable. It does not depend on a session
  close the hosts never emit.
- **Peer turns — `peer.py::PeerExchange`** (wired). Answering a peer challenge is a reasoning turn:
  the exchange reads `continuity_block()` and passes it to the agent's `on_challenge` handler when that
  handler accepts the optional 3rd `continuity` arg (arity-detected, so 2-arg handlers keep working), and
  advances the checkpoint after the reply. This is how continuity reaches an *autonomous peer* (e.g.
  michelle) that has no human chat surface.
- **Proactive output — `comms.py::CommsLoop`** (wired). Delivering a pending utterance is a one-sided
  action, so it records an *action* checkpoint (`record_action_checkpoint`, `last_action` only). No
  continuity READ here — CommsLoop ships pre-rendered (grounded-in-surprise) utterances; it has no
  reasoning/prompt seam to inject into.
- **Shared helpers.** `continuity_block()` / `record_turn_checkpoint()` / `record_action_checkpoint()`
  live in `agent_core/continuity.py` (re-exported from `chat`), so chat/peer/comms consume ONE tested path.
- **Goal pursuit (remaining).** The pursuit loop (`GoalPursuit`, in the separate `nmem_act` package) is
  the other autonomous-turn seam — a checkpoint on each pursued goal + continuity into the proposal. Left
  as a follow-up because it crosses package boundaries.
- **Targets consume via the seams, not bespoke wiring.** An agent routes its turns through the agent_core
  seams (chat `converse`, `PeerExchange`, `CommsLoop`) and inherits continuity; agent-specific voice stays
  host-side (michelle's `_on_challenge` just injects the supplied `continuity` into her own system prompt).
  agent_core is the source; the agent is the target.

**Phase 2 — narrative_self + delta (still michelle)**
- Add `nmem_narrative_self` + `nmem_continuity_checkpoint` (schema v5→6; codex review before landing).
- Narrative writer in per-agent `consolidation.run_nightly_synthesis` with provenance + bounded length
  + periodic full reconstruction + consistency check (Gap 2).
- Introspective + external `sleep_delta` at boot; precedence + staleness + long-gap boot mode (Gap 4).

**Phase 3 — measure & tune**
- Run the eval suite vs baseline. Prove (or disprove) the layer beats a 500-token summary before
  investing further. Drift score is the headline metric.

**Phase 4 — generalize (post-pilot, separate approval)**
- DJ-AI (keeper, richest signal) then refinery agents. Refinery gate: live on **nmem 0.9.2 / sym
  0.3.0**, so a version bump to 1.0.x is a prerequisite. Concurrency upsert (Gap 5) lands here.

---

## 7. Critical files (nmem/nmem-sym libraries)
- `nmem/src/nmem/memory.py` — `briefing()` (`:617`) → new `wake()`/`continuity()`.
- `nmem/src/nmem/consolidation.py` — `run_nightly_synthesis()` / `_write_retrospective_synthesis()` →
  narrative writer + delta.
- `nmem/src/nmem/db/models.py` + new `migrations/NNN` — two thin tables, schema v5→6.
- `nmem-sym/src/nmem_sym/bridge.py` — new read-only `continuity_inputs(agent_id)`; reuse
  `augment_search()` (`:2860`). **No agency-table writes.**
- `nmem/src/nmem/agent_core/chat.py` — `converse()` reads `continuity_block()` in and advances
  `record_turn_checkpoint()` out, every turn (§6.1). The consumer seam; generic to all targets.
- `nmem/src/nmem/agent_core/runtime.py` — `install_continuity_provider()` wires the sym seam into
  `mem.wake()` at `AgentRuntime.start()`.
- Target wiring (own repo/config) — route the target's turn through `agent_core.chat.converse` rather
  than a bespoke assembler, so it inherits continuity (michelle migration = the first such target).

## 8. Verification (end-to-end, on michelle-ai)
- Cold-boot with an empty query (`"Morning."`); confirm `wake()` returns identity + current
  commitments/goals/open loops with no query to drive retrieval.
- Plant a commitment, restart the process, confirm it survives in the wake state (checkpoint durable).
- Plant an episode contradicting the narrative; confirm the consistency check flags it (Gap 2/3).
- Run the eval suite vs the static baseline; record drift, "what were you doing" accuracy,
  commitment-honouring rate. `python -m nmem_sym.status` healthcheck stays green throughout.

## 9. Coordination / open items for the Path-B sessions
- Continuity **reads** agency tables via the bridge and **never writes** them — confirm no objection to
  a read-only `continuity_inputs()` on `SymbolBridge`.
- Narrative writing rides **per-agent nmem consolidation**, not graph-global dreamstate → needs **no
  keeper coordination**; flagged so it isn't mistaken for a second dreamstate driver.
- Confirm the schema-v6 migration (two nmem-core tables) doesn't collide with any in-flight nmem-core
  migration on the other session's branch.
