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
    """AGENT mode: build the app for the one agent on the data volume (env already sourced by
    the entrypoint) — the ops router (+ /health) and the dashboard at ``/``. Returns (app,
    runtime). The runtime is started in a **lifespan hook**, NOT here: its asyncpg connections
    must be created on the SAME event loop that serves requests, or every DB-backed endpoint
    hits 'another operation is in progress'. So build synchronously, start under uvicorn's loop."""
    from contextlib import asynccontextmanager

    import yaml
    from fastapi import FastAPI
    from fastapi.responses import HTMLResponse

    from nmem.agent_core import AgentRuntime, make_ops_router
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

    # Actor executor holder — filled in the lifespan (async registry assembly), read by the
    # build_executor closure the runtime calls during start(). Pure thinker if no actors: config.
    actors = {"reg": None, "gate": None, "close": None}

    def _build_executor(bridge):
        reg = actors["reg"]
        if reg is None or len(reg) == 0:
            return None                                # nothing to act with → stays a pure thinker
        from nmem.agent_core.actors import build_executor as _mk
        return _mk(reg, backend=runtime.backend, mem=runtime.mem, agent_id=runtime.agent_id,
                   bridge=bridge, gate=actors["gate"])

    runtime = AgentRuntime(config, persona, build_executor=_build_executor)
    viz = {"bridge": None}

    @asynccontextmanager
    async def lifespan(app):
        actors_cfg = config.get("actors")
        if actors_cfg:                                 # connect MCP/A2A + register tools BEFORE start()
            from nmem.agent_core.actors import assemble_registry
            actors["reg"], actors["close"] = await assemble_registry(actors_cfg)
            actors["gate"] = _build_gate(config.get("autonomy"))
        await runtime.start()                          # on uvicorn's loop → connections bound correctly
        log.info("[studio] agent %s up: %s", runtime.agent_id, runtime.status)
        from nmem.agent_core.viz import init_viz       # live deltas → nmem-viz /ingest (if configured)
        viz["bridge"] = init_viz(runtime)
        yield
        if viz["bridge"] is not None:
            await viz["bridge"].close()
        if actors["close"] is not None:
            await actors["close"]()                    # tear down live MCP/A2A sessions
        await runtime.stop()

    def _health_extras():
        return {"agent_id": runtime.agent_id,
                "viz": bool(os.environ.get("NMEM_VIZ_INGEST_URL")),
                "viz_url": os.environ.get("NMEM_VIZ_PUBLIC_URL", "")}

    app = FastAPI(title=f"nmem agent · {runtime.agent_id}", lifespan=lifespan)
    from nmem.agent_core.auth import SessionAuth, install_session_auth, make_auth_router
    auth = SessionAuth()
    app.include_router(make_auth_router(auth))
    # /health carries the agent id (dashboard title) + whether/where the viz hub is reachable
    app.include_router(make_ops_router(lambda: runtime, extra_health=_health_extras))

    dashboard = agent_dashboard_html()

    @app.get("/", response_class=HTMLResponse)
    async def home():
        return dashboard

    @app.get("/tools")
    async def tools():
        """The actor tools this agent has, with capability class + autonomy level — what the
        dashboard's Act panel shows and what the gate governs."""
        reg = actors["reg"]
        gate = actors["gate"]
        items = []
        if reg is not None:
            for name in reg.names():
                a = reg.get(name)
                items.append({"name": name, "capability": a.capability_class.value,
                              "description": a.description})
        return {"ok": True, "tools": items,
                "autonomy": (config.get("autonomy") or {}).get("level", "read_only"),
                "has_executor": runtime._runner is not None}

    @app.post("/act")
    async def act(req: dict):
        """Give the agent a goal and let it use its tools to accomplish it (one gated,
        outcome-recorded ToolCallingExecutor run). Body: {goal}. Returns the composite status +
        the per-tool step trace."""
        goal = (req or {}).get("goal", "").strip()
        if not goal:
            return {"ok": False, "error": "goal required"}
        if runtime._runner is None:
            return {"ok": False, "error": "this agent has no tools configured (pure thinker)"}
        from nmem.agent_core.actors import run as _run
        try:
            outcome = await _run(runtime._runner, goal)
            obs = outcome.observations or {}
            return {"ok": True, "status": getattr(outcome.status, "value", str(outcome.status)),
                    "steps": obs.get("steps", []), "summary": getattr(outcome, "outcome", "")}
        except Exception as e:  # noqa: BLE001
            log.warning("[studio] act failed: %s", e, exc_info=True)
            return {"ok": False, "error": str(e)}

    @app.post("/chat")
    async def chat(req: dict):
        """Hold one grounded conversation turn (agent_core.chat.converse). Body: {message,
        history?:[{role,content}]}. The chat page keeps history; the agent's memory grounds
        every turn regardless."""
        msg = (req or {}).get("message", "").strip()
        if not msg:
            return {"ok": False, "error": "message required"}
        try:
            reply = await runtime.converse(msg, history=(req or {}).get("history") or [])
            return {"ok": True, "reply": reply}
        except Exception as e:  # noqa: BLE001
            log.warning("[studio] chat failed: %s", e, exc_info=True)
            return {"ok": False, "error": str(e)}

    install_session_auth(app, auth)          # gate /admin//act//chat//tools AFTER all routes are added
    return app, runtime


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
