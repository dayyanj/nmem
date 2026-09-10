# Host-shell convergence — agents run ON the appliance, not beside it

**Status:** design-only (no code yet). **Owner:** next session (execution). **Written:** 2026-09-11.
**Parent:** [nmem-agent-core-plan.md](./nmem-agent-core-plan.md) (the runtime lift-and-shift — DONE).
**Companions:** [config-alignment-plan.md](./config-alignment-plan.md) (the schema this needs),
[nmem-studio-product-design.md](./nmem-studio-product-design.md) (the appliance),
[agent-comms-channel-agnostic.md](./agent-comms-channel-agnostic.md).

## The gap this closes

The parent plan lifted the **cognitive runtime** into `nmem.agent_core` ("an agent = data + adapters +
config", Groups 1–3 DONE 2026-09-08). Its §1 disposition table then treats the **host shell** —
`server.py` / `db.py` / `config.py` / `model_backend.py` / the `memory.py` bootstrap — as *keep
(bootstrap/adapter)*: the agent's irreducible floor.

That disposition is now wrong. **`agent_core.studio_server.build_agent_app` is already a GENERIC agent
host** — it reads `agent.yaml` + `persona.yaml` from a config dir, builds `AgentRuntime`, assembles the
actor registry, runs the FastAPI lifespan, mounts `make_ops_router`/`/health`/`/chat`, and starts the
mind on uvicorn's loop. michelle's `server.py` (111 LOC) is a **hand-rolled parallel of the same
function**; so are DJ-AI-v2's. Evidence of the duplication (michelle vs DJ-AI-v2):

| Module | michelle LOC | changed-lines vs DJ-AI-v2 | Reading |
|---|---|---|---|
| `model_backend.py` | 17 | docstring only | **byte-identical** re-export of `agent_core.backend` |
| `config.py` | 29 | 6 | ~80% identical yaml loader |
| `memory.py` | 128 | ~48 | same bootstrap skeleton, different **values** |
| `db.py` | 71 | ~59 | same async-engine pattern, different DSN/mounts |
| `server.py` | 173→111 | ~73 | same lifespan skeleton, different routers |

So the host shell is not floor — it's a **third generic layer** the parent plan didn't separate.

## The three tiers (the axis is engine-vs-service, not agent-vs-generic)

`agent_core` is deliberately **I/O-agnostic** (no web server, no process lifecycle; settings read from
env at import). That is a feature — it lets the same mind embed in FastAPI, a CLI, a cron job, a
notebook. So the code that "can't move to `agent_core`-core" mostly isn't *agent-specific* — it's the
**host-service** concern, which is generic on a different axis:

1. **Engine** — `agent_core` / `nmem-act` / `nmem-sym`: the I/O-agnostic mind. *(all logic; DONE)*
2. **Host / appliance** — FastAPI service, lifespan, config load, db/memory/backend bootstrap,
   health/ops, viz. **Already exists: `agent_core.studio_server` + `nmem.api` + `agent_core.ops`,
   gated behind the `api` extra** so `fastapi` never burdens the engine.
3. **Agent instance** — config + `persona.yaml` / `voice.yaml` / `prompts/*.md` + a plugins dir +
   genuinely bespoke seams. Near-zero Python.

**Packaging is already decided** (parent §4b, founder 2026-09-07): the host layer is *a package inside
`nmem` (`nmem.agent_core`), not a new repo* — because it tracks `agent_core`'s API in lockstep; a
separate repo just reintroduces the hand-built-wheel version-skew pain. This doc does **not** propose a
new repo; it proposes **converging michelle (then DJ-AI-v2) onto the appliance that already exists.**

## Per-module verdict (updates parent §1)

| Module | Old disposition | **New disposition** |
|---|---|---|
| `model_backend.py` | → config | **RETIRE** — byte-identical re-export; the appliance provides `agent_core.backend`; no per-agent file |
| `memory.py` bootstrap + `_shim_config` | keep (bootstrap) | **→ appliance.** Mechanism is the appliance's `build_memory`/`build_symbol_graph`; `_shim_config` exists ONLY to bridge michelle's yaml shape → the canonical one, so it **retires** under config-alignment (G4). `remember`/`recall` are 2-line agent_id wrappers → appliance helper |
| `db.py` | keep (adapter) | **→ appliance** (parent item E `build_engine`; the appliance owns engine + `get_session`) |
| `config.py` | keep | **→ appliance** (canonical loader; `AGENT_CONFIG` + env overlay) |
| `server.py` | keep (bootstrap) | **→ appliance** `build_agent_app`; only bespoke endpoints stay as a mounted router |
| `identity.py` residual | data + thin loader | **→ appliance persona/voice load** (`Persona.from_yaml` + `voice.build_system_prompt`, both graduated 2026-09-11); the agent keeps only the yaml/prose |
| `peer.py` | keep (graduated glue) | thin — appliance reads a `peer:`/`comms:` block and wires `agent_core.peer` (G2) |
| `viz_bridge.py` | keep (adapter) | appliance already calls `init_viz(runtime)`; retire the bespoke bridge |
| `cognition.py` / `actuation.py` | wiring stays | mostly appliance assembly + config; the bespoke floor = the sandbox actor decl + `build_proposal` values |
| **The floor (never converges)** | — | config **values**, persona/voice/prose **DATA**, the executor's actual work (sandbox), `build_proposal` specifics, and any truly bespoke HTTP endpoint |

## Gaps blocking michelle from adopting `build_agent_app` today

`studio_server.build_agent_app` boots the *studio* agent; michelle needs five things it doesn't yet do.
Each is a **hook the appliance should expose**, not a fork:

- **G1 — direct-actuator executor.** The appliance's `_build_executor` is **selector-only**
  (`assemble_registry` → `ToolCallingExecutor`). michelle runs the **direct** path: her own
  `ReferenceRunner` with the reflective+experiential sink and the computer-use *research* action, plus a
  **post-start** `register_computer_use_capability` call. The appliance must accept a host-supplied
  `build_executor` (and a `post_start` hook) so a direct-actuator agent plugs in. *(NB: the selector
  path is deferred for computer-use anyway — see the §33 composite-evidence hole in
  goal-planning-design.md — so michelle stays direct regardless.)*
- **G2 — comms sink.** The appliance wires no `comms_sink`; michelle passes `peer.build_comms_sink()`.
  The appliance should read a `peer:`/`comms:` config block and wire `agent_core.peer` when present.
- **G3 — extra routers.** michelle mounts a bespoke `/peer/challenge` (and `/chat`, which the appliance
  already has). The appliance should accept a list of extension routers (or load a `routers/` plugin
  dir), so bespoke endpoints mount without a hand-rolled `server.py`.
- **G4 — config-schema alignment.** michelle's `agent.yaml` uses a FLAT `nmem: {embedding_model,
  llm_base_url, …}`; `build_memory` wants the canonical nested `nmem: {embedding: {provider, model, …},
  llm: {…}, belief, policy}`. `_shim_config` is exactly that impedance-matcher. **This is the crux: once
  the canonical schema lands ([config-alignment-plan.md](./config-alignment-plan.md)), `_shim_config`
  retires and the appliance builds memory/graph straight from config.** The DSN env key is *already*
  aligned — both use `<AGENT>_DB_DSN_ASYNC` (`MICHELLE_DB_DSN_ASYNC`).
- **G5 — bootstrap ownership.** michelle hand-builds mem/graph/backend and passes them into
  `AgentRuntime(mem=, graph=, backend=)`; the appliance has `AgentRuntime` build them from config. After
  G4 michelle drops `init_memory`/`init_symbol_graph`/`init_backend` and lets the runtime bootstrap.

## Canonical config + env (what makes the shims RETIRE, not move)

The shims exist only because config shape/env names aren't canonical yet. Target (per config-alignment):

- **One config schema** the appliance reads directly: `nmem:` (nested `embedding`/`llm`/`belief`/
  `policy`), `symbol_graph:` (domain/edge_types/embed), `actors:`, `peer:`, `pursuit:`/`cognition:`,
  `autonomy:`. michelle's `agent.yaml` migrates to this shape (values unchanged; structure canonical).
- **Env** stays the single source for capability flags (`capabilities.env`, read before Python starts —
  the nmem-sym import-time rule) and the DSN (`<AGENT>_DB_DSN_ASYNC`, already aligned). No new
  `MICHELLE_*` names; per config-alignment's canonical-single-name policy.
- **Persona/voice** already canonical after 2026-09-11: `persona.yaml` (`Persona.from_yaml`) +
  `voice.yaml` (`agent_core.voice.build_system_prompt`) — this advances parent item **G** (prompt
  helpers): the grounded floor is `chat.system_prompt`, the voice layer is `agent_core.voice`, both
  generic; only the manifest + prose are the agent's.

## Sequence (michelle-first, validate-then-thin — same discipline as the parent)

1. **Appliance hooks (G1–G3), additive.** Extend `build_agent_app` into a reusable
   `create_agent_app(config_dir, *, build_executor=None, post_start=None, comms=None, routers=())` —
   defaults reproduce today's studio behaviour (selector executor, no comms). Validate the studio agent +
   the `minimal_agent` example are byte-identical. **Codex the seam** before michelle touches it.
2. **Config-schema alignment (G4/G5).** Migrate michelle's `agent.yaml` to the canonical shape; delete
   `_shim_config`; let the runtime bootstrap memory/graph/backend from config. Validate live: same
   domain/edge_types, data intact (the parent's Group-1A test), 0 tracebacks.
3. **michelle onto the appliance.** Replace `server.py` with `create_agent_app(...)` supplying her
   direct-actuator `build_executor`, her `register_computer_use_capability` `post_start`, her `peer`
   comms, and a small bespoke-endpoints router. **Delete** `server.py` / `db.py` / `config.py` /
   `model_backend.py` / the `memory.py` bootstrap / `identity.py`. michelle = `agent.yaml` +
   `persona.yaml` + `voice.yaml` + `prompts/*.md` + `capabilities.env` + a plugins/routers dir. Validate
   live (health, a pursuit end-to-end, peer, /chat).
4. **DJ-AI-v2 adopts.** Same `create_agent_app`; kills the last host-shell duplicate. (Its founder-KB
   two-engine DB is the one real delta — the appliance's engine helper must allow a second optional
   engine, or DJ keeps a tiny `db.py` for the founder corpus only.)

## The end state

A new nmem agent is a **config directory**: `agent.yaml` + `persona.yaml` + `voice.yaml` +
`prompts/*.md` + `capabilities.env` + optional `actor_plugins/` + optional `routers/`. It runs on
`nmem.agent_core`'s appliance with `python -m nmem.agent_core.studio_server` (or a thin `main.py` calling
`create_agent_app`). No `server.py`, no `db.py`, no `memory.py`, no `model_backend.py`, no `identity.py`.
The **floor** is data + a handful of injected seams (the executor's real work, `build_proposal` values,
bespoke endpoints) — everything else is the library. That is the literal realisation of the parent's
principle: *an agent = data + adapters + config.*
