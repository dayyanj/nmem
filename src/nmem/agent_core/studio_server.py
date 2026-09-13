"""The nmem-studio **single-agent appliance** — one image, one process, two modes.

    First boot   → WIZARD mode: serve the setup wizard at ``/``. The user picks a persona,
                   tests their LLM, chooses capabilities, and clicks Create. That writes the
                   agent's config to the data volume, provisions its database, and asks the
                   container to restart.
    Every boot after the agent exists → AGENT mode: the agent runs. Its capability flags and
                   DB DSN are already in the process env (the entrypoint sourced them BEFORE
                   Python started — mandatory, because nmem/nmem-sym read their settings from
                   env at import time), so AgentRuntime boots with the right mind.

Why a restart at the create→boot seam instead of booting in-process: capability flags and the
DSN are read from ``os.environ`` when the settings singletons are first imported. A process that
already served the wizard has imported them with an empty env; mutating env afterwards would NOT
take effect (the classic ``_env_on`` silent-disable footgun). A clean restart — the entrypoint
re-evaluates mode and sources the env first — is the robust appliance implementation. One image,
one agent, and after the restart genuinely one process running that agent.

The entrypoint (docker/studio/entrypoint.sh) chooses the mode by whether the agent's
``capabilities.env`` exists in the data dir, then execs ``python -m nmem.agent_core.studio_server``.
"""
from __future__ import annotations

import logging
import os
import re
import signal

log = logging.getLogger("nmem.studio")

DATA_DIR = os.environ.get("STUDIO_DATA_DIR", "/data")


def find_agent_dir() -> str | None:
    """The single agent's config dir under the data volume — the wizard writes it as
    ``DATA_DIR/<agent_id>`` (the router's per-agent layout), and this appliance runs whichever
    one exists. Discovery (not a fixed path) keeps the agent's id as its own, and matches the
    same scan the entrypoint does to source the env. None until an agent is created."""
    if not os.path.isdir(DATA_DIR):
        return None
    for name in sorted(os.listdir(DATA_DIR)):
        d = os.path.join(DATA_DIR, name)
        if os.path.isfile(os.path.join(d, "capabilities.env")):
            return d
    return None


def agent_exists() -> bool:
    """The appliance has an agent once a capabilities.env is on the data volume."""
    return find_agent_dir() is not None


# ── secret store + DB DSN wiring (wizard mode) ──────────────────────────────────
_ENV_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _merge_env_file(path: str, kv: dict) -> None:
    """Merge KEY=VALUE pairs into an env file (create/replace keys, keep the rest).

    ``entrypoint.sh`` SOURCES this file, so values are written **shell-quoted** (``shlex.quote``)
    and read back **unquoted** (``shlex.split``). Writing them raw would let a secret containing
    shell metacharacters — e.g. an API key ``$(rm -rf /)`` typed into the wizard — execute on
    boot, or silently corrupt any key with spaces/quotes. Env-var names are validated too."""
    import shlex

    existing: dict = {}
    if os.path.exists(path):
        for line in open(path):
            line = line.rstrip("\n")
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            try:
                parts = shlex.split(v)                 # undo the shell-quoting we wrote
                existing[k.strip()] = parts[0] if parts else ""
            except ValueError:
                existing[k.strip()] = v.strip()
    existing.update(kv)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        fh.write("# secrets + DSN for this agent — sourced by entrypoint.sh, never committed\n")
        for k, v in existing.items():
            if not _ENV_NAME_RE.match(k):
                log.warning("[studio] skipping invalid env-var name %r in secrets", k)
                continue
            fh.write(f"{k}={shlex.quote(str(v))}\n")    # shell-safe: source can't execute it
    os.chmod(path, 0o600)


def store_secrets(secrets: dict) -> None:
    """The studio router hands LLM/embed keys here (AFTER write_agent, so the agent dir already
    exists); the appliance keeps them in that dir's secrets.env (0600), out of agent.yaml."""
    d = find_agent_dir()
    if secrets and d:
        _merge_env_file(os.path.join(d, "secrets.env"), secrets)


def _agent_db_name() -> str:
    """The appliance runs ONE agent, so its database has a FIXED name (``NMEM_AGENT_DB``,
    default ``agent_nmem``) — independent of the agent's chosen id. That lets the bundled
    nmem-viz service know the DB URL at compose time (the id is only picked in the wizard)."""
    return os.environ.get("NMEM_AGENT_DB", "agent_nmem")


def _agent_dsn(agent_id: str) -> str:
    """The async DSN for this agent's own database, from the appliance's Postgres env
    (POSTGRES_HOST/PORT/USER/PASSWORD) + the fixed appliance DB name. This is what build_memory
    reads via the ``<AGENT>_DB_DSN_ASYNC`` env key that render_agent_yaml wrote into agent.yaml."""
    host = os.environ.get("POSTGRES_HOST", "postgres")
    port = os.environ.get("POSTGRES_PORT", "5432")
    user = os.environ.get("POSTGRES_USER", "nmem")
    pw = os.environ.get("POSTGRES_PASSWORD", "nmem")
    return f"postgresql+asyncpg://{user}:{pw}@{host}:{port}/{_agent_db_name()}"


async def provision_db(agent_id: str) -> str:
    """CREATE the appliance DB if absent (tables self-provision on first agent boot via
    build_memory/build_symbol_graph). Idempotent AND concurrency-safe: in a hive, N members share
    ONE ``NMEM_AGENT_DB`` and may provision it simultaneously — the check-then-CREATE is racy, so a
    concurrent ``CREATE DATABASE`` losing to another member (DuplicateDatabaseError) is success, not
    failure. Returns the DB name."""
    import asyncpg

    host = os.environ.get("POSTGRES_HOST", "postgres")
    port = int(os.environ.get("POSTGRES_PORT", "5432"))
    user = os.environ.get("POSTGRES_USER", "nmem")
    pw = os.environ.get("POSTGRES_PASSWORD", "nmem")
    db = _agent_db_name()
    conn = await asyncpg.connect(host=host, port=port, user=user, password=pw, database="postgres")
    try:
        exists = await conn.fetchval("SELECT 1 FROM pg_database WHERE datname = $1", db)
        if not exists:
            try:
                await conn.execute(f'CREATE DATABASE "{db}"')      # cannot run in a txn; asyncpg autocommits
                log.info("[studio] provisioned database %s", db)
            except Exception:  # noqa: BLE001
                # A concurrent hive member may have created it first — Postgres surfaces this race as
                # DuplicateDatabaseError OR a UniqueViolation on pg_database_datname_index (or another
                # transient). Whatever it was, if the DB now EXISTS the create succeeded (just not by us);
                # only a create that left the DB absent is a real failure.
                if await conn.fetchval("SELECT 1 FROM pg_database WHERE datname = $1", db):
                    log.info("[studio] database %s already created by a concurrent hive member", db)
                else:
                    raise
    finally:
        await conn.close()
    # pgvector: the extension is created per-DB by the nmem bootstrap; ensure it here too.
    conn2 = await asyncpg.connect(host=host, port=port, user=user, password=pw, database=db)
    try:
        await conn2.execute("CREATE EXTENSION IF NOT EXISTS vector")
    except Exception as e:  # noqa: BLE001 — non-fatal; nmem bootstrap will retry
        log.warning("[studio] could not create pgvector extension in %s: %s", db, e)
    finally:
        await conn2.close()
    return db


def _request_restart() -> None:
    """Ask the container to restart so it re-enters AGENT mode with the freshly-written env
    sourced. compose runs with restart: unless-stopped, so exiting brings the agent online."""
    log.info("[studio] agent created — restarting the appliance into agent mode")
    os.kill(os.getpid(), signal.SIGTERM)


async def _start_agent(spec: dict, agent_dir: str) -> None:
    """The studio router's start_agent hook (called AFTER write_agent). In the appliance we do
    NOT boot in-process (stale settings singletons); we provision the DB, wire the DSN into
    secrets.env, and restart into agent mode. The restart is deferred so the HTTP response for
    Create flushes to the browser first."""
    import asyncio
    import shutil

    agent_id = spec["agent_id"]
    # Provision BEFORE committing to agent mode. write_agent already wrote the config that the
    # entrypoint keys agent mode on, so if provisioning fails we must REMOVE it — else the
    # restart would boot a broken agent (no DB) and restart-loop. Fail back to the wizard instead.
    try:
        db = await provision_db(agent_id)
    except Exception:
        shutil.rmtree(agent_dir, ignore_errors=True)
        raise
    _merge_env_file(os.path.join(agent_dir, "secrets.env"),
                    {f"{agent_id.upper()}_DB_DSN_ASYNC": _agent_dsn(agent_id)})
    log.info("[studio] staged agent %s (db=%s); scheduling restart", agent_id, db)
    asyncio.get_event_loop().call_later(1.5, _request_restart)


# ── the two apps ────────────────────────────────────────────────────────────────
# Research-mode dispatch name: the sandbox Action, the capability descriptor, /act, AND the
# runtime's auto-default proposal builder must ALL dispatch on the same action_type, else an
# autonomous pursuit emits an action the ReferenceRunner never registered. Pin this when a
# computer_use block is present but leaves action_type unset (codex Step-3 P1).
_RESEARCH_ACTION_DEFAULT = "pursue_knowledge"


def _env_on(name: str, default: str = "false") -> bool:
    """Read a boolean capabilities.env flag (the entrypoint sourced it into os.environ)."""
    return os.environ.get(name, default).strip().lower() in ("1", "true", "yes", "on")


def _build_gate(cfg: dict | None):
    """Build the actor autonomy gate from the agent's ``autonomy`` config.
    ``{level: read_only|tiered|full, allow: [...], deny: [...]}``. Defaults to read_only —
    safe by construction (only read-only tools run until the operator raises it)."""
    from nmem_act.autonomy import AutonomyGate, AutonomyLevel
    cfg = cfg or {}
    try:
        level = AutonomyLevel((cfg.get("level") or "read_only").lower())
    except ValueError:
        level = AutonomyLevel.READ_ONLY
    return AutonomyGate(level=level, allow=set(cfg.get("allow") or []), deny=set(cfg.get("deny") or []))


def build_wizard_app():
    """WIZARD mode: the setup wizard + /studio/* router, wired to the appliance's secret store,
    DB provisioner, and restart-into-agent-mode."""
    from nmem.agent_core.studio import create_studio_app
    return create_studio_app(config_dir=DATA_DIR, store_secrets=store_secrets, start_agent=_start_agent)


def build_agent_app():
    """AGENT mode: the app for the one agent on the data volume. Now a thin caller of the reusable
    ``agent_core.host.create_agent_app`` (host-shell-convergence-plan.md Step 1) — the generic host
    owns the lifespan/bootstrap/ops + the default /chat; this function layers only the STUDIO-specific
    features (the executor, dashboard, ``/tools``, ``/act``, SessionAuth). The executor has two
    config-selected MODES (mutually exclusive, §33.3): a top-level ``computer_use:`` block ⇒ the
    DIRECT verifier-enforced research runner (``build_research_runner``); an ``actors:`` block ⇒ the
    gated selector (``ToolCallingExecutor``); neither ⇒ a pure thinker. Returns (app, ctx); the
    runtime is built in the lifespan (``ctx.runtime`` is None until startup), started on uvicorn's
    loop (asyncpg binds to the serving loop)."""
    import yaml
    from fastapi.responses import HTMLResponse

    from nmem.agent_core.host import create_agent_app
    from nmem.agent_core.persona import Persona
    from nmem.agent_core.studio import agent_dashboard_html

    agent_dir = find_agent_dir()
    if agent_dir is None:
        raise RuntimeError("agent mode requested but no agent config found under the data dir")
    persona_yaml = os.path.join(agent_dir, "persona.yaml")
    with open(os.path.join(agent_dir, "agent.yaml")) as f:
        config = yaml.safe_load(f)
    persona = Persona.from_dict(yaml.safe_load(open(persona_yaml))) if os.path.exists(persona_yaml) \
        else Persona(agent_id=config.get("db", {}).get("config_key", "agent"))

    # Two executor MODES, chosen by config (mutually exclusive — §33.3). A top-level `computer_use:`
    # block ⇒ the DIRECT verifier-enforced research runner (an LLM selector's Done(True) must not be
    # able to override the sandbox Verifier); otherwise an `actors:` block ⇒ the gated selector
    # (ToolCallingExecutor). Neither ⇒ a pure thinker. computer_use is deliberately NOT an
    # assemble_registry actor, so it is never selector-visible.
    # Presence, not truthiness: an explicit `computer_use:` key (even `{}` or null → SandboxClient
    # defaults it disabled) declares a research agent, so it must still supersede a stale capability
    # row and suppress the selector. `cu_cfg` tolerates a null value.
    cu_present = "computer_use" in config
    cu_cfg = config.get("computer_use") or {}
    if cu_present:
        # P1: pin the dispatch name so the runner, the capability descriptor, /act, AND the runtime's
        # auto-default proposal builder (AgentRuntime._default_proposal reads pursuit.action_type)
        # all agree — otherwise an omitted action_type defaults to pursue_knowledge for the runner but
        # llm_tool_call for the proposal, and autonomous pursuits dispatch an unregistered action.
        # `or {}` normalizes an empty `pursuit:` YAML section (safe_load → None) before setdefault.
        pcfg = config.get("pursuit") or {}
        pcfg.setdefault("action_type", _RESEARCH_ACTION_DEFAULT)
        config["pursuit"] = pcfg

    _cu_action = (config.get("pursuit") or {}).get("action_type", _RESEARCH_ACTION_DEFAULT)

    async def _on_resources_ready(ctx):
        # Own the one SandboxClient whenever a computer_use block is PRESENT — even disabled — so
        # _on_started can register UNAVAILABLE and supersede a stale 'available' descriptor from an
        # earlier enabled boot (codex Step-3 P2). Built before the runtime is constructed; the
        # research runner + capability registrar both read it off ctx.state.
        if cu_present:
            from nmem.agent_core.actors.computer_use import SandboxClient
            ctx.state["sandbox"] = SandboxClient(cu_cfg)

    async def _pre_start(ctx):
        # Selector mode only: assemble the actor registry (connect MCP/A2A + register tools) BEFORE
        # start(). A computer_use agent is a research agent — never also a selector — so skip it
        # whenever the block is present (enabled or not; a disabled sandbox → pure thinker, below).
        # Chat autonomy gate: from the operator's autonomy config, independent of the executor
        # mode, so a tiered deny/allow binds the always-on memory_search even for a pure-thinker
        # or computer_use agent — not only when an actors: block is present. Guarded: _build_gate
        # imports the OPTIONAL nmem_act package, which nmem[host] does not install; a pure-thinker
        # install without it must still boot (chat tools are unavailable there anyway).
        try:
            ctx.state["gate"] = _build_gate(config.get("autonomy"))
            ctx.runtime.gate = ctx.state["gate"]
        except ModuleNotFoundError:
            log.debug("[studio] autonomy gate unavailable (nmem_act not installed) — chat tools off")
        if cu_present:
            if config.get("actors"):
                log.warning("[studio] computer_use is configured — using the direct research runner; "
                            "the actors: selector tools are IGNORED (§33.3)")
            return
        actors_cfg = config.get("actors")
        if actors_cfg:
            from nmem.agent_core.actors import assemble_registry
            ctx.state["reg"], ctx.state["close"] = await assemble_registry(actors_cfg)
            # Expose the drop-in tools to the CHAT path too (converse/converse_stream), not just
            # the /act executor — a conversation can then call them under the same gate above.
            ctx.runtime.tools = ctx.state["reg"]

    def _build_executor(ctx, bridge):
        sandbox = ctx.state.get("sandbox")
        if sandbox is not None and sandbox.is_enabled():  # research mode: verifier-enforced runner
            from nmem.agent_core import build_research_runner
            pcfg = config.get("pursuit") or {}
            # Honor the SAME autonomy policy the selector mode does — an operator's
            # autonomy.deny of the research action must BLOCK it here too (codex Step-3). Defaults
            # to read_only, which permits the read-only research action.
            return build_research_runner(
                mem=ctx.runtime.mem, backend=ctx.runtime.backend, agent_id=ctx.runtime.agent_id,
                bridge=bridge, client=sandbox,
                action_name=_cu_action, tool_tag=pcfg.get("tool_tag"),
                verify_judge=_env_on("ACTUATOR_VERIFY_JUDGE_ENABLED"),
                reflect_enabled=_env_on("ACT_LLM_REFLECT_ENABLED", "true"),
                gate=_build_gate(config.get("autonomy")))
        reg = ctx.state.get("reg")                     # a disabled sandbox falls through to here → None
        if reg is None or len(reg) == 0:
            return None                                # nothing to act with → stays a pure thinker
        from nmem.agent_core.actors import build_executor as _mk
        return _mk(reg, backend=ctx.runtime.backend, mem=ctx.runtime.mem, agent_id=ctx.runtime.agent_id,
                   bridge=bridge, gate=ctx.state.get("gate"))

    async def _on_started(ctx):
        # Declare the sandbox to the nmem-sym capability registry as the catch-all knowledge actuator
        # so the feasibility planner can bind decomposition goals to it. Gated on goal_registry_enabled.
        # Runs whenever the client exists (present, enabled or not): a disabled client registers
        # UNAVAILABLE, which supersedes a stale 'available' row from a prior enabled boot (§33.2, P2).
        sandbox = ctx.state.get("sandbox")
        if sandbox is None:
            return
        try:
            from nmem_sym import config as sym_config
            if not getattr(sym_config.settings, "goal_registry_enabled", False):
                return
            from nmem.agent_core.actors.computer_use import register_computer_use_capability
            scope = getattr(getattr(ctx.runtime, "hive", None), "agent_id", None)
            avail = await register_computer_use_capability(
                ctx.runtime.bridge, goal_scope=scope, client=sandbox, action_name=_cu_action)
            log.info("[studio] registered computer-use capability %r under scope %r (%s)",
                     _cu_action, scope, avail)
        except Exception:  # noqa: BLE001
            log.warning("[studio] computer-use capability registration failed (non-fatal)", exc_info=True)

    async def _on_shutdown(ctx):
        close = ctx.state.get("close")
        if close is not None:
            await close()                              # tear down live MCP/A2A sessions
        sandbox = ctx.state.get("sandbox")
        if sandbox is not None:
            await sandbox.aclose()                     # single-owner lifecycle contract (no-op today)

    def _health_extras(ctx):
        out = {"agent_id": persona.agent_id,
               "viz": bool(os.environ.get("NMEM_VIZ_INGEST_URL")),
               "viz_url": os.environ.get("NMEM_VIZ_PUBLIC_URL", "")}
        sandbox = ctx.state.get("sandbox")
        if sandbox is not None:
            out["sandbox"] = {"enabled": sandbox.is_enabled(), "url": sandbox.cfg("url")}
        return out

    def _studio_routes(app, ctx):
        dashboard = agent_dashboard_html()

        @app.get("/", response_class=HTMLResponse)
        async def home():
            return dashboard

        @app.get("/tools")
        async def tools():
            """The actuator(s) this agent has, with capability class + autonomy level. Selector mode
            lists the registry; research mode reports the single sandbox actuator (it is deliberately
            NOT in the selector registry, §33.3) so the dashboard surfaces it and keeps /act reachable."""
            reg = ctx.state.get("reg")
            items = []
            if reg is not None:
                for name in reg.names():
                    a = reg.get(name)
                    items.append({"name": name, "capability": a.capability_class.value,
                                  "description": a.description})
            sandbox = ctx.state.get("sandbox")
            if sandbox is not None and sandbox.is_enabled():
                items.append({"name": _cu_action, "capability": "read_only",
                              "description": "Drive the computer-use sandbox to seek and verify "
                                             "knowledge for a goal."})
            return {"ok": True, "tools": items,
                    "autonomy": (config.get("autonomy") or {}).get("level", "read_only"),
                    "has_executor": ctx.runtime is not None and ctx.runtime._runner is not None}

        @app.post("/act")
        async def act(req: dict):
            """Give the agent a goal and run it through its actuator ONCE. Selector mode → a gated,
            outcome-recorded ToolCallingExecutor run; research mode → the verifier-enforced sandbox
            runner (the goal rides as the pursuit objective). Body: {goal}."""
            goal = (req or {}).get("goal", "").strip()
            if not goal:
                return {"ok": False, "error": "goal required"}
            if ctx.runtime is None or ctx.runtime._runner is None:
                return {"ok": False, "error": "this agent has no actuator configured (pure thinker)"}
            try:
                if ctx.state.get("sandbox") is not None:   # research mode: build the pursue proposal
                    from nmem_act import ActionProposal, CapabilityClass
                    proposal = ActionProposal(action_type=_cu_action, rationale=goal,
                                              capability_class=CapabilityClass.READ_ONLY,
                                              params={"objective": goal})
                    outcome = await ctx.runtime._runner.execute(proposal)
                else:
                    from nmem.agent_core.actors import run as _run
                    outcome = await _run(ctx.runtime._runner, goal)
                obs = outcome.observations or {}
                summary = getattr(outcome, "actual_outcome", None) or getattr(outcome, "outcome", "")
                return {"ok": True, "status": getattr(outcome.status, "value", str(outcome.status)),
                        "steps": obs.get("steps", []), "summary": summary}
            except Exception as e:  # noqa: BLE001
                log.warning("[studio] act failed: %s", e, exc_info=True)
                return {"ok": False, "error": str(e)}

    # The generic host owns lifespan/bootstrap/ops + the default /chat (runtime.converse — exactly
    # studio's old /chat). Studio injects only its executor (selector OR research runner) + the
    # sandbox lifecycle (resources_ready/started) + UI routes + auth.
    app, ctx = create_agent_app(
        config, persona,
        build_executor=_build_executor, on_resources_ready=_on_resources_ready,
        pre_start=_pre_start, on_started=_on_started, on_shutdown=_on_shutdown,
        extra_health=_health_extras, extra_routes=_studio_routes,
        title=f"nmem agent · {persona.agent_id}")

    from nmem.agent_core.auth import SessionAuth, install_session_auth, make_auth_router
    auth = SessionAuth()
    app.include_router(make_auth_router(auth))
    install_session_auth(app, auth)          # gate /admin//act//chat//tools AFTER all routes are added
    return app, ctx


def main() -> None:
    """Entry: pick the mode by whether the agent exists, then serve with uvicorn. The
    entrypoint has already sourced capabilities.env + secrets.env in agent mode. The runtime
    starts inside uvicorn's loop (agent-app lifespan), so DB connections bind to the serving loop."""
    import uvicorn

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    port = int(os.environ.get("STUDIO_PORT", "8080"))

    if agent_exists():
        app, _rt = build_agent_app()
    else:
        log.info("[studio] no agent yet — serving the setup wizard on :%d", port)
        app = build_wizard_app()
    uvicorn.run(app, host="0.0.0.0", port=port)


if __name__ == "__main__":
    main()
