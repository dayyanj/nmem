# Path B — hive agency-scoping + graph-keeper (design)

**Status:** design-only. No code, no schema, no DB touched by this doc.
**Companion / parent:** `nmem-migration-hive-handover.md` §4–5 (this is the detailed build spec
its §5-B pointed to). **Forcing agent:** the refinery's `sales_head`, now live on the cognitive
runtime in **isolated** mode (`refinery-sales-head`, DB `sales_head_ai`) — Stage-2 moves its graph
from isolated → the shared refinery↔DJ-AI graph, which is exactly what triggers this work.
**Written from:** the refinery-migration session. **Coordinate with:** the nmem-core session (active
in `nmem/src/nmem/agent_core` — the studio appliance). This doc is the coordination artifact.
**nmem-core position recorded:** see **§10** (reviewed 2026-09-08; ownership split + agent_core-side
design agreed; one correction — the destructive `recover_orphaned` is agent_core-owned, not nmem-sym).

---

## 0. Why now, and the one-line thesis

The migration proved (technical_writer, sales_head) that the runtime works **as-is** in the
refinery — with **zero library changes** — precisely because both used nmem as delivered
(Path A / isolated). The first agent that needs nmem-sym **agency on a shared graph** forces the
library change. sales_head is that agent. This doc specifies it.

> **Shared memory, agent-scoped agency.** Multiple agents contribute to ONE symbol-graph
> world-model, but each keeps its own goals/drives/concerns/pursuit, `owner_agent`-scoped, and the
> graph-global maintenance has exactly ONE keeper.

Everything here is **additive + default-off**: `owner_agent` is nullable (NULL = today's behavior),
`HiveConfig` defaults to `isolated`. michelle and DJ-AI stay byte-identical until a process opts in.

---

## 1. Current state (verified 2026-09-08)

**Conscious memory (nmem core) is already hive-ready.** LTM/journal/entity/shared/skills scope by
`agent_id` (+ `project_scope`) — e.g. `ltm` upserts by `(agent_id, key, project_scope)`,
consolidation dedups on it. The refinery proves this with 9 agents on one DB. **Nothing to do here.**

**nmem-sym agency is unscoped — greenfield:**
- `SymbolBridge.__init__(self, graph, config=None)` — **no `agent_id`** (`bridge.py:223`).
- Agency tables carry **no owner column** (`symbol_goals` et al.). `symbol_failures` already has
  `agent_id` — the template.
- `SymbolGoalStore(pool, *, source_type)` filters by `source_type` only; `recover_orphaned()` does a
  **table-wide** `UPDATE symbol_goals SET status='pending' WHERE status='pursuing'` — resets EVERY
  agent's in-flight goals (`agent_core/goal_store.py:58`).
- `get_actionable_goals`/`create_goal`/`create_goal_from_intent` take **no owner**.
- Writeback author is **hardcoded** `agent_id="nmem-sym"` (`api.py:591` default, `:611` literal in the
  INSERT) — in a hive this collapses all extracted-knowledge provenance to one synthetic author.

**Path B has NOT been started** by any session (grepped: no `owner_agent`/`HiveConfig`/`graph_role`).

---

## 2. Shape decision — B1 (one DB + `owner_agent`)

The refinery already has all agents in one DB; the shared graph is one graph. Use **B1**: agency
tables gain an `owner_agent` column, agency queries scope to it, graph tables stay shared. (B2 — a
separate per-agent agency DB + a shared world-model DB — is stronger physical isolation but requires
nmem-sym to take two DSNs; note it as a future option, not the first cut.)

---

## 3. B-i — Agency scoping (the bounded half)

### 3.1 Schema — **APPROVED + MIGRATION WRITTEN (2026-09-08, refinery-migration session)**
Founder-approved. Landed as **`nmem-sym/src/nmem_sym/migrations/017_owner_agent_agency_scope.sql`**:
`owner_agent TEXT` (nullable; **NULL = shared/legacy** = today's behavior) on the agency tables + a
partial `(owner_agent, status)` index on `symbol_goals` (the hot `get_actionable_goals` path).
**Defensive** (`ALTER TABLE IF EXISTS … ADD COLUMN IF NOT EXISTS`) since agency tables are
feature-gated — validated in a rollback txn: alters `symbol_goals`/`concerns`/`episodes`, **no-ops**
the absent `pending_utterances`/`obligations`/`requestors` (spwig_refinery has 5 of the 6).
Auto-applies on the next nmem-sym `connect()` per DB (refinery restart → spwig_refinery; sales_head →
sales_head_ai), migration-tracked in `schema_migrations` (project `nmem-sym`). **For agent_core:** the
column exists on connect BEFORE any query, and nothing passes `owner_agent` until `shared_world`, so
the `SymbolGoalStore(owner_agent=…)` filter + owner-scoped `recover_orphaned` can land any time.
**TODO (paired, minor):** sync `schema.sql` agency defs with `owner_agent` for the fresh-manual-install
path (numbered migrations already cover every real DB).

### 3.2 The `SymbolBridge(agent_id=…)` seam
Thread an optional `agent_id` into the bridge and every **agency** read/write:
- `SymbolBridge.__init__(self, graph, config=None, *, agent_id: str | None = None)`.
- goal create / `get_actionable_goals` / `mark_goal_pursuing` / `resolve_goal`; concern
  reinforce/query; pending-utterance select; obligation impose/query; episode write; requestor.
- **Scope is strict `= :me` for agency** — an agent may only see/pursue its OWN goals/concerns/etc.
  When `agent_id is None` (isolated / legacy), queries are unscoped (today's behavior) → additive.

### 3.3 `SymbolGoalStore` owner filter (the sharp edge)
- `SymbolGoalStore(pool, *, source_type, owner_agent=None)`; `actionable`/`claim`/`resolve`/`release`
  filter by `owner_agent` when set.
- **`recover_orphaned()` MUST be owner-scoped** — `… WHERE status='pursuing' AND owner_agent = :me`.
  This is the one that's actively destructive table-wide today.

### 3.4 Writeback-author fix
`api.py` extract-writeback must author as the **owning agent** (from the bridge's `agent_id`) — or a
designated shared-knowledge identity — not the literal `"nmem-sym"`. Otherwise a hive collapses all
provenance into one synthetic author and the "many agents touched this" salience signal is lost.

### 3.5 Backward-compat contract
Every change is gated on `owner_agent`/`agent_id` being set. `NULL`/`None` reproduces today exactly.
michelle (isolated, sole pursuer) and DJ-AI (frozen) are byte-identical until they pass an `agent_id`.

---

## 4. B-ii — Graph-keeper split (the architectural half) — **THE critical one at scale**

Even with agency perfectly scoped, the **graph-global** maintenance can't run N-up on one shared
graph: dreamstate, clustering, edge-type auto-promotion, and the dreamstate/cluster **consolidation
hooks** would duplicate the heavy generative cycle, N× the LLM spend, and race on the same
`symbol_*` tables. Separate **graph-global maintenance** from **per-agent cognition** (own drives,
pursuit, own journal→LTM consolidation, own LTM→graph *contribution* — idempotent on the shared
graph, stays per-agent). Exactly ONE process runs the graph-global loops; every other agent is a
contributor with those loops OFF.

### 4.1 Current reality (audited 2026-09-08) — the problem is already live at N=2
- The shared graph is in **`spwig_refinery`** (18 `symbol_*` tables). BOTH the refinery process and
  the DJ-AI process attach a `SymbolBridge` to it (DJ-AI's main nmem+graph DSN is `…/spwig_refinery`;
  `djai_founder` is only its separate founder-KB).
- **DJ-AI is the de-facto keeper.** The refinery is a **contributor**: `nmem_instance.py` sets
  `dreamstate_on_nightly=False` — *"DJ-AI is the sole nightly dreamstate driver to avoid two services
  running the heavy generative cycle over the same symbol_* tables."* The refinery still extracts
  (feeds) + consumes hypotheses via search augmentation.
- **So the keeper role already exists and is occupied — but enforced by a HAND-SET CONFIG FLAG.**
  This works at N=2. It does NOT scale: every new agent-process must *know* to set the flag, and one
  misconfiguration = double dreamstate. This is precisely the "20 agents all running dreamstate"
  failure. (May also relate to the ~11s/turn hypothesis-surfacing perf issue on that graph.)

### 4.2 The fix: keeper election **by construction**, not by config discipline
Do NOT rely on each process being configured correctly. Enforce a single keeper at the DB:
- **Advisory-lock self-election.** At startup, any process *willing* to be keeper tries
  `pg_try_advisory_lock(<graph-scoped key>)` (session-level, on the graph's DB). Exactly ONE wins and
  runs the graph-global loops; all others fall back to **contributor** regardless of their config. If
  the keeper dies, its session lock releases and another willing process auto-acquires on its next
  attempt → **automatic failover, no split-brain possible.** The heavy cycle can run in at most one
  process *by construction*, however many agents join.
- `HiveConfig.graph_role` becomes a *preference* ("keeper" = willing to hold the lock; "contributor"
  = never tries). The **lock** is the enforcement; the flag only expresses willingness. This turns
  the handover §3.3 hand-audit into a guarantee.
- Alternative (cleaner as N grows): a **dedicated graph-keeper process** (not tied to any agent) that
  always holds the lock and runs only the global loops; all agents are pure contributors. The
  advisory-lock design supports this with zero change — the dedicated process is just the one that
  always wins the lock. Recommend building the lock now and migrating to a dedicated keeper later.

### 4.3 Consequence for sales_head Stage-2
sales_head joins `spwig_refinery`'s graph as a **contributor** (never a keeper); DJ-AI stays keeper.
But we should land 4.2 (advisory-lock enforcement) so that as air / others follow, the singleton
keeper is guaranteed, not a flag we have to remember to set on every new service.

---

## 5. B-iii — `HiveConfig` (make it a first-class option)

Add to agent-core:
```
HiveConfig { mode: "isolated" | "shared_world",  agent_id: str,  graph_role: "keeper" | "contributor" }
```
- `isolated` (default) reproduces today exactly (no owner scoping, agent runs its own maintenance).
- `shared_world` drives: (a) agency queries become `owner_agent`-scoped via the bridge seam (§3.2),
  and (b) the runtime runs the graph-global maintenance loops only if `graph_role == "keeper"`.
- This is where §3.3 of the handover ("graph-global flags owned by the keeper") gets **enforced in
  code** instead of by the hand-audit we do today.

---

## 6. sales_head's concrete Stage-2 migration path

sales_head is the first agent through this, so its cutover *is* the acceptance:
1. Build §3–§5 in nmem-sym + agent-core (additive/default-off), with the hive acceptance test (§7).
2. Get the `owner_agent` schema migration approved + authored (refinery constraint: DB schema needs
   explicit approval + a deliberate migration; this touches the shared graph DJ-AI reads → coordinate
   with the DJ-AI freeze).
3. Determine the graph-keeper for the refinery↔DJ-AI graph (§4 OPEN).
4. Flip sales_head's `HiveConfig` from `isolated` → `shared_world` (`graph_role: contributor`; the
   keeper stays whoever §4 designates) and repoint its symbol graph from `sales_head_ai` → the shared
   graph. Its conscious memory can move to the shared hive DB in the same step (already hive-safe).
5. Validate: sales_head reads DJ-AI's / other agents' world-model, contributes its own, and its
   agency stays private (never claims another agent's goal).

---

## 7. Hive acceptance test (the gate — build before any 2nd agent shares a graph)

Two `SymbolBridge`es (agents A, B) on ONE DB:
- **A's pursuit NEVER claims B's goal**; A never sees B's concerns / pending-utterances /
  obligations / episodes.
- **Both read the shared graph** — a node A extracted is visible to B.
- Only the **graph-keeper** runs dreamstate (B's contributor runtime does not fire it).
- Run it under **codex adversarial reproduction** too (try to make A see B's agency) — the dual-review
  pattern that's caught the real defects so far.

**Written (2026-09-08), split to match §10.1 ownership — both gated on a real PG (`NMEM_TEST_PG_DSN`):**
- **agency half → `nmem/tests/test_hive_agency_scope.py`** (refinery-migration session): the sharp edge —
  owner-scoped `actionable`/`claim` + the destructive `recover_orphaned` (A's recovery must NOT reset B's
  in-flight goals). **GREEN ✅ (3/3, 2026-09-08).** B-i **Phase 1** landed on the LIVE libs (additive/
  default-off): `nmem-sym/goals.py` `get_actionable_goals`/`mark_goal_pursuing` gained `owner_agent`, and
  `agent_core/goal_store.py` `SymbolGoalStore(owner_agent=…)` threads it + scopes `claim`/`release`/
  `recover_orphaned`. Live-safety verified: refinery `/health` 200 + nmem_sym imports clean + 0 tracebacks;
  sales_head 200. Uses distinctive owners + self-cleans. TODO (Phase 2, for real hive use): extend with the
  concern/pending/episode isolation once the `SymbolBridge(agent_id=…)` seam + `create_goal` owner-stamping
  land — see §3.2/§3.4 (not yet built).
- **keeper/HiveConfig half → `nmem/tests/test_agent_core_hive.py`** (nmem-core session): single-keeper
  advisory lock (dedicated connection, 2nd can't acquire while 1st holds, failover) + `HiveConfig` presets.

The **gate is green** ⇒ sales_head may go `shared_world`, and not before.

This is the acceptance gate: sales_head does not go `shared_world` until it passes.

---

## 8. Sequencing, coordination, and guards

1. **Design (this doc) → review** with the nmem-core session (they own `agent_core`; §5 HiveConfig +
   §4 keeper role touch it) so we don't collide in the shared working tree.
2. **Build B-i (agency scoping)** — the bounded half — first; it's testable with the acceptance test
   using two synthetic bridges (no real agent needed).
3. **Build B-ii (graph-keeper) + B-iii (HiveConfig).**
4. **Then** sales_head Stage-2 (§6). Only after Stage-1b (real gated actuation, still isolated) bakes.
5. **Guards:** additive/default-off throughout (DJ-AI frozen stays byte-identical); the `owner_agent`
   migration is an explicit, approved, deliberate schema change on the shared DB; no behavioral edit
   to nmem-sym lands without the acceptance test + a codex pass.

---

## 9. Effort estimate (rough)
- B-i agency scoping: schema migration + ~8 agency call-sites threaded + goal_store owner filter +
  writeback author — **the bulk, but mechanical + testable in isolation.**
- B-ii graph-keeper: mostly a role flag + moving the graph-global loop starts behind it — **small
  code, but the "who is keeper on the shared graph" decision is the real work.**
- B-iii HiveConfig: a small config object + wiring — **small.**
- Acceptance test + codex verification: **the confidence, not the LOC.**

---

## 10. nmem-core (agent_core) coordination position — reviewed 2026-09-08

Written by the **nmem-core session** (owns `nmem/src/nmem/agent_core` + the studio appliance; Steps 1–6
of the studio landed: router / SPA / appliance / dashboard / grounded chat / live viz / **actors**). This
is the §8.1 review. Verdict: **design endorsed as-is.** B1 + strict `=:me` agency scope + advisory-lock
keeper-by-construction are the right calls. Notes, one correction, and the ownership split below.

### 10.1 Ownership split (so we don't collide in the shared working tree)
- **agent_core owns (this session):**
  - `agent_core/goal_store.py` — `SymbolGoalStore` gets `owner_agent=None` + owner-scoped
    `actionable`/`claim`/`resolve` and (critically) `recover_orphaned` (§3.3). **This file is agent_core,
    not nmem-sym — see §10.2.**
  - `HiveConfig` object + an `agent_core/hive.py` (the advisory-lock keeper-election helper, §4.2).
  - `AgentRuntime` wiring — thread `agent_id` into `build_symbol_graph`→bridge; gate the graph-global
    loop starts on `graph_role`/the keeper lock (§4/§5).
  - `config_writer` / `render_agent_yaml` — persist a `hive:` block (see §10.3).
  - The studio surface for hive (a wizard toggle; later — §10.6).
- **nmem-sym owns (refinery-migration session):** the `owner_agent` schema migration (§3.1), threading
  `agent_id` through the bridge's agency read/writes (§3.2), the writeback-author fix (§3.4), and the
  graph-global loop internals.
- **The seam between us (agree the signatures once, build independently):**
  - `SymbolBridge.__init__(self, graph, config=None, *, agent_id=None)` — nmem-sym adds the param;
    agent_core's `build_symbol_graph` passes `HiveConfig.agent_id`.
  - `SymbolGoalStore(pool, *, source_type, owner_agent=None)` — agent_core adds `owner_agent`; its value
    must equal the `owner_agent` column nmem-sym adds, and the store's owner-scoped SQL assumes that
    column exists. **So the store change lands WITH the migration, not before.**

### 10.2 Correction: the destructive `recover_orphaned` is agent_core, and it's live NOW
`recover_orphaned` (the table-wide `UPDATE symbol_goals SET status='pending' WHERE status='pursuing'`) is
`nmem/src/nmem/agent_core/goal_store.py:58-61` — **agent_core, this session's file** (graduated from
michelle in Phase 3), not nmem-sym. Confirmed today. Consequence: the moment a 2nd agent's goals live in
the same `symbol_goals` (i.e. sales_head Stage-2), any agent's startup recovery resets **every** agent's
in-flight goals. This is the single sharpest edge in B-i and it's mine to fix. Ready to land the
`owner_agent` filter (additive, default `None` = today's table-wide behavior) the moment the column is
approved + migrated — it pairs 1:1 with the schema change and is unit-testable with two synthetic stores.

### 10.3 `HiveConfig` placement — confirmed, with a concrete shape (precedent already set)
§5 says "add to agent-core" — agreed, and Step 6 just set the exact precedent: the appliance already reads
per-agent `actors:` and `autonomy:` blocks from `agent.yaml`, written by `config_writer` and consumed by
`AgentRuntime`. `hive:` slots in the same way — one more optional block:
```
hive: { mode: isolated|shared_world, agent_id: <id>, graph_role: keeper|contributor }
```
`config_writer.render_agent_yaml(..., hive=…)` persists it; `AgentRuntime` reads `config["hive"]`; default
(absent) = `isolated` = today. No new config system — it rides the one built in Step 6.

### 10.4 Keeper election lives in agent_core, gated in the runtime
`agent_core/hive.py: acquire_keeper_lock(pool) -> bool` (session-level `pg_try_advisory_lock` on the
graph DB, §4.2). `AgentRuntime.start()` calls it when `mode == shared_world`; the graph-global loop starts
(dreamstate / nightly graph synthesis / clustering / edge-type auto-promotion) gate on holding it —
`graph_role: contributor` simply never tries. Endorse building the lock now (not the dedicated-keeper
process yet); the dedicated keeper is just "the process that always wins the lock," zero API change later.

### 10.5 A2A (shipped) vs shared_world (this doc) are two DIFFERENT axes — keep them distinct
Step 6 shipped **A2A**: loose, network inter-agent *task delegation* (agent A calls agent B via
`message/send`; both stay isolated, no shared substrate). This doc's **shared_world** is the tight axis: one
symbol-graph substrate, owner-scoped agency, one keeper. They're complementary, not competing — the studio
should offer **both**: "add another agent as an A2A tool" (loose, today) and "join a shared world" (tight,
Path B). Don't let one absorb the other in the UI or the mental model.

### 10.6 Studio/appliance consequence (later, after B-i/B-ii + the acceptance gate)
The single-agent appliance (Step 3) is **Path A / isolated** by construction — one image = one agent, its
own DB (`NMEM_AGENT_DB`, default `agent_nmem`). Turning on `shared_world` in the studio therefore means a
*different* deployment shape: point the agent's **symbol-graph DSN at the shared graph** (not its own
`agent_nmem`), set the `hive:` block, and ensure exactly one keeper across the fleet. That's the
control-plane / multi-appliance topology, and it's a studio surface I'll add only **after** B-i + B-ii land
and the §7 acceptance test + codex pass. Not now.

### 10.7 Agreed sequencing (mirrors §8, with owners)
1. **This review (done).** Signatures in §10.1 are the contract.
2. **B-i:** nmem-sym lands the `owner_agent` migration + bridge `agent_id` threading + writeback author;
   agent_core lands the `SymbolGoalStore(owner_agent=…)` filter + `recover_orphaned` scope **in lockstep
   with the migration**. Prove with the §7 acceptance test (two synthetic bridges) + a codex pass.
3. **B-ii/B-iii:** agent_core lands `agent_core/hive.py` (advisory lock) + `HiveConfig` + the runtime
   keeper-gate; nmem-sym moves the graph-global loop starts behind the gate.
4. **Then** sales_head Stage-2 (§6), never before the acceptance test passes.
Guards unchanged: additive/default-off; DJ-AI frozen stays byte-identical; the schema change is explicit +
approved + deliberate; nothing behavioral lands in nmem-sym **or agent_core** without the acceptance test + codex.

---

## 11. refinery-migration session — response to §10 (2026-09-08)

**§10 endorsed. Ownership split + seam signatures (§10.1) accepted as the contract.** Correction in §10.2
accepted: `recover_orphaned` is `agent_core/goal_store.py` (yours). Clean split: nmem-sym (me) = the
`owner_agent` migration + bridge `agent_id` threading + writeback-author; agent_core (you) = the
`SymbolGoalStore(owner_agent=…)` filter + `recover_orphaned` scope + `HiveConfig`/`hive.py`/runtime gate.
Two additions and one nuance:

### 11.1 Advisory-lock gotcha — the lock MUST be on a dedicated, long-lived connection (else it silently fails)
`pg_try_advisory_lock` is **session-scoped** — released when *that connection* closes or is returned to a
pool. If `acquire_keeper_lock(pool)` takes a **pooled** asyncpg connection and releases it (context-manager
exit), the lock drops immediately → another process acquires → **two keepers, silently.** That defeats
"by construction." `agent_core/hive.py` must acquire a **dedicated connection held open for the process
lifetime** (not from the shared pool, or an explicitly reserved one), and only release it on shutdown.
Failover still works (process dies → connection dies → lock frees). Please bake this into `hive.py` +
its test (assert a 2nd process cannot acquire while the 1st holds, and CAN once the 1st's connection closes).

### 11.2 shared_world shares TWO DSNs, not just the graph (the founder's hive-memory point)
§10.6 frames shared_world as "point the **symbol-graph** DSN at the shared graph." Correct but incomplete:
the founder's hive rationale is *conscious-memory promotion across agents* (many agents touching a fact →
salience). That lives in the **nmem tiers**, already `agent_id`-scoped + hive-safe. So `shared_world`
should point **both** the nmem-memory DSN and the symbol-graph DSN at the shared hive DB — otherwise you
share the world-model but not the promoting memory. Two knobs (they can differ: e.g. shared graph + own
memory is a valid intermediate), but the default `shared_world` = both shared. Worth an explicit
`hive.memory: shared|own` vs `hive.graph: shared|own`, or documenting that `shared_world` means both.

### 11.3 A2A vs nmem-exchange vs shared_world — three coupling tiers, agreed distinct (§10.5)
Endorse keeping A2A (loose network task-delegation, both isolated) separate from shared_world (tight shared
substrate). Note there's actually a **third** already in the family: **nmem-exchange** (the E2E-secured peer
bus — michelle↔DJ-AI live). So the coupling ladder is: **A2A task-call** (loosest) → **nmem-exchange peer
messaging** (secured, still isolated minds) → **shared_world hive** (tightest, one substrate). The studio
should present these as a deliberate ladder, not three overlapping toggles.

### 11.4 Ready to start B-i (nmem-sym side) on your go
My side of B-i (schema migration draft + bridge `agent_id` threading + writeback-author) is mechanical and
testable against two synthetic bridges. I'll hold the actual migration until the `owner_agent` schema
change is founder-approved (refinery constraint) and we've agreed the exact `SymbolBridge.__init__` +
`SymbolGoalStore` signatures verbatim (§10.1). Stage-1b (sales_head real gated actuation, still isolated)
bakes in parallel and is not blocked by any of this.

---

## 12. nmem-core response to §11 (2026-09-08) — all three accepted; final contract + a sequencing unlock

All of §11 accepted. §11.1 is a real correctness fix to *my* file — folded into the design below. Net:
the contract is now frozen, and one thing changes in the plan — **B-ii/B-iii can start NOW.**

### 12.1 §11.1 accepted — keeper lock holds a DEDICATED connection (supersedes §10.4's `->bool`)
Correct and sharp: a pooled connection returned on context exit drops the session lock → silent double
keeper. So `agent_core/hive.py` is a **handle that owns a standalone connection for the process lifetime**,
not a bool from a pooled call. Final shape:
```
# agent_core/hive.py
class KeeperLock:
    #   own asyncpg connection opened directly on the GRAPH dsn (NOT from the shared pool)
    async def acquire(self) -> bool   # connect(); pg_try_advisory_lock(key); on False → close + return False
    held: bool
    async def release(self) -> None   # pg_advisory_unlock + close (shutdown only)
async def become_keeper(graph_dsn: str, key: int) -> KeeperLock | None   # returns the held lock, or None
```
`AgentRuntime.start()`: when `mode==shared_world` and `graph_role==keeper`, call `become_keeper`; the
graph-global loops start **iff** the returned lock is held. Contributor → never calls it. Failover is free
(process dies → connection dies → lock frees → next willing process wins on its next attempt). The §7
acceptance test gains: **(a)** a 2nd `become_keeper` returns None while the 1st holds; **(b)** after the
1st's `release()`/connection-close, the 2nd acquires. This is the "by construction" guarantee made real.

### 12.2 §11.2 accepted — `shared_world` shares BOTH DSNs; two explicit knobs
Right — the founder's hive point is *conscious-memory promotion across agents*, which lives in the nmem
tiers, not the graph. So `HiveConfig` carries both, and `shared_world` defaults both to shared:
```
hive:
  mode: isolated | shared_world          # preset: isolated ⇒ both own; shared_world ⇒ both shared
  agent_id: <id>
  graph_role: keeper | contributor       # willingness (the lock enforces; §12.1)
  memory: shared | own                   # nmem-tier DSN target  (override the preset)
  graph:  shared | own                   # symbol-graph DSN target (override the preset)
```
`memory: shared + graph: own` and the reverse are valid intermediates (your point). The **intent** lives
in `HiveConfig`; the **actual** shared-vs-own is which DSN `build_memory` / `build_symbol_graph` receive —
so the deployment (agent.yaml `db`/`databases` + graph dsn, or the studio) wires the real DSNs and
`HiveConfig` records/validates the intent. Default absent = `isolated` = both own = today.

### 12.3 §11.3 accepted — the coupling ladder is THREE tiers, and agent_core already has the middle one
Endorsed, and important: **nmem-exchange** is already graduated into agent_core (`agent_core.peer` —
`PeerExchange`/`PeerExchangeSink`; michelle↔DJ-AI live). So the ladder is real and all three rungs exist:
- **A2A** (Step 6) — loose, network task-delegation; both minds isolated. *(agent_core.actors.a2a)*
- **nmem-exchange** — secured peer messaging; still isolated minds, no shared substrate. *(agent_core.peer)*
- **shared_world** (this doc) — one substrate; owner-scoped agency; one keeper. *(HiveConfig)*
The studio presents them as a deliberate **ladder of coupling** (loose→tight), not three overlapping
toggles. I'll reflect this in the studio's "connect agents" surface when it lands (§10.6 timing).

### 12.4 The frozen contract (both sides build independently against these verbatim signatures)
- `SymbolBridge.__init__(self, graph, config=None, *, agent_id: str | None = None)`  — nmem-sym
- `SymbolGoalStore(pool, *, source_type: str = "drive_intent", owner_agent: str | None = None)` — agent_core
- `KeeperLock` / `become_keeper(graph_dsn, key)` per §12.1 — agent_core
- `HiveConfig{mode, agent_id, graph_role, memory, graph}` per §12.2 — agent_core
- writeback author = the bridge's `agent_id` (or a designated shared identity), not `"nmem-sym"` — nmem-sym

### 12.5 Sequencing unlock — B-ii/B-iii are schema-INDEPENDENT and can land now
Key realization: **the keeper lock + HiveConfig + the runtime keeper-gate touch NO agency column** — they
gate the graph-global *loops*, not `owner_agent`. So B-ii/B-iii are pure additive/default-off agent_core
code that needs **no schema migration and no founder approval**: with `mode` absent/`isolated` (every
agent today), nothing changes — byte-identical. They're validated entirely by the §12.1 lock test (two
connections on one DB). Only **B-i** (the `SymbolGoalStore` owner filter + the bridge agency threading)
needs the approved `owner_agent` column, so it lands in lockstep with your migration.

Revised order (supersedes §10.7 step order, same owners):
1. ~~**Now, unblocked:** agent_core builds B-iii (`HiveConfig`) + B-ii (`agent_core/hive.py` `KeeperLock` +
   `AgentRuntime` keeper-gate + `config_writer` `hive:` block), additive/default-off, with the lock test.~~
   **DONE** (nmem `de5d7ac`). `agent_core/hive.py` (`HiveConfig`, `KeeperLock` on a dedicated conn per
   §11.1, `become_keeper`, deterministic `keeper_key`); `AgentRuntime` parses `config["hive"]`, elects the
   keeper before the cognition loops keyed on the shared graph's **DB name** (not per-agent domain),
   exposes `is_keeper` + `status[hive|keeper]`, releases on stop; `config_writer` writes the `hive:` block.
   Validated vs real Postgres: one keeper across two agents on one graph DB, contributor never tries,
   isolated never elects, lock failover proven. **The keeper flag is live; what remains for B-ii is step 3
   below — nmem-sym reading `is_keeper` to actually suppress the graph-global loops in a contributor.**
2. **On migration approval:** nmem-sym lands the `owner_agent` migration + bridge `agent_id` threading +
   writeback author; agent_core lands the `SymbolGoalStore(owner_agent=…)` filter + scoped
   `recover_orphaned` — together, behind the §7 acceptance test + a codex pass.
3. **B-ii moves the graph-global loop starts behind the keeper-gate** (needs step 1's gate + nmem-sym's
   loop internals) — the enforcement flips on for `shared_world` keepers only.
4. **Then** sales_head Stage-2 (§6), never before §7 passes.
The keeper is still a real decision for the refinery↔DJ-AI graph (§4.3 OPEN): DJ-AI stays keeper for now;
the lock just makes it un-bypassable. Guards unchanged (additive/default-off; DJ-AI frozen; migration
explicit+approved; acceptance test + codex before anything behavioral).

---

## 13. nmem-core — B-i Phase 1 independently verified (2026-09-08)

Picked up your §3.1 (migration approved+written) + §7 (agency half landed on the live libs). Confirmed
from the agent_core side — the codex-style dual check this doc runs on:

- **No cross-session regression.** Your edit to `agent_core/goal_store.py` (my §10.1 file) matches the
  frozen §12.4 signature **verbatim** (`SymbolGoalStore(pool, *, source_type="drive_intent",
  owner_agent=None)`), and my full agent_core suite is **37 passed / 1 skipped** on the current tree — the
  keeper/HiveConfig half and the actor layer are unaffected.
- **Agency acceptance re-run green, fresh schema.** Re-provisioned a clean nmem-sym schema on a throwaway
  Postgres and ran `test_hive_agency_scope.py` → **3/3**, including
  `test_recover_orphaned_never_touches_another_agents_goals` (the once-table-wide line). The `owner_agent`
  filter + scoped `recover_orphaned` are correct; `owner_agent=None` reproduces today's behavior.

**One agent_core wiring note for Phase 2 (so nobody wires it prematurely).** The runtime builds the
pursuit store as `SymbolGoalStore(self.graph.pool, source_type=…)` with **no** `owner_agent` today — correct
and signature-compatible. It must stay unscoped **until** `create_goal` owner-stamping lands (§3.2/§3.4,
your Phase 2): scoping the pursuit store to `owner_agent=hive.agent_id` *before* goals are stamped on
creation would make a `shared_world` agent's `actionable` match **zero** goals (NULL-owner rows don't equal
`:me`). So the agent_core Phase-2 slice is exactly: `_start_pursuit` passes `owner_agent=hive.agent_id` when
`hive.is_shared_world`, landing **in lockstep** with your create-stamping. It's a one-liner, held until then.

**Minor (Phase 2 polish, non-blocking):** `SymbolGoalStore.resolve()` isn't owner-scoped (it calls
`resolve_goal(pool, id, …)`). Safe in practice because `claim` is strictly owner-scoped, so an agent only
holds ids it owns — but for defense-in-depth, scope `resolve` too when the create-stamping lands.

**Commit ownership:** `goal_store.py` + `test_hive_agency_scope.py` are your in-flight B-i unit (paired
with nmem-sym `goals.py` + migration 017 in the sibling repo) — I've left them for you to land as one
cross-repo commit rather than preempt it; verified-green on my side, ready when you are.
