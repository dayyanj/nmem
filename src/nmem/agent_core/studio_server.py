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
import stat
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


def _schedule_restart(delay: float = 1.5) -> None:
    """Restart into agent mode after ``delay`` (lets the HTTP response flush first) WITHOUT rewriting
    agent.yaml. Roster/key edits change the descriptor file on disk, so a plain restart re-runs
    expand-at-load — no config rebuild needed. Preferred over _reconfigure(_current_spec()) for those,
    because _current_spec()/write_agent can't represent comms/reasoning_effort/custom db.env_key and
    would silently drop them."""
    import asyncio
    asyncio.get_event_loop().call_later(delay, _request_restart)


def _patch_agent_yaml_hive(agent_dir: str, delegation: dict | None) -> None:
    """Persist ONLY the hive.delegation block to agent.yaml (delegation lives in the seed, not the
    descriptor), leaving comms/backends/db and every other field byte-for-byte intact — unlike a full
    write_agent rebuild from _current_spec(), which can't represent them and drops them. Read → patch →
    atomic replace (temp + os.replace) so a crash mid-write can't truncate the agent's config."""
    import tempfile

    import yaml
    # Follow a symlink to its TARGET so the atomic replace updates the externally-maintained seed the
    # agent actually reads — replacing the link with a regular file would strand the target unchanged and
    # detach it from future seed updates (mirrors _replace_keyfile / HiveDescriptor.save).
    ypath = os.path.realpath(os.path.join(agent_dir, "agent.yaml"))
    with open(ypath) as f:
        doc = yaml.safe_load(f) or {}
    hive = doc.get("hive") if isinstance(doc.get("hive"), dict) else {}
    if delegation is None:
        hive.pop("delegation", None)
    else:
        hive["delegation"] = delegation
    doc["hive"] = hive
    prev = os.stat(ypath) if os.path.exists(ypath) else None
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(os.path.abspath(ypath)), prefix=".agent-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            yaml.safe_dump(doc, f, sort_keys=False)
        # Preserve the existing file's mode + owner/group so an in-place edit doesn't tighten a
        # group-/world-readable agent.yaml down to the server user's 0600 (mirrors HiveDescriptor.save).
        if prev is not None:
            os.chmod(tmp, stat.S_IMODE(prev.st_mode))
            if hasattr(os, "chown"):
                try:
                    os.chown(tmp, prev.st_uid, prev.st_gid)
                except OSError:
                    try:
                        os.chown(tmp, -1, prev.st_gid)
                    except OSError:
                        pass
        os.replace(tmp, ypath)
    except Exception:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


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
    # Materialize wizard hive-membership intent (create/join) AFTER the config exists. Non-fatal: a
    # hive setup failure must not destroy an otherwise-good agent — it boots solo and the operator can
    # establish/join the hive from the dashboard (P2c). Only runs when the wizard asked for peering.
    setup = (spec.get("hive") or {}).get("setup")
    if isinstance(setup, dict) and str(setup.get("action", "")).lower() in ("create", "join"):
        res = _provision_hive(agent_dir, agent_id, setup)
        if not res.get("ok"):
            # The user EXPLICITLY asked to create/join a hive; a failure (malformed pasted descriptor,
            # etc.) must SURFACE and be retryable — not silently strand a solo agent with no dashboard
            # recovery path (the Hive card is hidden for solo agents, and edit mode hides Create/Join).
            # Roll the whole create back (as a DB-provision failure does) so the wizard reports the error
            # and the operator can fix the input and retry. (Runtime peering issues never brick — the
            # peer bus fails open — so this fatal path is create-time input validation only.)
            shutil.rmtree(agent_dir, ignore_errors=True)
            raise RuntimeError(f"hive setup failed: {res.get('error')} — fix the input and retry")
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


async def _probe_identity_readiness() -> dict | None:
    """One-shot boot probe for the writing-style (LUAR) identity sidecars, so the dashboard can tell
    the operator plainly whether the feature is actually live. Returns None when text-identity is off
    (nothing to say). Never raises — text-identity fails open, so this only drives a hint, never
    behaviour. Cached at boot; brought up-to-date on the next agent restart."""
    if not _env_on("NMEM_CHAT_TEXT_IDENTITY_ENABLED"):
        return None
    matcher = os.environ.get("NMEM_IDENTITY_MATCHER_URL", "").strip()
    embed = os.environ.get("NMEM_IDENTITY_TEXT_EMBED_URL", "").strip()
    if not (matcher and embed):
        return {"configured": False}

    async def _matcher_health() -> tuple[bool, bool]:
        """(reachable, text_style_calibrated) — the matcher's /health reports the loaded fusion
        curves, so we can tell recognition is actually LIVE, not merely that the sidecar is up."""
        try:
            import httpx
            async with httpx.AsyncClient(timeout=2.0) as c:
                r = await c.get(matcher.rstrip("/") + "/health")
                j = r.json() or {}
                ok = r.status_code == 200 and bool(j.get("ok", True))
                return ok, ("text_style" in (j.get("calibrations") or []))
        except Exception:  # noqa: BLE001
            return False, False

    matcher_ok, calibrated = await _matcher_health()
    return {"configured": True, "matcher": matcher_ok, "text_embed": await _probe_url_health(embed),
            "calibrated": calibrated}


async def _probe_url_health(url: str) -> bool:
    """Best-effort GET {url}/health → True on a 200. Never raises."""
    try:
        import httpx
        async with httpx.AsyncClient(timeout=2.0) as c:
            r = await c.get(url.rstrip("/") + "/health")
            return r.status_code == 200
    except Exception:  # noqa: BLE001
        return False


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


def _expand_hive_config(config: dict, agent_dir: str, default_identity: str) -> None:
    """Expand-at-load (P1.2): turn a compact ``hive:`` membership block into the verbose
    ``exchange:``/``delegation:`` config Piece A reads, IN PLACE, and resolve the broker URL into the
    process env. Thin wrapper over the shared ``hive_descriptor.expand_into_config`` (the ``hive
    doctor`` CLI uses the same path so it validates the effective config). ``env=os.environ`` here so
    ``peer._resolve`` sees the descriptor's broker. No-op without ``hive.descriptor``; fail-open."""
    from nmem.agent_core.hive_descriptor import expand_into_config
    expand_into_config(config, agent_dir=agent_dir, default_identity=default_identity,
                       env=os.environ, logger=log)


def _fingerprint(pub: str) -> str:
    """Short, stable fingerprint of a base64 public key — operators compare these across hosts to
    confirm a member's key matches out-of-band (the trust-root the untrusted broker can't provide).
    Never raises: a malformed key yields 'invalid', not an exception into the dashboard route."""
    import base64
    import hashlib
    if not isinstance(pub, str) or not pub:
        return "invalid"
    try:
        raw = base64.b64decode(pub, validate=True)
    except Exception:  # noqa: BLE001
        return "invalid"
    if len(raw) != 32:            # x25519/ed25519 pubs are 32 bytes — anything else is malformed
        return "invalid"
    return hashlib.sha256(raw).hexdigest()[:16]


def hive_status_payload(config: dict, report) -> dict:
    """Read-only 'is my hive healthy?' summary for the dashboard Hive card: which member I am, the
    roster (with key fingerprints for out-of-band verification), the task types I accept + my peer
    routes, and the cached hive-doctor findings. PURE over (config, DoctorReport|None) so it's unit-
    testable and reuses the readiness refresher's cached report (no per-request re-probe). Never
    raises — a malformed field degrades the card, never 500s the dashboard. Works for BOTH a
    descriptor-backed hive and a hand-written exchange:/delegation: seed (reads the expanded config)."""
    cfg = config if isinstance(config, dict) else {}
    ex = cfg.get("exchange") if isinstance(cfg.get("exchange"), dict) else {}
    dl = cfg.get("delegation") if isinstance(cfg.get("delegation"), dict) else {}
    hive = cfg.get("hive") if isinstance(cfg.get("hive"), dict) else {}
    enabled = bool(ex.get("enabled") or dl.get("enabled"))
    ident = ((ex.get("identity") or {}).get("agent_id")) or hive.get("identity") or ""
    keyring = ex.get("keyring") if isinstance(ex.get("keyring"), dict) else {}
    members = []
    for aid, bundle in sorted(keyring.items()):
        b = bundle if isinstance(bundle, dict) else {}
        members.append({"agent_id": aid, "sign_fp": _fingerprint(b.get("sign_pub", "")),
                        "box_fp": _fingerprint(b.get("box_pub", "")), "is_me": aid == ident})
    accepts = [t.get("type") for t in (dl.get("task_types") or [])
               if isinstance(t, dict) and t.get("type")]
    peer_channels = dl.get("peer_channels") if isinstance(dl.get("peer_channels"), dict) else {}
    checks: list[dict] = []
    ok, solo = True, False
    if report is not None:
        try:
            checks = [{"name": c.name, "ok": bool(c.ok), "level": c.level, "message": c.message}
                      for c in report.checks]
            ok, solo = bool(report.ok), (not report.enabled)
        except Exception:  # noqa: BLE001 — advisory; a broken report just omits the checks
            pass
    return {"enabled": enabled, "identity": ident, "descriptor": hive.get("descriptor"),
            "members": members, "accepts": accepts, "peer_channels": peer_channels,
            "delegation_enabled": bool(dl.get("enabled")),
            "checks": checks, "ok": ok, "solo": solo}


def _hive_descriptor_path(config: dict, agent_dir: str) -> str | None:
    """Absolute path to the descriptor backing this agent, or None if it isn't descriptor-backed.
    Roster edits (add/remove-member) mutate this file; a solo agent or a hand-written
    exchange:/delegation: seed has no descriptor to write, so those edits are refused up front.
    Pure; never raises. Relative descriptor paths resolve against the agent dir (as expand-at-load
    does), so the returned path matches the file the runtime actually reads."""
    cfg = config if isinstance(config, dict) else {}
    hive = cfg.get("hive") if isinstance(cfg.get("hive"), dict) else {}
    desc = hive.get("descriptor")
    if not isinstance(desc, str) or not desc:
        return None
    return desc if os.path.isabs(desc) else os.path.join(agent_dir, desc)


def _hive_keyfile_path(config: dict, agent_dir: str, default_identity: str) -> tuple[str, str]:
    """This agent's ``(identity, keyfile_abs_path)`` — the private key rotate-key replaces. Mirrors
    expand-at-load's resolution exactly (``hive.identity`` or the persona id; ``hive.keyfile`` or
    ``<identity>.key.json``; relative → under the agent dir) so the path matches the file the runtime
    reads. Pure; never raises."""
    cfg = config if isinstance(config, dict) else {}
    hive = cfg.get("hive") if isinstance(cfg.get("hive"), dict) else {}
    identity = hive.get("identity") or default_identity
    keyfile = hive.get("keyfile") or f"{identity}.key.json"
    if not os.path.isabs(keyfile):
        keyfile = os.path.join(agent_dir, keyfile)
    return identity, keyfile


def _hive_identity(config: dict, default_identity: str) -> str:
    """This agent's EFFECTIVE hive identity — ``hive.identity`` if set, else the persona id. This is the
    agent_id it appears as in the roster (which can differ from the persona id), so roster lookups and
    the self-removal guard must resolve it rather than assuming persona.agent_id. Pure; never raises."""
    cfg = config if isinstance(config, dict) else {}
    hive = cfg.get("hive") if isinstance(cfg.get("hive"), dict) else {}
    return hive.get("identity") or default_identity


def _provision_hive(agent_dir: str, agent_id: str, setup: dict) -> dict:
    """Materialize a wizard hive-membership intent into a working peering config (P2b). Runs AFTER
    write_agent created ``agent_dir``: generate THIS agent's keyfile, create a new descriptor or accept
    a pasted one, add this agent to it, and PATCH agent.yaml's ``hive:`` block to the compact form
    expand-at-load reads (descriptor/identity/keyfile[/delegation]) — so the next boot peers with no
    hand-editing. Thin orchestration over the ``hive_cli`` engine (same code the CLI + dashboard use).

    ``setup`` = {action:'create'|'join', name?, descriptor_text?, broker_env?, accepts?, nfs?}. Returns
    {ok, action, descriptor, keyfile, bundle} or {ok:False, error}. NEVER raises — a failed hive setup
    is reported so the caller boots the agent solo (retry from the dashboard), never crash-boots."""
    import yaml

    from nmem.agent_core import hive_cli
    from nmem.agent_core.hive_descriptor import HiveDescriptor

    action = str((setup or {}).get("action", "")).lower()
    if action not in ("create", "join"):
        return {"ok": False, "error": f"unknown hive action {action!r} (want 'create' or 'join')"}
    nfs = bool(setup.get("nfs"))
    desc_path = os.path.join(agent_dir, "hive.yaml")
    keyfile = os.path.join(agent_dir, f"{agent_id}.key.json")
    try:
        if action == "create":
            name = str(setup.get("name") or "").strip() or f"{agent_id}-hive"
            broker_env = str(setup.get("broker_env") or "NMEX_REDIS_URL").strip() or "NMEX_REDIS_URL"
            hive_cli.create_descriptor(name, out=desc_path, broker_env=broker_env, nfs=nfs)
        else:  # join — persist the pasted descriptor, validating it parses BEFORE writing it
            text = setup.get("descriptor_text")
            if not isinstance(text, str) or not text.strip():
                return {"ok": False, "error": "join needs a pasted hive descriptor"}
            HiveDescriptor.from_dict(yaml.safe_load(text) or {})    # raises on a malformed descriptor
            with open(desc_path, "w") as f:
                f.write(text)
        res = hive_cli.join_hive(desc_path, agent_id=agent_id, keyfile=keyfile, nfs=nfs)
    except SystemExit as e:               # engine refuses (clobber, bad bundle) → SystemExit
        return {"ok": False, "error": str(e)}
    except Exception as e:  # noqa: BLE001 — malformed descriptor / IO; report, don't crash the create
        log.warning("[studio] hive provision (%s) failed for %s: %s", action, agent_id, e, exc_info=True)
        return {"ok": False, "error": str(e)}

    # Patch agent.yaml's hive block to the compact peering form. Relative paths (portable across a
    # host→container bind-mount; expand-at-load resolves them against the agent dir). Merge alongside
    # any mode/graph_role already written (the two hive axes are orthogonal).
    accepts = setup.get("accepts")
    peering = {"descriptor": "hive.yaml", "identity": agent_id, "keyfile": f"{agent_id}.key.json"}
    try:
        ypath = os.path.join(agent_dir, "agent.yaml")
        with open(ypath) as f:
            doc = yaml.safe_load(f) or {}
        cur = doc.get("hive") if isinstance(doc.get("hive"), dict) else {}
        cur.pop("setup", None)            # belt-and-suspenders (config_writer already strips it)
        # Delegation ledgers need an ISOLATED db — AgentRuntime._wire_delegation refuses delegation under
        # shared_world graph-sharing. So enable delegation only when NOT shared_world; a shared_world agent
        # still gets full peering (the exchange), just no delegation queue (rather than a config that
        # silently can't deliver the requested delegation).
        shared_world = cur.get("mode") == "shared_world"
        if accepts is not None and not shared_world:
            peering["delegation"] = {"enabled": True, "accepts": [str(a) for a in accepts if a]}
        elif accepts is not None and shared_world:
            log.info("[studio] hive %s: '%s' is shared_world — peering enabled, delegation skipped "
                     "(delegation ledgers need an isolated db)", action, agent_id)
        cur.update(peering)
        doc["hive"] = cur
        with open(ypath, "w") as f:
            yaml.safe_dump(doc, f, sort_keys=False)
    except Exception as e:  # noqa: BLE001 — keys exist but the wiring failed; solo boot, operator retries
        log.warning("[studio] hive provision: wrote descriptor+key but could not patch agent.yaml: %s",
                    e, exc_info=True)
        return {"ok": False, "error": f"materialized keys but failed to wire agent.yaml: {e}"}
    log.info("[studio] hive %s: '%s' wired for peering (descriptor %s)", action, agent_id, desc_path)
    return {"ok": True, "action": action, "descriptor": desc_path, "keyfile": keyfile,
            "bundle": res.get("bundle")}


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

    # Hive expand-at-load (P1.2): if the seed carries a `hive:` block with a descriptor, DERIVE the
    # verbose exchange:/delegation: config from it in place — so the comms/delegation factories below
    # read generated config instead of hand-maintained keyring/channels/peer_channels. No-op (config
    # unchanged) when there's no `hive.descriptor`, so a hand-written exchange:/delegation: seed is
    # byte-identical to today. Fail-open: a bad descriptor leaves the agent solo, never crash-boots.
    _expand_hive_config(config, agent_dir, persona.agent_id)

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

            # Embodied visual memory (H5): generic, no per-agent wiring. build_visual_memory
            # self-gates — returns None unless NMEM_VISUAL_MEMORY_ENABLED is on AND the sandbox is
            # enabled AND a sensory DSN (NMEM_SENSOR_DB_DSN) + the nmem-sym-sensor lib are present;
            # SensorGraph.connect() self-applies the sensory migrations. Fail-open: a text agent, a
            # disabled sandbox, or a missing sensor lib just leaves visual_memory = None.
            from nmem.agent_core import build_visual_memory
            ctx.state["visual_memory"] = await build_visual_memory(
                ctx.state["sandbox"], mem=ctx.mem, agent_id=persona.agent_id)

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
                visual_memory=ctx.state.get("visual_memory"),  # SEE→REMEMBER: wrap the outcome sink
                gate=_build_gate(config.get("autonomy")))
        reg = ctx.state.get("reg")                     # a disabled sandbox falls through to here → None
        if reg is None or len(reg) == 0:
            return None                                # nothing to act with → stays a pure thinker
        from nmem.agent_core.actors import build_executor as _mk
        # Thread the brain's reasoning_effort into the tool selector: a thinking-capable model
        # (e.g. Qwen, family: qwen) needs reasoning ON in the loop or it never concludes and
        # thrashes tools to max_steps (the query-db gotcha; see actors.build_executor). Read it
        # from backends.brain.reasoning_effort; None → unchanged (thinking off).
        brain = (config.get("backends", {}) or {}).get("brain", {}) or {}
        return _mk(reg, backend=ctx.runtime.backend, mem=ctx.runtime.mem, agent_id=ctx.runtime.agent_id,
                   bridge=bridge, gate=ctx.state.get("gate"),
                   reasoning_effort=brain.get("reasoning_effort"))

    async def _refresh_readiness(ctx):
        # Refresh the advisory readiness probes (identity sidecars + the sandbox). Cached for the sync
        # _health_extras. Re-run on a timer so state that changes AFTER boot — bringing a profile up
        # per the banner's instruction, or a later outage — is reflected instead of frozen at startup.
        try:
            ctx.state["readiness_identity"] = await _probe_identity_readiness()
        except Exception:  # noqa: BLE001 — readiness is advisory, never fatal
            ctx.state["readiness_identity"] = None
        try:
            sandbox = ctx.state.get("sandbox")
            url = sandbox.cfg("url") if (sandbox is not None and sandbox.is_enabled()) else ""
            ctx.state["readiness_sandbox"] = (
                {"reachable": await _probe_url_health(url)} if url else None)
        except Exception:  # noqa: BLE001
            ctx.state["readiness_sandbox"] = None
        # Hive membership preflight (hive doctor, P0): validate the exchange/delegation/keyfile/broker
        # config + probe broker reachability, so a misconfigured hive is a visible dashboard warning
        # instead of a silent runtime drop. Read-only; fails open (an errored probe → no hive items).
        try:
            from nmem.agent_core import hive_doctor
            ctx.state["readiness_hive"] = await hive_doctor.run(config)
        except Exception:  # noqa: BLE001 — advisory only
            ctx.state["readiness_hive"] = None

    async def _on_started(ctx):
        # Start the readiness refresher (first pass now, then every 20s) so the dashboard's banners
        # track the real sidecar/sandbox state over time, not just the boot snapshot.
        import asyncio
        await _refresh_readiness(ctx)

        async def _loop():
            while True:
                await asyncio.sleep(20)
                await _refresh_readiness(ctx)
        ctx.state["readiness_task"] = asyncio.create_task(_loop())
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
        task = ctx.state.get("readiness_task")
        if task is not None:
            task.cancel()
        peer = ctx.state.get("peer")
        if peer is not None:
            await peer.close()                         # close the exchange bus (host owns it) before runtime.stop
        close = ctx.state.get("close")
        if close is not None:
            await close()                              # tear down live MCP/A2A sessions
        vm = ctx.state.get("visual_memory")
        if vm is not None:
            await vm.aclose()                          # close the sensor pool + save predictor
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
        vm = ctx.state.get("visual_memory")
        if vm is not None:
            out["visual_memory"] = bool(getattr(vm, "enabled", False))
        peer = ctx.state.get("peer")
        if peer is not None:
            out["peer"] = bool(getattr(peer, "started", False))
            deleg = (ctx.runtime.status or {}).get("delegation") if ctx.runtime is not None else None
            if deleg is not None:
                out["delegation"] = deleg
        out["readiness"] = _readiness(ctx)
        return out

    def _readiness(ctx):
        """Plain-English 'is this actually working?' notes for capabilities that need an optional
        profile or extra wiring — so an enabled-but-unsatisfied capability is a visible, actionable
        banner on the dashboard, never a silent no-op. warn = enabled but inert; info = enabled + live."""
        items: list[dict] = []
        ri = ctx.state.get("readiness_identity")
        if ri is not None:                                     # writing-style identity is ON
            if not ri.get("configured"):
                items.append({"level": "warn", "capability": "writing-style identity",
                              "message": "Writing-style identity is ON but its sidecars aren't configured "
                                         "(NMEM_IDENTITY_MATCHER_URL / _TEXT_EMBED_URL)."})
            elif not (ri.get("matcher") and ri.get("text_embed")):
                items.append({"level": "warn", "capability": "writing-style identity",
                              "message": "Writing-style identity is ON but the LUAR sidecars aren't "
                                         "reachable — recognition stays off until they're up. Start them: "
                                         "docker compose --profile identity up."})
            elif not ri.get("calibrated"):                     # sidecars up, but no text_style curve loaded
                items.append({"level": "warn", "capability": "writing-style identity",
                              "message": "Writing-style identity is ON and its sidecars are up, but no "
                                         "text_style calibration is loaded — recognition abstains. Seed a "
                                         "default (nmem-identity seed-text-calibration) or fit one, then "
                                         "restart the matcher."})
            else:
                items.append({"level": "info", "capability": "writing-style identity",
                              "message": "Writing-style identity is live (using a default calibration — "
                                         "refit with nmem-identity fit_calibration for best accuracy)."})
        # A computer-use sandbox (research OR perception) that's configured but unreachable means /act
        # and visual capture can't run — warn regardless of visual memory.
        sandbox = ctx.state.get("sandbox")
        rs = ctx.state.get("readiness_sandbox")                # {reachable} from the probe, or None
        sandbox_on = sandbox is not None and sandbox.is_enabled()
        sandbox_reachable = bool(rs and rs.get("reachable"))
        if sandbox_on and not sandbox_reachable:
            items.append({"level": "warn", "capability": "computer-use sandbox",
                          "message": "The computer-use sandbox is configured but isn't reachable — "
                                     "acting/research can't run. Start it: docker compose "
                                     "--profile perception up."})
        if _env_on("NMEM_VISUAL_MEMORY_ENABLED"):              # embodied visual memory is ON
            if not sandbox_on:
                items.append({"level": "warn", "capability": "visual memory",
                              "message": "Visual memory is ON but no computer-use sandbox is attached. "
                                         "Start it: docker compose --profile perception up, and set the "
                                         "Research sandbox to http://sandbox:8080."})
            elif sandbox_reachable and ctx.state.get("visual_memory") is None:
                items.append({"level": "warn", "capability": "visual memory",
                              "message": "Visual memory is ON and a sandbox is attached, but the sensory "
                                         "store isn't available (check NMEM_SENSOR_DB_DSN / nmem-sym-sensor)."})
        # Hive membership: surface the doctor's actionable findings (warn/error), plus one green
        # note when a configured hive is fully healthy — so "peering on but broker down / roster
        # asymmetric / keyfile unreadable" is a banner, not a silent no-op.
        rh = ctx.state.get("readiness_hive")
        if rh is not None and rh.enabled:
            problems = rh.readiness_items()
            items.extend(problems)
            if not problems and rh.ok:
                items.append({"level": "info", "capability": "hive membership",
                              "message": "Hive is configured and healthy (peering + delegation "
                                         "validated, broker reachable)."})
        return items

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

        @app.get("/hive/status")
        async def hive_status():
            """Read-only hive membership summary for the dashboard Hive card — roster + key
            fingerprints, accepted task types + peer routes, and the cached hive-doctor findings.
            Reuses the readiness refresher's cached DoctorReport (refreshed every 20s), so this is a
            cheap read with no per-request broker probe. Fails open to a solo/disabled payload."""
            try:
                return {"ok": True, **hive_status_payload(config, ctx.state.get("readiness_hive"))}
            except Exception as e:  # noqa: BLE001
                log.warning("[studio] hive status failed: %s", e, exc_info=True)
                return {"ok": False, "enabled": False, "error": str(e)}

        @app.get("/hive/descriptor")
        async def hive_descriptor():
            """The shareable hive descriptor (PUBLIC only — member public keys + the broker env-var
            NAME, never a secret) plus THIS agent's bundle, so the operator can hand the descriptor to
            a new joiner or paste their bundle to an existing keeper. Descriptor-backed hives only."""
            desc_path = _hive_descriptor_path(config, agent_dir)
            if desc_path is None or not os.path.exists(desc_path):
                return {"ok": False, "error": "this agent isn't descriptor-backed (no hive descriptor)"}
            try:
                from nmem.agent_core.hive_descriptor import HiveDescriptor
                with open(desc_path) as f:
                    text = f.read()
                desc = HiveDescriptor.load(desc_path)
                me = desc.member(_hive_identity(config, persona.agent_id))
                return {"ok": True, "name": desc.name, "text": text,
                        "bundle": me.bundle() if me else None}
            except Exception as e:  # noqa: BLE001
                log.warning("[studio] hive descriptor read failed: %s", e, exc_info=True)
                return {"ok": False, "error": str(e)}

        @app.post("/hive/add-member")
        async def hive_add_member(req: dict):
            """Merge a peer's PUBLIC bundle into this hive's descriptor and restart to pick it up.
            Body: {bundle:{agent_id,sign_pub,box_pub}} or {bundle_text:'<json>'}. Descriptor-backed
            hives only (a hand-written seed has no roster file). Restart preserves memory (DB untouched)."""
            from nmem.agent_core import hive_cli
            desc_path = _hive_descriptor_path(config, agent_dir)
            if desc_path is None:
                return {"ok": False, "error": "this agent isn't descriptor-backed — roster edits need a "
                                              "hive descriptor (create or join a hive first)"}
            bundle = (req or {}).get("bundle")
            if bundle is None and isinstance((req or {}).get("bundle_text"), str):
                import json as _json
                try:
                    bundle = _json.loads(req["bundle_text"])
                except Exception as e:  # noqa: BLE001
                    return {"ok": False, "error": f"bundle_text is not valid JSON: {e}"}
            if not isinstance(bundle, dict):
                return {"ok": False, "error": "bundle must be a JSON object {agent_id, sign_pub, box_pub}"}
            try:
                added = hive_cli.add_member(desc_path, bundle)
            except SystemExit as e:       # engine raises SystemExit for a non-object bundle
                return {"ok": False, "error": str(e)}
            except Exception as e:  # noqa: BLE001 — malformed keys (HiveDescriptorError) etc.
                log.warning("[studio] hive add-member failed: %s", e, exc_info=True)
                return {"ok": False, "error": str(e)}
            _schedule_restart()      # descriptor changed on disk → restart re-expands; no config rewrite
            return {"ok": True, "added": added.get("agent_id"), "restarting": True}

        @app.post("/hive/remove-member")
        async def hive_remove_member(req: dict):
            """Drop a member from this hive's descriptor and restart. Body: {agent_id}. Refuses to
            remove SELF (leaving a hive = reset the data volume). Descriptor-backed hives only."""
            from nmem.agent_core import hive_cli
            desc_path = _hive_descriptor_path(config, agent_dir)
            if desc_path is None:
                return {"ok": False, "error": "this agent isn't descriptor-backed — roster edits need a "
                                              "hive descriptor"}
            aid = str((req or {}).get("agent_id", "")).strip()
            if not aid:
                return {"ok": False, "error": "agent_id required"}
            if aid == _hive_identity(config, persona.agent_id):
                return {"ok": False, "error": "can't remove yourself from your own roster "
                                              "(reset the data volume to leave a hive)"}
            try:
                removed = hive_cli.remove_member(desc_path, aid)
            except Exception as e:  # noqa: BLE001
                log.warning("[studio] hive remove-member failed: %s", e, exc_info=True)
                return {"ok": False, "error": str(e)}
            if not removed:
                return {"ok": False, "error": f"no member '{aid}' in the descriptor"}
            _schedule_restart()      # descriptor changed on disk → restart re-expands; no config rewrite
            return {"ok": True, "removed": aid, "restarting": True}

        @app.post("/hive/delegation")
        async def hive_delegation(req: dict):
            """Enable/disable whether this agent ACCEPTS delegated peer tasks (its worker), and
            restart. Body: {enabled:bool, accepts?:[str]}. Enabling needs at least one accepted task
            type (existing or passed in). Descriptor-backed hives only. Memory preserved."""
            enabled = bool((req or {}).get("enabled"))
            if _hive_descriptor_path(config, agent_dir) is None:
                return {"ok": False, "error": "this agent isn't descriptor-backed — configure "
                                              "delegation in the seed"}
            # Delegation ledgers need an isolated db — AgentRuntime._wire_delegation refuses BOTH worker
            # and requester roles under shared_world. Refuse to ENABLE here (same rule as _provision_hive)
            # so the dashboard can't report delegation on when no tasks could ever run. Disabling is fine.
            if enabled and (config.get("hive") or {}).get("mode") == "shared_world":
                return {"ok": False, "error": "delegation needs an isolated database — this agent shares a "
                                              "world graph (hive.mode: shared_world), so delegation can't "
                                              "run. Peering (the exchange) still works."}
            deleg = dict((config.get("hive") or {}).get("delegation") or {})
            accepts = (req or {}).get("accepts")
            if isinstance(accepts, list):
                deleg["accepts"] = [str(a) for a in accepts if a]
            deleg.setdefault("accepts", [])          # enabling with no worker types = requester-only (valid)
            deleg["enabled"] = enabled
            try:
                # Patch ONLY the hive.delegation block on disk (comms/backends/db untouched) + restart —
                # a full _reconfigure(_current_spec()) would drop config the wizard can't represent.
                _patch_agent_yaml_hive(agent_dir, deleg)
            except Exception as e:  # noqa: BLE001
                log.warning("[studio] hive delegation toggle failed: %s", e, exc_info=True)
                return {"ok": False, "error": str(e)}
            _schedule_restart()
            return {"ok": True, "enabled": enabled, "accepts": deleg.get("accepts", []),
                    "restarting": True}

        @app.post("/hive/rotate-key")
        async def hive_rotate_key(req: dict):
            """Rotate THIS agent's keypair: generate a fresh key, publish the new public bundle to the
            descriptor, swap in the private keyfile, and restart. Descriptor-backed hives only. Peers
            must pick up the updated descriptor/bundle afterwards or they'll reject our messages — the
            returned bundle is surfaced so the operator can re-share it. Memory (the DB) is untouched."""
            from nmem.agent_core import hive_cli
            desc_path = _hive_descriptor_path(config, agent_dir)
            if desc_path is None:
                return {"ok": False, "error": "this agent isn't descriptor-backed — key rotation needs a "
                                              "hive descriptor"}
            identity, keyfile = _hive_keyfile_path(config, agent_dir, persona.agent_id)
            # Preserve the keyfile's EXACT existing permission bits so a rotated key keeps the same
            # access (0600 private, 0644 NFS root-squash, or a group-restricted 0640) — never widen a
            # group-only key to world-readable. `nfs` (world-readable) gates the DESCRIPTOR's
            # world-readability too, so key it on the other-read bit only, not group-read.
            try:
                st_mode = os.stat(keyfile).st_mode
                keymode = stat.S_IMODE(st_mode)
                nfs = bool(st_mode & 0o004)
            except OSError:
                keymode, nfs = None, False
            try:
                res = hive_cli.rotate_key(desc_path, agent_id=identity, keyfile=keyfile,
                                          nfs=nfs, mode=keymode)
            except (Exception, SystemExit) as e:  # noqa: BLE001 — engine raises SystemExit on refusals
                log.warning("[studio] hive rotate-key failed: %s", e, exc_info=True)
                return {"ok": False, "error": str(e)}
            _schedule_restart()      # descriptor+keyfile changed on disk → restart re-expands
            return {"ok": True, "identity": identity, "bundle": res["bundle"], "restarting": True}

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

        @app.post("/chat/session-end")
        async def chat_session_end(req: dict):
            """Summarize a finished /chat conversation → journal, and (if the caller identifies
            itself) build a durable dossier of the interlocutor the agent carries into later
            sessions. Body: {transcript:[{role,content}] | [str…], speaker_id?, speaker_name?,
            speaker_type?}. The generic host owns the turn-by-turn /chat; this closes the session
            (the twin/front-end bridge calls it on hang-up). Fail-open."""
            from nmem.agent_core import summarize_and_remember
            try:
                return await summarize_and_remember(
                    ctx.mem, ctx.backend, ctx.persona.agent_id,
                    req.get("transcript") or [],
                    interlocutor_id=req.get("speaker_id"),
                    interlocutor_name=req.get("speaker_name"),
                    interlocutor_type=req.get("speaker_type", "agent"))
            except Exception as e:  # noqa: BLE001
                return {"ok": False, "error": str(e)}

    async def _comms_factory(ctx):
        """CONFIG-DRIVEN peering for a thin studio appliance (host-shell-convergence-plan G2): build
        the agent's PeerExchange from its ``exchange:`` block with a GENERIC grounded ``on_challenge``
        (no bespoke voice code), start it, stash it on ctx.state for the delegation factory, and
        return the comms ChannelSink. No-op (None) unless ``exchange.enabled`` — so an agent with no
        ``exchange:`` block is byte-identical to today (no peer)."""
        excfg = config.get("exchange") or {}
        if not excfg.get("enabled", False):
            return None
        from nmem.agent_core.peer import PeerExchange, grounded_on_challenge
        comms_channel = (config.get("comms") or {}).get("channel", "")
        peer = PeerExchange(config, mem=ctx.mem, agent_id=persona.agent_id,
                            on_challenge=grounded_on_challenge(ctx.backend, persona),
                            comms_channel=comms_channel)
        await peer.start()   # no-op + returns None when exchange.enabled is false (guarded above anyway)
        ctx.state["peer"] = peer
        return peer.comms_sink

    async def _delegation_factory(ctx):
        """CONFIG-DRIVEN delegation seams for a thin appliance. Reads the ``delegation:`` block:
        ``{enabled, task_types:[{type,capability_class,description}], peer_channels:{target: channel}}``.
        Worker role = the generic AskExecutor over the declared task types (an ``ask`` answered via the
        agent's grounded cognition); requester role = channel_for from peer_channels. Shares the ONE
        peer the comms factory built. Returns {} unless enabled (create_agent_app + runtime gate again)."""
        dcfg = config.get("delegation") or {}
        if not dcfg.get("enabled", False):
            return {}
        peer = ctx.state.get("peer")
        if peer is None:
            log.warning("[studio] delegation.enabled but no peer (needs exchange.enabled) — skipping")
            return {}
        from nmem.agent_core.delegation import AskExecutor, TaskTypeRegistry, TaskTypeSpec
        specs = [TaskTypeSpec(t.get("type"), t.get("capability_class", "READ_ONLY"),
                              t.get("description", ""))
                 for t in (dcfg.get("task_types") or []) if t.get("type")]
        registry = TaskTypeRegistry(specs) if specs else None
        executor = AskExecutor(ctx.backend, persona) if registry else None
        peer_channels = dcfg.get("peer_channels") or {}   # {target_agent_id: channel}
        channel_for = (lambda t: peer_channels[t]) if peer_channels else None
        # approve stays None for now → READ_ONLY passes, MUTATING/HIGH_RISK fail-closed (safe). A
        # tiered gate adapter (ctx.state['gate']) is wired when we delegate mutating work (P2+).
        return {"peer": peer, "executor": executor, "registry": registry,
                "channel_for": channel_for, "approve": None}

    # The generic host owns lifespan/bootstrap/ops + the default /chat (runtime.converse — exactly
    # studio's old /chat). Studio injects only its executor (selector OR research runner) + the
    # sandbox lifecycle (resources_ready/started) + UI routes + auth + config-driven peering/delegation.
    app, ctx = create_agent_app(
        config, persona,
        build_executor=_build_executor, on_resources_ready=_on_resources_ready,
        pre_start=_pre_start, on_started=_on_started, on_shutdown=_on_shutdown,
        comms_factory=_comms_factory, delegation_factory=_delegation_factory,
        extra_health=_health_extras, extra_routes=_studio_routes,
        title=f"nmem agent · {persona.agent_id}")

    # ── EDIT support: rehydrate the wizard from the running config + reconfigure IN PLACE ──
    def _parse_env_file(path: str) -> dict:
        """Read a KEY=VALUE env file back to a dict (undo the shell-quoting _merge_env_file wrote)."""
        import shlex
        env: dict = {}
        if os.path.exists(path):
            for line in open(path):
                line = line.rstrip("\n")
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                try:
                    parts = shlex.split(v)
                except ValueError:
                    parts = [v]
                env[k.strip()] = parts[0] if parts else ""
        return env

    def _current_spec() -> dict:
        """The running agent's config as a wizard spec — NO secrets (the LLM key is referenced only by
        its env-var name). Capabilities come from the on-disk capabilities.env, structure from agent.yaml."""
        from nmem.agent_core import capabilities as caps
        cap_env = _parse_env_file(os.path.join(agent_dir, "capabilities.env"))
        brain = (config.get("backends") or {}).get("brain") or {}
        llm = {"provider": brain.get("provider", "openai"), "base_url": brain.get("url", ""),
               "model": brain.get("model", ""), "family": brain.get("family", "generic")}
        if brain.get("api_key_env"):
            llm["api_key_env"] = brain["api_key_env"]
        return {
            "agent_id": persona.agent_id,
            "enabled": sorted(caps.enabled_flags(env=cap_env)),
            "values": {f: cap_env[f] for f in caps.VALUE_FLAGS if cap_env.get(f)},
            "outward_actions": cap_env.get("NMEM_SYM_DRIVES_OUTWARD_ACTIONS", "explore"),
            "persona": {"agent_id": persona.agent_id,
                        "objectives": [s for _l, s in persona.objectives],
                        "world_entities": persona.world_entities},
            "llm": llm,
            "embedding": (config.get("nmem") or {}).get("embedding"),
            "actors": config.get("actors"),
            "autonomy": config.get("autonomy"),
            "hive": config.get("hive"),
            "computer_use": config.get("computer_use"),
        }

    async def _reconfigure(spec: dict) -> None:
        """Overwrite the running agent's config in place and restart into it — MEMORY PRESERVED (the
        DB is untouched). The agent id is immutable (it keys the DB + dir); write_agent doesn't touch
        secrets.env, so the stored LLM key + DB DSN survive, and a new key is merged only if entered."""
        import asyncio

        from nmem.agent_core.config_writer import write_agent
        if (spec or {}).get("agent_id", "").strip() != persona.agent_id:
            raise ValueError(f"cannot change the agent id (this appliance runs '{persona.agent_id}') — "
                             "reset the data volume to start a different agent")
        # write_agent rebuilds agent.yaml from the spec alone, so MERGE the running config's fields the
        # edit form can't represent — else an unrelated save silently drops them (a dropped autonomy
        # deny-list would re-permit denied tools; symbol_graph/pursuit/policy/belief would reset).
        cur_nmem = config.get("nmem") or {}
        cur_auto = config.get("autonomy") or {}
        new_auto = dict(spec.get("autonomy") or {})
        if cur_auto or new_auto:                        # form supplies `level`; allow/deny survive
            spec["autonomy"] = {**cur_auto, **new_auto}
        # Preserve hive MEMBERSHIP across an unrelated edit: the edit form round-trips only
        # mode/graph_role, so a plain save would drop descriptor/identity/keyfile/delegation and the
        # next boot would run solo (no peering/delegation). Keep them from the running config until
        # the wizard can round-trip them (P2).
        cur_hive = config.get("hive") or {}
        new_hive = dict(spec.get("hive") or {})
        for hk in ("descriptor", "identity", "keyfile", "delegation"):
            if hk not in new_hive and hk in cur_hive:
                new_hive[hk] = cur_hive[hk]
        if cur_hive or new_hive:
            spec["hive"] = new_hive
        for k, cur_val in (("belief", cur_nmem.get("belief")), ("policy", cur_nmem.get("policy")),
                           ("pursuit", config.get("pursuit")), ("symbol_graph", config.get("symbol_graph")),
                           ("goal_lifecycle", config.get("goal_lifecycle"))):
            if k not in spec and cur_val is not None:
                spec[k] = cur_val
        # Preserve the LLM + embedding credential REFERENCES when no new key was entered: the form maps
        # several providers to a generic family and would otherwise rewrite api_key_env to the wrong var,
        # orphaning the stored secret. Keep the running config's env-var name unless a new key is given.
        llm = dict(spec.get("llm") or {})
        cur_brain = (config.get("backends") or {}).get("brain") or {}
        if not llm.get("api_key") and cur_brain.get("api_key_env"):
            llm["api_key_env"] = cur_brain["api_key_env"]
            spec["llm"] = llm
        emb = spec.get("embedding")
        cur_emb = cur_nmem.get("embedding") or {}
        if isinstance(emb, dict) and not emb.get("api_key") and cur_emb.get("api_key_env"):
            emb["api_key_env"] = cur_emb["api_key_env"]
            spec["embedding"] = emb
        files = write_agent(agent_dir, spec)          # overwrites agent.yaml/capabilities.env/persona.yaml
        secrets = files.get("secrets") or {}
        if secrets:                                    # only when a NEW key was entered — merges, keeps DSN
            store_secrets(secrets)
        log.info("[studio] reconfigured %s in place (memory preserved); scheduling restart", persona.agent_id)
        asyncio.get_event_loop().call_later(1.5, _request_restart)

    from nmem.agent_core.studio import make_studio_router, studio_index_html
    app.include_router(make_studio_router(
        get_runtime=lambda: ctx.runtime, config_dir=DATA_DIR, store_secrets=store_secrets,
        allow_create=False, get_current_spec=_current_spec, reconfigure=_reconfigure))

    @app.get("/edit", response_class=HTMLResponse)
    async def edit_page():
        return studio_index_html()

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
