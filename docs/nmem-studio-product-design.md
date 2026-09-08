# nmem-studio — product design

**Status:** design/vision, 2026-09-08. **Owner:** founder. Written from the same session that built
agent-core; captures the product shape discussed so it's ready to pick up.

**Foundations BUILT (2026-09-08, backend only — no web UI yet):**
- **Capability map + validator** (`agent_core.capabilities`, commit a4e049d): machine-readable
  `flag→requires` map; `AgentRuntime` fail-fasts on a bad combo. The studio's dependency-enforcement layer.
- **Catalog + presets** (805bb05): `catalog()` renders pills from the live pydantic `Field` descriptions
  (anti-drift); `PRESETS` (Memory/Reflective/Full-cognition) = the "basic" view.
- **Config writer** (`agent_core.config_writer`, 0e8385c): `build_agent_files(spec)` →
  correct-by-construction `capabilities.env` (dependency-complete, no inline-#, booleans, defaults
  omitted, implied value-constraints auto-written) + non-secret `agent.yaml` (keys stripped) +
  `persona.yaml` + `secrets` (env-var→value, stored out of band). `Persona.from_dict/to_dict` = persona
  as data. This IS the wizard→config backend + the mechanical env-rule enforcement.
- **Entry-UI mockup**: a working wizard grounded in the real catalog (dependency-enforced pills, presets,
  provider-aware Models step with a server-side "Test connection", live `capabilities.env` preview). Not
  in the repo; published as an inspectable artifact during design.

Remaining is the web layer + the `/studio/test-llm` & `/studio/list-models` backend endpoints + first-boot
image, per §10 phasing. Provider setup + connection-test design captured in §5-bis below.
**Companions:** `nmem-agent-core-plan.md` (the runtime), `nmem-migration-hive-handover.md` (env rules +
hive), `capability-activation-sweep.md` (the flag surface + dependency map).

---

## 1. Vision

**Pull an image, open a webpage, and have a live cognitive agent in ten minutes** — no hand-editing env
files, no PhD in the stack. nmem-studio is the onboarding + management UX on top of the NMEM stack. It
is the thing that converts a genuinely-novel-but-complex research system (6-tier memory, symbol-graph
world model, drives/goals/pursuit, diverse-priors/hive) into something a person can *buy and run*.

**Guiding truth:** the studio is a **config generator + dashboard over machinery that already exists.**
Setup fields → `agent.yaml` + `Persona`; capability toggles → `capabilities.env`; boot → `AgentRuntime`
(`nmem.agent_core`). It introduces no new cognition. Its job is authoring correct config and surfacing a
running agent — nothing more.

**Why the UI matters beyond convenience:** hand-editing `capabilities.env` is where every env footgun
lives (inline-`#` crash, dependency flags, `==1` vs `true` — see the handover §3). A UI that writes the
env *cannot produce a broken one*. The documentation guard becomes a UX guard. That's a robustness win,
not decoration.

## 2. What's in the box (image / compose)

Ship a **docker-compose**, not a single baked image — data must persist across `docker rm`.

| Service | In the box | Notes |
|---|---|---|
| studio + `AgentRuntime` | ✅ | the FastAPI/SPA app + the agent process (or the runtime as a sibling) |
| PostgreSQL + pgvector | ✅ | named volume; first-boot bootstraps tables (agent-core self-provisions) |
| Redis | ✅ | for nmem-exchange (peer/hive); optional for a solo pure-thinker |
| nmem-viz | ✅ | bundled, pointed at the agent's graph |
| embedder (sentence-transformers, all-MiniLM, 384-d) | ✅ **bundled** | removes a config burden + the dim-mismatch trap; offer a "slim + remote embedder" variant to drop the ~torch weight |
| **LLM endpoint** | ❌ **external** | user points at vLLM / Ollama / OpenAI / hosted — the big/variable dep |

First boot: create the agent's DB + bootstrap nmem/nmem-sym tables (already `CREATE TABLE`/migration-driven
in `build_memory`/`build_symbol_graph`), then land on the wizard.

## 3. The four web faces (all over backends that mostly exist)

1. **Setup wizard** — persona + template → LLM/embed endpoints (advanced) → capability preset/pills →
   Create. Writes config, bootstraps, starts the runtime. *(new build)*
2. **Dashboard** — `/health` + the `/admin/*` actions + a memory/graph peek. This is literally
   `nmem.agent_core.ops.make_ops_router` (Group 3F) with a face. *(backend done)*
3. **Chat** — talk to the agent, memory-grounded, with read-only tool-calling. michelle's converse path
   is the reference; graduating it is the **G roadmap item** (prompt assembly + `build_memory_context`).
   The most compelling way to *feel* the cognitive depth. *(needs G)*
4. **nmem-viz** — "watch it think": drives firing, memories consolidating, the graph growing. Already a
   deployed unified viz server (SPA+REST+WS); bundle + point at the graph. **The best demo / the
   differentiator made visible.** *(exists)*

Favourable build-to-value ratio: two of four faces are essentially done, one needs a small graduation (G).

## 4. Config generation (the studio's core job)

### 4.1 Schema-introspection — the anti-rot rule
Do **not** hand-author the toggle list. Generate the pills from the pydantic config schema: `NmemConfig`
and the `NMEM_SYM_*` settings already carry `Field(default=…, description="…")` per flag. The UI renders
each pill's label / default / help text from that, so it **auto-reflects new capabilities** the moment
the libraries add them. Without this, the UI drifts out of sync within a release.

### 4.2 Machine-readable dependency map — the prerequisite
The dependency map (e.g. `RECALL_DRIVE` ⇒ `AUTONOMY__ENABLED`; `PENDING_UTTERANCES` ⇒ `OUTCOME_SURPRISE`;
`DRIVES_CREATE_GOALS`/`GOAL_LLM_ENRICH` ⇒ `CONCERNS_ENABLED`; recall's `RECALL_AGENT_ID` = the agent's id)
currently lives in prose (`capability-activation-sweep.md`). For the UI to *enforce* deps it must become a
small **structured artifact** (`flag → requires[]`), shared by the UI and a runtime validator. Worth doing
regardless: it lets `AgentRuntime` **fail fast on an invalid combo** instead of silently no-opping.
Toggling a pill that has a dep auto-enables (or greys until you enable) the dep.

### 4.3 Basic = presets, Advanced = pills
Basic view is **2–3 presets**, each a dependency-correct flag *bundle*:
- **Memory** — tiers + consolidation (a remembering assistant)
- **Reflective** — + skills, recall, self-engineering
- **Full cognition** — + drives, goals, pursuit, dreamstate, prediction

Advanced exposes the individual pills (schema-generated, dep-enforced). Presets → one-click running;
advanced → there when wanted. **Never** emit an inline `#` on an enabled line; **never** write a toggle as
a `NmemConfig` kwarg (env only) — the UI is the enforcement point for the handover §3 rules.

### 4.4 The env-writing contract (regression-guard)
The studio's env writer MUST obey the handover §3 rules: `true`/`false` (or int for legacy `NMEM_SYM_*`),
comments only on their own line, dependency-complete bundles, default-off for anything untouched. This is
the mechanism that stops the footguns recurring — encode the rules in the writer + a unit test.

## 5. Plugging in actor logic (the hard part, layered)

"Acting" mostly means "calling tools," and nmem-act already has the tool-calling loop
(`ToolCallingExecutor` / `OpenAIToolSelector` / `ToolInfo`). So the boundary is not "users must write an
executor" — it's three tiers, and the UI covers the first two:

1. **No-code — tools as webhooks / MCP (the common case).** A "tool" = name + description + JSON-schema +
   endpoint. Ship a built-in **WebhookToolExecutor**: the agent's LLM function-calls, each call POSTs to a
   user-configured URL. Tools are defined *in the wizard* — no Python. Add an **MCP-client executor** so
   any MCP tool-server becomes the agent's hands by config. This covers most "give my agent the ability to
   X" needs with zero code.
2. **Low-code — plugin mount (custom actuators).** The image watches a mounted dir
   (`/agent/plugins/executors/`) for a Python module implementing nmem-act's `ActionExecutor`; the runtime
   discovers it and the UI lists it as an attachable executor. For the genuinely bespoke (a specific SDK, a
   robot, a browser sandbox like michelle's). Image stays intact; the user extends via a volume.
3. **`build_proposal` default.** agent-core ships a sensible default proposal-builder (recall + prior
   findings + skills), so most actors never think about it; custom only when needed.

Product framing: **"tool-calling actors are no-code; custom actuators use a documented plugin mount."**
This is built on machinery we already have and turns "the UI can't do actors" into "the UI does most
actors end-to-end."

## 5-bis. Model providers + connection test (setup & validate from the UI)

The Models step is provider-aware: a dropdown of presets (Local vLLM/Ollama, OpenAI, Anthropic/Claude,
Moonshot/Kimi, OpenRouter, DeepSeek, Groq, Together, Custom) pre-fills `base_url` + dialect and reveals a
key field only when the provider needs one. A provider preset is data: `{label, base_url, dialect,
key_env, sample_models}`.

**The connection test is SERVER-SIDE, and the key never touches the browser.** The browser can't call
`api.openai.com` / `api.anthropic.com` directly (CORS), and mustn't hold the key. So:
- The wizard POSTs `{provider, base_url, model, key}` to the studio backend (`POST /studio/test-llm`).
- The backend **stores the key as a secret** (its secret store / env, referenced from `agent.yaml` only by
  var name — never the literal), then runs the test **itself**: constructs `agent_core.backend`
  (`OpenAICompatibleBackend` / `AnthropicBackend`, the very client the agent will use, honouring the
  dialect) and makes a one-token chat call. Returns `{ok, model, latency_ms, error}` — never the key back.
- `POST /studio/list-models` (server-side `GET {base}/models`) powers the "fetch available" model picker.
- Testing the real chat path (not just `/models`) validates dialect quirks (Claude `thinking`, Qwen
  `reasoning_effort`), so green means "the agent can actually talk to it," not "the host pinged."

**Consumer subscriptions (ChatGPT Plus / Claude Max / etc.) — PARKED (deliberate, not an oversight).** A
subscription backs the chat *app*, not a programmatic chat endpoint; the only sanctioned programmatic path
is the coding-agent CLIs (Codex on Plus, Claude Code on Max), which are agentic harnesses (inject their own
prompt/tools) and are rate-limited for interactive use — a poor fit for an always-on agent's continuous
cognition, plus ToS greyness for headless 24/7 use. The cheap path for a constantly-thinking agent is
**local models** (zero marginal cost, no limits), already the studio default. A `CLIBackend` that shells
out to `claude -p` / `codex exec` behind the `chat()` interface is feasible as a *personal/local* adapter
only, never a product feature.

## 6. Starter personas / templates
Ship 2–3 editable templates (the blank persona is the real friction, not the toggles):
- **Researcher** — learns a domain from primary sources; verifies over confabulating.
- **Diverse-prior critic peer** (michelle-style) — challenges reasoning, offers a genuinely different view.
- **Support responder** — answers grounded in a knowledge base, escalates honestly.
Each pre-fills objectives, world-seed topics, baseline KB, and capabilities — all user-editable.

## 7. Prerequisites (what must exist before/with the studio)
- **Machine-readable dependency map** (§4.2) + a runtime combo-validator. *(small, do first)*
- **Config-schema introspection** endpoint (pills + descriptions + defaults from the settings classes).
- **G graduation** — chat/prompt-assembly (`build_memory_context` + persona system-prompt) into agent-core.
- **WebhookToolExecutor** + an **MCP-client executor** (thin wrappers over nmem-act's tool-calling).
- **Executor plugin-discovery** in `AgentRuntime` (scan a mount, register, expose to the UI).
- **nmem-viz bundling** + a point-at-this-graph config.
- **Compose + first-boot bootstrap** (pg/pgvector/redis/embedder + table provisioning).

## 8. Security
Localhost-single-user is fine to start (light auth). Anything exposed needs real auth. The studio writes
env, configures LLM endpoints (**API keys → secrets, never logged**), and can trigger `/admin` actions
(consolidate/dreamstate/nightly) — so gate `/admin` behind auth once it's not localhost. Decide the
deployment posture (local tool vs exposed service) before shipping.

## 9. Commercial / licensing model

**This is a product, and the model is open-engine + commercial-studio + hosted** (classic open-core;
consistent with Spwig's AGPL ethos).

- **What's sellable is the experience + service, not the image** (anyone can rebuild it from the open
  repos). The moat is the studio UX, the packaging/upgrade path, support, hosted, and — critically — the
  cognitive-depth *differentiator made accessible*. The depth is commercially worthless if it takes deep
  expertise to stand up; the studio is what monetizes it.
- **Recommended split:** keep the **libraries AGPL** (community, adoption, research credibility); make the
  **studio** (wizard/dashboard/chat/viz-integration/fleet + hive management) a **separately-licensed
  proprietary layer**. Offer a **hosted** version (per-agent/month, mirroring Spwig's €35/mo) — usually the
  stronger revenue than selling images.
- **⚠️ The one legal thing to nail FIRST — before architecting the studio↔engine boundary:** AGPL's
  "network use = distribution" clause can reach code that *links* the AGPL libraries in-process. Two clean
  options, and since **it's the founder's copyright**, the second is straightforward:
  (a) keep a clean process/API boundary (studio talks to the runtime over HTTP, doesn't link AGPL code
  in-proc), or
  (b) **dual-license** — AGPL for the community + a commercial license for the studio/image and for
  enterprises who don't want copyleft (MongoDB/Qt/GitLab pattern). Owning the copyright makes (b) clean.
  **Decide this before the build**, because it constrains the architecture.
- **Honest cost:** commercializing is an ongoing commitment — versioned images, an upgrade/migration path
  (the hive/segregation work becomes customer-facing), docs, a support surface. Different bar than an
  internal accelerator. Decide *intent* deliberately: a business, or an accelerator you might open up. The
  build is similar; the polish + support cost diverge sharply.

## 10. Phasing
0. **Foundations** — machine-readable dep-map + combo-validator; config-schema introspection endpoint.
1. **Image** — compose (pg/pgvector/redis/embedder) + first-boot bootstrap; runs a pure-thinker from a
   hand-written config (no UI yet). Proves the packaging.
2. **Wizard (thinker)** — persona/template + endpoints + preset/pills → writes config → boots. End-to-end
   for a pure-thinker.
3. **Dashboard** — mount the ops router with a face.
4. **Chat** — graduate G; add the chat page.
5. **Viz** — bundle nmem-viz.
6. **Actors** — WebhookToolExecutor + MCP executor (no-code tools) + the plugin-mount for custom.
7. **Hive** — the Path B work (segregation handover) surfaced as an "advanced: shared world / join a
   hive" flow. Last, and gated on the `owner_agent`/graph-keeper work.

## 11. Open decisions (for the founder)
- **Intent:** commercial product vs internal accelerator (sets the polish/support bar).
- **License split + copyright/dual-license** (do first — §9).
- **Studio↔engine boundary:** in-process vs HTTP API (follows the license decision).
- **Solo vs hive positioning:** is the headline "your own private agent" or "a hive"? (Affects the wizard's
  default and the marketing.)
- **Image distribution:** self-host image only, hosted only, or both.

## 13. RESUME — next steps to a running studio (post-compact start here)

**Where we are:** Steps 1–3 are **DONE + validated live** (michelle's venv on dj-ai, real Postgres +
real Gemma). The studio is a runnable single-agent appliance:
- `agent_core.studio` — the `/studio/*` router (`catalog`/`test-llm`/`list-models`/`create`),
  `create_studio_app()` (router + SPA at `/`), `studio_index_html()`. Commit `a348a88`.
- `src/nmem/agent_core/studio_ui/index.html` — the productionised wizard SPA, generated from the
  design mockup by `docs/mockups/build_studio_spa.py` (mockup = SoT for markup/style; generator swaps
  its simulated `<script>` for live `/studio/*` wiring). Commit `ce1f087`.
- `agent_core.studio_server` — the **single-agent appliance** (wizard mode ↔ agent mode; create→boot
  via container restart; agent-dir discovery; secrets.env at 0600; `provision_db`). `docker/studio/`
  = Dockerfile + compose (studio + pgvector + redis, external LLM) + entrypoint.sh + Dockerfile.dockerignore.
  Commit `6b907ab`. **Image build:** kick `docker compose build` from `docker/studio/` (long: CPU torch +
  bundled all-MiniLM). Python path is proven; the build only validates layering.
- Config-authoring backend (`capabilities` map/validator/`catalog()`/`PRESETS`, `config_writer`) as before,
  now also emitting `backends.brain` + `api_key_env` key resolution (the two gaps the boot exposed).
- **Topology DECIDED (founder, 2026-09-08):** single-agent appliance (one image = studio + the agent).
  Multi-agent = several appliances or the later control-plane topology.

**Build order — resume at Step 4:**

1. ~~`agent_core/studio.py` — the `/studio/*` router~~ **DONE** (`a348a88`).
2. ~~Productionise the wizard SPA~~ **DONE** (`ce1f087`).
3. ~~First-boot + compose image (single-agent appliance)~~ **DONE** (`6b907ab`); image-build layering is
   the only unverified bit.
4. **Dashboard page** = give agent mode a face over `make_ops_router` (already mounted): health + `/admin/*`
   as a small UI, not raw JSON. (`studio_server.build_agent_app` currently serves a placeholder landing.)
5. **Chat page** needs the **G graduation** (`build_memory_context` + persona system prompt into agent_core;
   michelle's converse is the reference). **Viz**: bundle nmem-viz, point at the agent's graph.
6. **Actors** (later): `WebhookToolExecutor` + MCP executor over nmem-act tool-calling (no-code tools) +
   the plugin-mount for custom executors (§5).
7. **Hive** (last): Path B from `nmem-migration-hive-handover.md`, as an "advanced: shared world" flow.

**Still open (founder call, does NOT block Steps 4–5):** the license split / studio↔engine boundary
(§9, §11). The appliance is in-process-with-HTTP, which works under either license model; only the
*packaging/licensing* of the SPA + `/studio/*` + appliance as the proprietary layer depends on this call.

**Guardrails carried in:** env-flag rules are now enforced by `config_writer` (don't bypass it — write env
only through it). Default-off/additive. DJ-AI FROZEN. The nmem/nmem-act/nmem-sym repos also have an
in-flight **1.0.0 release-prep from another session** (CHANGELOG/README/pyproject/version) — NOT ours;
never `git add -A`, always add specific files.

## 12. Reference index
- Runtime + seams: `nmem/src/nmem/agent_core/` (runtime, ops [dashboard backend], **capabilities**,
  **config_writer**, comms, peer, actuation, persona, memory, backend, goal_store, recall). Example:
  `nmem/examples/minimal_agent/`. Entry-UI mockup: `nmem/docs/mockups/studio-wizard.html`.
- Tool-calling machinery (for the actor tiers): `nmem-act` (`ToolCallingExecutor`, `OpenAIToolSelector`,
  `ToolInfo`, `ToolStep`, `Done`).
- Reference agent (chat/converse, executor pattern): `michelle-ai/service/` (`cognition.start_cognition`,
  `actuation`, `peer`, `identity.build_persona`).
- Flag surface + dep map: `capability-activation-sweep.md`. Env rules + hive: `nmem-migration-hive-handover.md`.
