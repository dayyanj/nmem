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
def _merge_env_file(path: str, kv: dict) -> None:
    """Merge KEY=VALUE pairs into an env file (create/replace keys, keep the rest). Values are
    written raw on their own line — the entrypoint sources this file, so no inline comments."""
    existing: dict = {}
    if os.path.exists(path):
        for line in open(path):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                existing[k.strip()] = v.strip()
    existing.update(kv)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        fh.write("# secrets + DSN for this agent — sourced by entrypoint.sh, never committed\n")
        for k, v in existing.items():
            fh.write(f"{k}={v}\n")
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
    build_memory/build_symbol_graph). Idempotent. Returns the DB name."""
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
            await conn.execute(f'CREATE DATABASE "{db}"')          # cannot run in a txn; asyncpg autocommits
            log.info("[studio] provisioned database %s", db)
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

    agent_id = spec["agent_id"]
    db = await provision_db(agent_id)
    _merge_env_file(os.path.join(agent_dir, "secrets.env"),
                    {f"{agent_id.upper()}_DB_DSN_ASYNC": _agent_dsn(agent_id)})
    log.info("[studio] staged agent %s (db=%s); scheduling restart", agent_id, db)
    asyncio.get_event_loop().call_later(1.5, _request_restart)


# ── the two apps ────────────────────────────────────────────────────────────────
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

    runtime = AgentRuntime(config, persona)            # pure thinker; add executors via a custom image
    viz = {"bridge": None}

    @asynccontextmanager
    async def lifespan(app):
        await runtime.start()                          # on uvicorn's loop → connections bound correctly
        log.info("[studio] agent %s up: %s", runtime.agent_id, runtime.status)
        from nmem.agent_core.viz import init_viz       # live deltas → nmem-viz /ingest (if configured)
        viz["bridge"] = init_viz(runtime)
        yield
        if viz["bridge"] is not None:
            await viz["bridge"].close()
        await runtime.stop()

    def _health_extras():
        return {"agent_id": runtime.agent_id,
                "viz": bool(os.environ.get("NMEM_VIZ_INGEST_URL")),
                "viz_url": os.environ.get("NMEM_VIZ_PUBLIC_URL", "")}

    app = FastAPI(title=f"nmem agent · {runtime.agent_id}", lifespan=lifespan)
    # /health carries the agent id (dashboard title) + whether/where the viz hub is reachable
    app.include_router(make_ops_router(lambda: runtime, extra_health=_health_extras))

    dashboard = agent_dashboard_html()

    @app.get("/", response_class=HTMLResponse)
    async def home():
        return dashboard

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
