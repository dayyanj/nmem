# NMEM stack — migration & hive/segregation HANDOVER

**Status:** authoritative handover, 2026-09-08. **Audience:** the refinery-migration session (and any
future session touching the NMEM stack). **Why this doc exists:** the migration session surfaced the
shared-DB segregation issue; *this* session carries the whole-stack integration context (the capability
sweep, the agent-core upstreaming, the validation methodology, the env-flag footguns), so the plan is
authored here. Treat this as the source of truth; it is written to be read cold.

**Companion docs (read as needed):**
`nmem-agent-core-plan.md` (the runtime + what's graduated) · `capability-activation-sweep.md` (every
capability flag + the dependency map) · `hive-segregation-exploration.md` (the segregation survey this
plan builds on) · `native-vs-bespoke-test-plan.md` · `config-alignment-plan.md`.

---

## 0. TL;DR

1. **Two paths, both supported, selected by config** — **A: Isolated** (default, safe, what runs today)
   and **B: Hive / shared world-model** (opt-in). Never make B the default; never let an agent land in B
   implicitly.
2. **The nmem (conscious-memory) half of a hive is already solved and proven** — the refinery runs 9
   agents on one DB with `agent_id`-scoped private tiers + a cross-agent `shared` tier. A migrating agent
   inherits that for free.
3. **The work is downstream (nmem-sym / nmem-act)** and splits into a *small* part (agency scoping) and
   an *architectural* part (a shared graph needs a single graph-keeper).
4. **Environment-flag discipline is a hard rule** (§3). It has bitten us; do not let it regress.
5. **Validate-then-thin, live, with codex as an adversarial second reviewer** (§6). This is how the whole
   sweep + upstreaming was validated; keep doing it.

---

## 1. The NMEM stack (layers + data flow + current state)

```
        ┌─────────────────────────────────────────────────────────────┐
 nmem   │ conscious memory: working / journal / ltm / entity (PRIVATE, │  agent_id-scoped
        │ agent_id-keyed) + shared + policy (CROSS-AGENT).             │  ✅ hive-ready
        │ skills / context_recipes / commitments (agent_id = me OR NULL)│
        └───────────────┬─────────────────────────────────────────────┘
                        │ extraction (LTM → graph), request_surface (recall)
        ┌───────────────▼─────────────────────────────────────────────┐
 nmem-  │ symbol graph WORLD-MODEL: nodes / edges / hypotheses /        │  UNSCOPED
 sym    │ procedures / edge-type vocab.  + per-agent AGENCY: goals /    │  (single-agent-
        │ concerns / pending_utterances / obligations / episodes /      │   per-DB today)
        │ drives (in-proc).  Maintenance loops: dreamstate/cluster/    │
        │ edge-promotion (GRAPH-GLOBAL) + consolidation hooks.          │
        └───────────────┬─────────────────────────────────────────────┘
                        │ ActionProposal → Outcome, GoalPursuit lifecycle
        ┌───────────────▼─────────────────────────────────────────────┐
 nmem-  │ actuation: ReferenceRunner, GoalPursuit (claim→execute→      │  dependency-free;
 act    │ classify→resolve/release→recover), experiential outcome sink,│  agent passes
        │ reflective skill capture. Dependency-free (injected callables)│  seams
        └──────────────────────────────────────────────────────────────┘

 nmem.agent_core  = the reusable glue: AgentRuntime (headless boot/run/shutdown + all cognition
                    wiring), memory/backend/persona bootstrap, recall consumer, comms (CommsLoop),
                    peer (PeerExchange), experiential sink, ops router. A new agent = config +
                    persona + (executor) + host I/O.
 nmem-exchange    = agent↔agent messaging bus (secured envelopes). agent_core.peer wraps it.
 nmem-viz         = observation/telemetry.
```

**Current state (2026-09-08):**
- **agent-core is lift-and-shift ready.** Groups 1+2+3F of the upstream plan are done. A pure-thinker
  agent = persona + config + ~20-line main (see `nmem/examples/minimal_agent/`); an acting agent adds an
  executor + `build_proposal`.
- **michelle** is the reference agent + live proving ground: her own DB (`michelle_ai` on :5433), boots
  entirely on `AgentRuntime`, uses the full stack. **ISOLATED** (Path A).
- **DJ-AI** is a separate host, its own store. ⚠️ **FROZEN** — do not restart it into new lib code until a
  coordinated lib-update+restart (it shares the nmem libraries; all our changes are default-off so it stays
  byte-identical until then).
- **refinery** = 9 agents, one `spwig_refinery` DB, shared `nmem_*` (agent_id-scoped + shared tier). Only
  ~1 agent uses nmem-sym, partially — so **no shared-graph contamination today.** This is the target of
  the migration.

---

## 2. How an agent is stood up on agent-core (the shape every migrated agent takes)

A new/migrated agent supplies **four things, no cognition code**:

1. **config** (`agent.yaml`-shape dict): DB DSN, LLM/embed endpoints, symbol-graph domain, loop cadences,
   and (for actors) a `pursuit` block. See `examples/minimal_agent/agent.yaml`.
2. **capabilities.env**: which nmem/nmem-sym capabilities are on (see §3 for the RULES).
3. **persona** (`nmem.agent_core.Persona`): objectives, world-seed topics, baseline KB, capabilities,
   `world_entities`. Seeded idempotently by `seed_persona` at boot.
4. **entrypoint**: build + run an `AgentRuntime`. Headless — the agent brings its own I/O (FastAPI, voice,
   CLI, or the refinery's existing worker loop).

```python
runtime = AgentRuntime(
    config, persona,
    build_executor=lambda bridge: build_my_runner(bridge),   # -> nmem_act runner (actors only)
    build_proposal=my_build_proposal,                        # PursuitGoal -> ActionProposal (actors only)
    comms_sink=my_peer.comms_sink,                           # optional (communication drive)
    skill_chronic=my_chronic_handler,                        # optional
    mem=prebuilt_mem, graph=prebuilt_graph, backend=prebuilt_backend,  # optional: pass or let it build
)
await runtime.start(); ...; await runtime.stop()
```

`AgentRuntime` can **build** mem/graph/backend (the example path) or accept **pre-built** ones (michelle's
path — a host that already owns its boot). It owns only what it built (ownership-tracked close). Mount
`agent_core.ops.make_ops_router(get_runtime, extra_health=…)` for `/health` + `/admin/*` ops.

**Refinery note:** refinery agents already have a worker loop + shared DB. They will pass **pre-built
mem/graph** (the refinery's) and likely run the runtime's cognitive loops selectively. Reuse michelle's
`start_cognition`/`build_persona` pattern as the template.

---

## 3. ENVIRONMENT-FLAG RULES  ⚠️ regression-guard — do not violate

These have bitten us (crash loops + silently-disabled subsystems). They are non-negotiable.

### 3.1 The model
- **nmem / nmem-sym config are pydantic `BaseSettings` singletons** parsed from the environment at import,
  per repo prefix: `NMEM_` (nmem), `NMEM_SYM_` (nmem-sym). Nested sections use the `__` delimiter:
  `NMEM_AUTONOMY__ENABLED`, `NMEM_SKILLS__DEDUP`, `NMEM_COMMITMENT_DETECTION__ENABLED`,
  `NMEM_SELF_ENGINEERING__INCLUDE_IN_PROMPT`.
- **`capabilities.env` is the SINGLE SOURCE OF TRUTH** for capability toggles. Booleans are `true`/`false`
  (pydantic), and `NMEM_SYM_*` legacy `0`/`1` integer flags where the field is an int.
- **nmem-act is dependency-free** — it has NO pydantic settings. Its flags are passed as **constructor
  args** by the host (e.g. `make_reflective_sink(enabled=…)`); the host decides how to read them.

### 3.2 The rules (DO / DON'T)
- ✅ **Read the typed config field** in host code: `from nmem_sym import config as sym_config;
  sym_config.settings.drives_enabled`. Likewise `mem._config.autonomy.enabled`.
- ❌ **NEVER re-parse env in the host** with an ad-hoc `os.environ.get("NMEM_SYM_X") == "1"` helper. When
  the env moved from `1`/`0` to `true`/`false`, exactly such a host helper **silently disabled every
  subsystem** (the `_env_on` footgun). Booleans changed shape; typed fields didn't. Trust the typed field.
- ❌ **NEVER pass capability toggles as kwargs** to `NmemConfig(...)` / `BridgeConfig(...)`. They would
  **override the env and split-brain the manifest**. Pass only *structural* config (DSN, endpoints,
  dimensions, belief/policy) as kwargs; let capability flags come from env. (`agent_core.build_memory`
  deliberately follows this — copy it.)
- ❌ **NO inline `#` comments on an ENABLED line** in `capabilities.env`. Env-file parsing keeps everything
  after `=` as the value, so `FLAG=true   # note` becomes the string `"true   # note"` → pydantic
  `bool_parsing` error → **startup crash loop**. Put the note on its own line ABOVE the flag. (This
  crash-looped michelle for ~90s. If it happens: `sed -i -E '/^NMEM/ s/[[:space:]]*#.*$//' capabilities.env`.)
- ✅ **Dependency flags live in the doc, not inline.** Some flags require others (`RECALL_DRIVE` requires
  `AUTONOMY__ENABLED`; `PENDING_UTTERANCES` requires `OUTCOME_SURPRISE`; `DRIVES_CREATE_GOALS`/
  `DRIVES_GOAL_LLM_ENRICH` require `CONCERNS_ENABLED`; recall's `RECALL_AGENT_ID` must equal the agent's
  id or it surfaces the wrong store). The authoritative dependency map is in
  `capability-activation-sweep.md` — consult it before enabling anything. Do NOT encode deps as inline
  comments (see the previous rule).
- ✅ **Default-off / additive.** Every new capability defaults OFF. This is the invariant that keeps a
  SHARED library safe across agents *and sessions*: DJ-AI, michelle, and refinery agents stay
  byte-identical until a flag is explicitly set in THEIR `capabilities.env`. Never flip a default to on.
- ✅ **CSV list fields** use `Annotated[list[str], NoDecode]` + a `_split_csv` validator (e.g.
  `DRIVES_OUTWARD_ACTIONS=explore,ground_sensory`). Structured dict defaults are set in the config class,
  not the env.
- ⚠️ **Known no-op / gotcha flags:** `guided_json` is ignored by vLLM 0.18 — `response_format` is the only
  enforcing path (don't rely on `guided_json`). `NMEM_IMMUNE_DB_DSN` is unwired in-repo. `thinking_budget`
  is a no-op on the Qwen fork; `reasoning_effort` is the lever.

### 3.3 Hive-specific env rules (NEW — see §4/§5)
- **Per-agent env is per-process.** Each agent process has its own `capabilities.env`. In a shared DB,
  agents MAY run different *agency* capability sets (one actor + one pure-thinker is fine).
- ⚠️ **Graph-global capability flags must be owned by the graph-keeper, not set per-agent.** In a shared
  world-model, the flags that drive graph-global maintenance — `DREAMSTATE_*`, `CLUSTER_*`,
  `EXTRACT_AUTOPROMOTE_EDGE_TYPES_ENABLED`, and the dreamstate/cluster consolidation hooks — must be
  enabled on the **single graph-keeper** and OFF on the other agents, or N agents fight over one graph
  (duplicate dreamstate, N× LLM spend, races). This rule does not exist yet in code; §5 adds a
  `HiveConfig` role to enforce it. Until then, in any shared-graph pilot, hand-audit that only ONE process
  has the graph-global flags on.

---

## 4. The two paths (architecture)

### Path A — Isolated (DEFAULT)
Each agent gets its own cognitive substrate — own DB (michelle) or own owner-scoped space. Zero shared
graph. "Collaboration" happens through the pieces that are already cross-agent:
- nmem **`shared` tier** (canonical cross-agent facts — already scoped), and
- **nmem-exchange** peer messaging (`agent_core.peer` — already built).

**What it needs:** nothing new. A migrating agent adopts agent-core in isolated mode and uses its
`agent_id`. This is safe **today** and is where every agent should start.

### Path B — Hive / shared world-model (OPT-IN)
Multiple agents share ONE symbol-graph world-model (collectively built knowledge) while each keeps its own
agency (goals/drives/pursuit/concerns). "Shared memory, agent-scoped agency" — extended to the symbol
graph. Three layers:

| Layer | In a hive | State |
|---|---|---|
| nmem conscious memory | private tiers agent_id-scoped; `shared`/`policy` cross-agent | ✅ done (refinery proves it) |
| nmem-sym **world-model** (nodes/edges/hypotheses/procedures) | **shared** — one graph everyone reads/contributes to; provenance via `source_ids` → agent nmem entries | storage already global-per-DB; **needs a single graph-keeper for maintenance** |
| nmem-sym/nmem-act **agency** (goals/concerns/pending/obligations/episodes) | **per-agent** — `owner_agent`-scoped | ✗ **must be built** (currently unscoped) |

Two implementation shapes for the shared graph:
- **B1 — one DB + `owner_agent` column on agency tables** (recommended for the refinery: it already has
  all agents in one DB). Agency co-located but query-scoped; graph tables stay shared.
- **B2 — two DBs: a shared world-model DB + a per-agent agency DB.** Stronger *physical* isolation of
  agency; requires nmem-sym to take two DSNs. Heavier; note as a future option, not the first cut.

---

## 5. Detailed plan

### Path A plan — migrate a refinery agent to agent-core (isolated)
1. Give the agent a `Persona` (objectives/world-seed/baseline-KB) + prompt files.
2. Point it at the refinery's existing mem/graph (pass **pre-built** to `AgentRuntime`) OR its own,
   depending on isolation choice. In isolated mode, use its own symbol-graph `domain` (distinct per agent)
   — but note `domain` is NOT a row-scope (see §6 caveat), so isolation in a *shared DB* is NOT achieved
   by `domain` alone. **Isolated-in-a-shared-DB requires Path B agency scoping anyway** if the agent uses
   nmem-sym agency. If the agent is a *pure thinker* or doesn't use nmem-sym, isolation is automatic.
3. `capabilities.env` per §3 (start from michelle's validated set; enable per the dependency map).
4. Wire the agent's executor (if it acts) + `build_proposal`; reuse `build_experiential_sink`.
5. Validate per §6.

> ⚠️ **The subtlety the migration must not miss:** in the refinery's *single shared DB*, an agent that uses
> nmem-sym **agency** (goals/pursuit/etc.) is NOT isolated even with a distinct `domain` — because agency
> tables are unscoped. So: **either** the migrated agent avoids nmem-sym agency (pure-thinker / nmem-only),
> **or** you must do Path B agency scoping first. There is no "isolated nmem-sym agency in a shared DB"
> without §5-BwB1.

### Path B plan — hive (do when the 2nd nmem-sym-agency agent goes shared)
**B-i. Agency scoping (the bounded half).**
- Add `owner_agent TEXT` (nullable; NULL = shared) to the agency tables:
  `symbol_goals`, `symbol_concerns`, `symbol_pending_utterances`, `symbol_obligations`,
  `symbol_requestors`, `symbol_episodes`. (`symbol_failures` already has `agent_id` — use it as the
  template.)
- Add an **`agent_id` seam to `SymbolBridge`** (`SymbolBridge(..., agent_id=…)`) and thread it into every
  agency read/write: goal create/`get_actionable_goals`/claim/resolve, concern reinforce/query, pending
  select, obligation impose/query, episode write. Scope strict `= :me` for agency (an agent must only
  pursue/see its own goals/concerns/etc.).
- Fix the **writeback author**: nmem-sym currently writes extracted knowledge back to nmem as hardcoded
  `agent_id="nmem-sym"` (`api.py`, `bridge.py`). Author as the owning agent (or a designated
  shared-knowledge identity) so a hive doesn't collapse all provenance into one synthetic author.
- `agent_core.SymbolGoalStore` already filters by `source_type`; extend it to also scope by `owner_agent`
  when a hive is configured.

**B-ii. Graph-keeper split (the architectural half).**
- Separate **graph-global maintenance** (dreamstate, clustering, edge-type auto-promotion, and the
  consolidation *dreamstate/cluster hooks*) from **per-agent cognition** (own drives, pursuit, own
  journal→LTM consolidation, own LTM→graph extraction = *contribution*, which stays per-agent and is
  idempotent on the shared graph).
- Designate ONE **graph-keeper** role (a dedicated process, or one nominated agent) that runs the
  graph-global loops; all other agents run their per-agent loops with the graph-global hooks OFF.
- In `AgentRuntime`, this becomes a role flag: build the runtime with graph-maintenance loops enabled
  (keeper) or disabled (contributor).

**B-iii. `HiveConfig` — make it a first-class OPTION.**
- Add `HiveConfig` to agent-core: `{ mode: "isolated" | "shared_world", agent_id, graph_role:
  "keeper" | "contributor" }`. `isolated` is the default and reproduces today exactly.
- `HiveConfig` drives: whether agency queries are `owner_agent`-scoped, and whether the runtime runs the
  graph-global maintenance loops (keeper) or not (contributor). This is where §3.3's "graph-global flags
  owned by the keeper" gets *enforced in code* instead of by hand-audit.

**B-iv. Validation** — see §6 (the hive integration test is the acceptance gate).

---

## 6. Validation & test approach (how we've validated the whole stack — keep doing this)

### 6.1 The methodology
- **Validate-then-thin, never blind-cut.** Enable the native/library mechanism, run it **beside** the
  bespoke one (both into the same store, or A/B by flag), compare over real cycles, confirm
  parity-or-better, **then** delete the bespoke code. The live agent (michelle) is the integration test
  throughout. Regressions are expected on young natives — that's the point; fix the library.
- **Live validation on the running agent** (dj-ai-03): `systemctl --user restart michelle-ai` →
  `/health` 200 → `journalctl --user -u michelle-ai` greps for the specific log line the change should
  produce → **DB before/after counts** to prove no data loss/orphaning (e.g. Group 1A confirmed
  `domain=michelle-cognition` + 836 nodes/214 ltm unchanged before deleting the old bootstrap).
- **`0 tracebacks` is a health gate.** `journalctl … | grep -c Traceback` must be 0 after a change.
- **The `/admin/*` ops endpoints ARE the harness** (now `agent_core.ops.make_ops_router`):
  `/admin/consolidate` (full cycle), `/admin/nightly` (the nightly path — commitment detection +
  self-engineering + retrospective; NOT run by consolidate), `/admin/dreamstate` (one dreamstate cycle),
  `/admin/seed_recall` (drive a recall end-to-end), `/admin/probe_recipes` (read-only recipe match). Any
  migrated HTTP agent gets these for free — use them to validate it the same way.
- **Unit tests in the libraries.** nmem-act suite is 136 green (incl. 9 GoalPursuit + 5 comms). Add tests
  for new library code; run `PYTHONPATH=src python3 -m pytest -q` in the repo.
- **Shadow comparison** for native-vs-bespoke: distinguish producers by a stored marker (e.g. native A5
  goals carry `source_ref->>'drive'`; bespoke curiosity goals had empty `source_ref`) and compare
  volume/quality before deleting the bespoke one.
- **Behavioral baselines** for capabilities whose value is statistical (e.g. skill-loop verified-yield);
  re-measure after a bake.

### 6.2 codex as the adversarial second reviewer (the dual-review pattern)
- We run **two independent reviews**: a Claude review AND **codex** (OpenAI CLI, ChatGPT-account, model
  pinned to `gpt-6-astra` in `~/.codex/config.toml`). They are reconciled; disagreements are investigated
  against source.
- **codex earns its keep by REPRODUCING findings with runnable scripts**, not just reading. On the
  actuation/pursuit code it surfaced real defects a read-only review missed — e.g. `record_action_outcome`
  does not itself discharge drives (discharge is a separate `discharge_drive` call), a goal-resolution vs
  observation-status mismatch, and nmem-act missing from the install manifest. Output was captured as
  `michelle-ai/tools/review_findings.json` (typed findings: file/line/severity/issue/fix).
- **How to use it on the hive work:** the agency-scoping change is exactly codex's strength — ask it to
  **exhaustively verify that every agency query carries the `owner_agent` filter** (an easy thing for a
  human/one-model to miss a call-site on), and to write a reproduction that two bridges on one DB don't
  cross-actuate. Reconcile with a Claude review; fix; re-verify.

### 6.3 The hive acceptance test (Path B gate)
A hive integration test is the gate before a 2nd agent shares a DB:
- Two `SymbolBridge`es (agents A, B), one DB.
- Assert: A's pursuit **never claims B's goal**; A never sees B's concerns / pending-utterances /
  obligations / episodes; **both read the shared graph** (a node A extracted is visible to B).
- Assert: only the **graph-keeper** runs dreamstate (B's contributor runtime does not fire it).
- Run it under codex reproduction too (adversarial: try to make A see B's agency).

---

## 7. Constraints & regression guards (carry into the other session)

- **Env-flag rules (§3) are hard.** The inline-`#` crash and the `== "1"` silent-disable are the two that
  have actually bitten; guard both.
- **Default-off / additive**, always. It's what keeps DJ-AI + michelle + refinery agents byte-identical on
  a shared library until *their* env opts in.
- **DJ-AI is FROZEN.** It shares the nmem libraries. Do not restart it into new lib code except via a
  deliberate, coordinated lib-update+restart. Everything we've shipped is default-off so it's safe until
  then; keep it that way.
- **Proven-by-extraction** (agent-core rule): graduate a capability into the library only AFTER it ran
  live in a real agent. Don't build hive abstractions speculatively — build them when the 2nd nmem-sym
  agency agent forces the issue.
- **Refinery constraints** (from its CLAUDE.md): never edit `config/refinery.yaml`, `alembic/`,
  `service/models.py`, `db.py`, `backend_pool.py` directly; DB schema changes need explicit approval; use
  `text()` for raw SQL; always `exc_info=True` on error logs. The `owner_agent` migration (§5-Bi) IS a
  schema change → explicit approval + an alembic migration authored deliberately.
- **Deploy discipline:** batch to ONE service restart at the end (repeated restarts break in-flight
  background execution). michelle runs on dj-ai-03 as a `--user` systemd unit on the NAS mount (code edits
  live on restart; nmem/nmem-sym/nmem-act are editable/source-linked in her venv).

---

## 8. Sequencing & session coordination

1. **This session** owns this plan + the agent-core stack context. The **migration session** executes the
   refinery migration and (when triggered) Path B.
2. **Order:** migrate agents to agent-core in **Path A (isolated)** first — pure-thinkers and nmem-only
   agents are safe immediately. An agent that needs nmem-sym **agency** in the shared refinery DB is the
   trigger for **Path B** (there is no isolated nmem-sym-agency in a shared DB without it — §5 caveat).
3. **Path B is a design-doc-first effort** (agency scoping + graph-keeper + `HiveConfig`), gated by the
   hive acceptance test (§6.3) and the `owner_agent` schema-change approval.
4. Keep `capability-activation-sweep.md` (flag status + dep map) and `nmem-agent-core-plan.md` (what's
   graduated) updated as the migration proceeds — they are the live trackers.

---

## 9. Reference index

- **agent-core runtime + graduated pieces:** `nmem/src/nmem/agent_core/` (runtime, memory, backend,
  persona, comms, peer, actuation [experiential sink], goal_store, recall, ops). Charter in
  `agent_core/__init__.py`.
- **A worked reference agent:** `michelle-ai/service/` — `server.py` (boot + ops router mount),
  `cognition.py` (`start_cognition` = the seams), `actuation.py` (executor + `build_runner`),
  `peer.py` (challenge cognition), `identity.py` (`build_persona`).
- **Minimal template:** `nmem/examples/minimal_agent/` (config + persona + ~20-line main + README).
- **Key commits:** GoalPursuit `bd24acd`; agent_core stand-up `852f316`; michelle-on-core `a746f69`;
  Group 1 `0e4edb6`; peer `39527ca`; experiential sink `089aad3`; ops router `a559916`; hive exploration
  `be90a66`.
- **The segregation survey this builds on:** `hive-segregation-exploration.md`.
