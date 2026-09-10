# Host-shell convergence — agents run ON the appliance, not beside it

**Status:** design-only (no code yet). **Owner:** next session (execution). **Written:** 2026-09-11.
**Reviewed:** codex round-1 (2026-09-11) — direction sound; corrected the "appliance is already a reusable host"
over-claim + added missing gaps (see [§ Reality check](#reality-check-codex-round-1)). **Revise before executing.**
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

## Reality check (codex round-1)

The direction is right, but be precise about the starting point: **`build_agent_app` PROVES a generic host is
feasible; it is not yet that host.** It is a *Studio appliance* implementation, coupled to things michelle/DJ must
NOT inherit:
- **Config discovery is wizard-shaped:** it ignores `AGENT_CONFIG` and scans `$STUDIO_DATA_DIR/*/capabilities.env`
  for the first agent dir (`studio_server.py:31-45,225-232`). michelle selects `config/agent.yaml` via
  `AGENT_CONFIG`; DJ via `DJAI_CONFIG`. So `create_agent_app(config_dir, …)` is a real **extraction**, not a rename.
- **Appliance lifecycle is studio-only:** wizard provisioning of a fixed `NMEM_AGENT_DB`, per-agent `secrets.env`,
  and a SIGTERM-for-container-restart (`studio_server.py:98-185`) must stay in the *wizard* layer; the reusable
  host is **agent-mode only**.
- **Studio policy/UI is not generic:** `SessionAuth`, the dashboard, `/tools`, `/act`, and a *minimal* `/chat`
  (message+history only) are Studio choices. michelle's `/chat` carries `session_id`/continuity/token-overrides/
  metacog fields — an extension router **cannot** replace an already-registered same-path route, so the host needs
  **configurable/overridable routes + a pluggable chat handler**, not just `routers=()`.
- **Import-time ordering is NOT solved:** `nmem_sym.config.settings` binds at import (`nmem-sym config.py:52-59`).
  `create_agent_app` **cannot** load `capabilities.env` itself after imports — it must *require* the service
  manager/entrypoint sources secrets + capabilities **before any nmem import** (michelle's systemd `EnvironmentFile`
  already does this; keep it).

So restate the target: **extract a reusable `create_agent_app` FROM `build_agent_app`**, separating the generic
agent-mode host from the studio wizard/appliance lifecycle. The appliance then becomes *one caller* of it.

**And the honest end state** (codex): *config + data + explicit host ADAPTERS/factories* — **not** literally
"no Python". Structural YAML shims retire and agent-specific **values migrate into config**, but adapter LOGIC
stays as declared plugins/factories: the sandbox executor's real work, the peer **on_challenge** cognition
callback, the voice/chat prompt policy, DJ's founder-KB context, and any bespoke route.

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

`AgentRuntime` already accepts the seams (`build_executor`, `build_proposal`, `comms_sink`, `skill_chronic`, and
optional prebuilt `backend`/`mem`/`graph` — `runtime.py:95-125,193-211`); the gaps are in the **host layer** that
must wire them from a config dir. These are **structured lifecycle seams**, not a single `post_start` — the host
needs *resources-constructed → runtime-started → shutdown-ordering* callbacks, because michelle's peer must be built
with live memory + started **before** runtime construction and closed on shutdown, and her executor needs the
runtime's mem/backend/identity + sandbox config + reflection/skill hooks:

- **G1 — direct-actuator executor.** The appliance's `_build_executor` is **selector-only**
  (`assemble_registry` → `ToolCallingExecutor`). michelle runs the **direct** path: her own
  `ReferenceRunner` with the reflective+experiential sink and the computer-use *research* action, plus a
  **post-start** `register_computer_use_capability` call. The appliance must accept a host-supplied
  `build_executor` (and a `post_start` hook) so a direct-actuator agent plugs in. *(NB: the selector
  path is deferred for computer-use anyway — see the §33 composite-evidence hole in
  goal-planning-design.md — so michelle stays direct regardless.)*
- **G2 — comms as a FACTORY, not a config block.** michelle passes `peer.build_comms_sink()`, and her peer is
  constructed with live memory + an **agent-specific `on_challenge` cognition callback**, started before runtime
  construction, closed on shutdown (`michelle peer.py:35-45,65-68`). A declarative `peer:` block alone can't supply
  the callback — the host takes a **comms factory** given the runtime context + lifecycle ordering.
- **G3 — routes: override/disable, not just append.** michelle's `/chat` (session_id/continuity/token/metacog) is
  richer than the host's default; a router can't shadow a same-path route. The host needs a **pluggable chat handler
  + route enable/disable**, plus mount points for bespoke endpoints (`/peer/challenge`).
- **G4 — config-schema alignment (structural shim retires; VALUES migrate).** michelle's `agent.yaml` is a FLAT
  `nmem: {embedding_model, llm_base_url, …}`; `build_memory` wants nested `nmem: {embedding, llm, belief, policy}`
  + `build_symbol_graph` wants `domain`/`edge_types`. `_shim_config` also **injects `db.env_key`** (absent from the
  checked-in yaml) and encodes agent-specific VALUES — `agent_trust.michelle=0.9`, `policy.writers={system,michelle}`,
  the embedding-provider default. Under the canonical schema ([config-alignment-plan.md](./config-alignment-plan.md))
  the **shape-mapping retires**, but those **values move into `agent.yaml`** (they don't disappear). NB the wizard's
  `config_writer.build_agent_files` doesn't yet pass `belief`/`policy` to `render_agent_yaml` (`config_writer.py:81-
  136,192-203`) — a prerequisite fix. The DSN env NAME is aligned (`<AGENT>_DB_DSN_ASYNC`); the config SCHEMA is not.
- **G5 — bootstrap ownership.** michelle hand-builds mem/graph/backend and passes them into
  `AgentRuntime(mem=, graph=, backend=)`; `AgentRuntime` already builds omitted ones from config at `start()`
  (`runtime.py:193-211`). After G4 michelle drops `init_memory`/`init_symbol_graph`/`init_backend`.
- **G6 — voice-aware converse.** The Studio path calls `runtime.converse`, which uses the generic grounded prompt
  (`chat.system_prompt`), NOT `voice.yaml`/prose (`chat.py:45-60`). michelle's `/chat` + peer replies depend on her
  `build_system_prompt` (`agent_core.voice`). So "persona/voice canonical" holds for the *components* but the host
  doesn't yet *wire* voice into converse — it must accept an agent's system-prompt builder.
- **G7 — generalize `build_backend`'s env overrides.** `agent_core.backend.build_backend` hard-codes `MICHELLE_LLM_*`
  override vars (`backend.py:264-286`) — it contradicts the "no new `MICHELLE_*` names" goal and blocks DJ/general
  deployment overrides. Generalize to agent-neutral names BEFORE deleting the host backend config. (Ironic: the
  already-"graduated" backend still carries michelle's env names.)

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

0. **Prerequisites (library, before any host extraction).** (a) A **`host` extra** — `fastapi`, `uvicorn`,
   `PyYAML` (the `api` extra omits PyYAML though `studio_server`/persona/config-writer import it), plus `httpx`
   for the computer-use actor. (b) **Generalize `build_backend`** env overrides off `MICHELLE_LLM_*` (G7). (c)
   Teach `config_writer` to carry `belief`/`policy`/`db.env_key` (G4). (d) Make `SessionAuth`, the dashboard,
   `/tools`, `/act` **optional host features** (they're Studio policy, not generic) so michelle/DJ don't inherit
   studio auth/UI/exposure.
1. **Extract `create_agent_app` FROM `build_agent_app` (additive; the appliance becomes one caller).** Separate
   the generic **agent-mode** host from the studio wizard/provision/restart lifecycle. Signature is a **structured
   lifecycle**, not four kwargs:
   `create_agent_app(config_dir, *, build_executor=None, comms_factory=None, on_resources_ready=None,`
   `on_started=None, on_shutdown=None, chat_handler=None, routers=(), enable=(...), system_prompt=None)`.
   It must NOT scan `$STUDIO_DATA_DIR`; it reads `config_dir` (from `AGENT_CONFIG`/`DJAI_CONFIG`), and REQUIRES the
   entrypoint sourced `capabilities.env` before import. Defaults reproduce studio behaviour; validate the studio
   agent + `minimal_agent` example are byte-identical. **Codex the seam** before michelle touches it.
2. **Config-schema alignment (G4/G5).** Migrate michelle's `agent.yaml` to the canonical nested shape, moving the
   `_shim_config` VALUES (belief/policy/embedding-provider/`db.env_key`) into the file; delete the shim; let the
   runtime bootstrap memory/graph/backend from config. Validate live: same domain/edge_types, data intact (the
   parent's Group-1A test), 0 tracebacks.
3. **michelle onto `create_agent_app`.** Supply her direct-actuator `build_executor`, a `post_start`/`on_started`
   that runs `register_computer_use_capability`, her `comms_factory` (peer + `on_challenge`), her `system_prompt`
   builder (voice, G6), a `chat_handler` preserving her `/chat` fields, and a `/peer/challenge` router. **Then
   delete** `server.py`/`db.py`/`config.py`/`model_backend.py`/`memory.py`-bootstrap/`identity.py` — **but first
   migrate the ops tools** that import them (`tools/test_identity.py`, `tools/test_peer.py`, …) to a replacement
   access path. Validate live (health, a pursuit end-to-end, peer, /chat).
4. **DJ-AI-v2 adopts.** Same `create_agent_app`. DJ has **several** legit retained adapters, not one: a two-engine
   founder DB, a custom `/api/converse` contract, its own `skill_chronic`, founder-KB context in `build_proposal`,
   a relative plugin dir, and a special executor setup. These stay as DJ's declared factories/plugins; only the
   generic host shell deletes.

## The end state

A new nmem agent is a **config directory + a thin `main.py`**: `agent.yaml` + `persona.yaml` + `voice.yaml` +
`prompts/*.md` + `capabilities.env` + optional `actor_plugins/`/`routers/`, plus a small `main.py` that calls
`create_agent_app(config_dir, …)` and injects the agent's **factories** (executor, comms/on_challenge,
system-prompt/voice, chat handler, bespoke routes). No `server.py`, `db.py`, `memory.py`, `model_backend.py`, or
`identity.py`. The honest **floor** (codex): *config + data + explicit host adapters* — structural YAML shims
retire and agent-specific **values migrate into config**, but the **adapter logic stays as declared factories**:
the executor's real work (sandbox), the peer `on_challenge` cognition, the voice/chat prompt policy, DJ's
founder-KB context, and any bespoke route. That is the realistic realisation of the parent's principle —
*an agent = data + adapters + config* — where "adapters" is a handful of injected factories, not a hand-rolled
host shell.
