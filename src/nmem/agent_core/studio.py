"""Optional HTTP studio router — the setup-wizard backend.

The four endpoints the nmem-studio wizard (docs/mockups/studio-wizard.html) posts to,
so a new agent can be configured, connection-tested, and stood up entirely from a web
UI — no editing files by hand:

    GET  /studio/catalog     the capability map + presets + groups the pills render from
    POST /studio/test-llm    build the posted brain, do a one-token chat, report ok/latency
    POST /studio/list-models ask a provider what models it serves (populate the dropdown)
    POST /studio/create       config_writer.write_agent + hand secrets to the host

**The key never leaves the server.** test-llm / list-models take the api_key in the POST
body, use it to talk to the provider, and NEVER echo it back — the response carries only
{ok, model, latency_ms, error} / a model-id list. create writes a secret-free agent.yaml
(config_writer strips the key) and passes the secrets to an injected ``store_secrets``
callable; the response reports only the secret env-var NAMES, never their values.

**Optional + framework-free, exactly like ops.py.** FastAPI is imported lazily INSIDE
``make_studio_router`` so the headless core never hard-depends on a web framework.

    from nmem.agent_core.studio import make_studio_router
    app.include_router(make_studio_router(config_dir="/data/agents", store_secrets=vault.put))
"""
from __future__ import annotations

import logging
import re
import time
from collections.abc import Callable

log = logging.getLogger(__name__)

# Providers we know how to build a backend for (mirrors agent_core.backend).
_PROVIDERS = ("openai", "anthropic")
# A path-safe agent id: alphanumeric start, then [A-Za-z0-9_], max 64. Blocks ../, absolute
# paths, dots, slashes, spaces, and shell/SQL metacharacters before it touches the filesystem.
# NO hyphen: agent_id also becomes an env-var-name component (<AGENT>_DB_DSN_ASYNC etc.), and a
# hyphen there is an invalid shell name that _merge_env_file would skip → missing DSN → boot loop.
_AGENT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_]{0,63}$")


def _build_test_backend(spec: dict):
    """Construct a backend DIRECTLY from a posted spec (not via build_backend, which layers
    michelle's MICHELLE_* env over config). ``spec`` = {provider, base_url, model, api_key,
    family, timeout?}. Raises ValueError on a bad/missing field (surfaced as ok:false)."""
    from nmem.agent_core.backend import AnthropicBackend, OpenAICompatibleBackend

    provider = (spec.get("provider") or "openai").strip().lower()
    model = (spec.get("model") or "").strip()
    api_key = spec.get("api_key") or ""
    family = (spec.get("family") or "generic").strip()
    base_url = (spec.get("base_url") or "").strip()
    timeout = float(spec.get("timeout") or 30.0)
    if not model:
        raise ValueError("model required")
    if provider == "anthropic":
        return AnthropicBackend(model=model, api_key=api_key, family=family or "claude",
                                base_url=base_url or "https://api.anthropic.com", timeout=timeout)
    if provider == "openai":
        return OpenAICompatibleBackend(base_url=base_url, model=model, api_key=api_key,
                                       family=family, timeout=timeout)
    raise ValueError(f"unknown provider {provider!r} (expected one of {_PROVIDERS})")


def make_studio_router(get_runtime: Callable | None = None, *, config_dir: str = ".",
                       store_secrets: Callable[[dict], None] | None = None,
                       start_agent: Callable | None = None):
    """Build an APIRouter of /studio/* wizard endpoints.

    Args:
        get_runtime: optional () -> live AgentRuntime (reserved for future studio ops).
        config_dir: base dir agents are written under (each agent → config_dir/<agent_id>).
        store_secrets: optional callable(secrets: dict[str,str]) the host uses to persist the
            LLM/embed keys to its secret store. If absent, create writes files but the caller
            must place secrets itself — the response still lists only key NAMES.
        start_agent: optional callable(spec, config_dir) (awaitable) to boot the new agent
            in-process after create. If absent, create only writes the config.
    """
    from fastapi import APIRouter   # lazy — keeps the headless core framework-free
    router = APIRouter()

    def _rt():
        return get_runtime() if callable(get_runtime) else get_runtime

    @router.get("/studio/catalog")
    async def studio_catalog():
        """The capability map the wizard renders: every flag enriched with its live pydantic
        description + default + current enabled-state, plus the dependency-complete presets and
        the group ordering. Correct-by-construction: pills, deps, and presets all come from the
        one map (agent_core.capabilities), so the UI can't drift from the runtime."""
        from nmem.agent_core import capabilities as caps
        return {
            "capabilities": caps.catalog(),
            "groups": [g for g in {c["group"]: None for c in caps.catalog()}],
            "presets": {name: {"label": p.get("label", name), "blurb": p.get("blurb", ""),
                               "flags": sorted(caps.preset_flags(name))}
                        for name, p in caps.PRESETS.items()},
        }

    @router.post("/studio/test-llm")
    async def studio_test_llm(req: dict):
        """Build the posted brain and do a one-token chat to prove the endpoint+key+model work.
        Body: {provider, base_url, model, api_key, family}. Returns {ok, model, latency_ms,
        error} — the api_key is USED here and never returned."""
        spec = req or {}
        try:
            be = _build_test_backend(spec)
        except Exception as e:  # noqa: BLE001 — bad spec is a user error, report it
            return {"ok": False, "model": spec.get("model"), "error": str(e)}
        t0 = time.monotonic()
        try:
            reply = await be.chat([{"role": "user", "content": "Reply with the single word: ok"}],
                                  temperature=0.0, max_tokens=8)
            dt = int((time.monotonic() - t0) * 1000)
            return {"ok": True, "model": be.model, "latency_ms": dt,
                    "sample": (reply or "").strip()[:80]}
        except Exception as e:  # noqa: BLE001
            dt = int((time.monotonic() - t0) * 1000)
            log.info("[studio] test-llm failed for model=%s: %s", spec.get("model"), e)
            return {"ok": False, "model": be.model, "latency_ms": dt, "error": _clean_error(e)}

    @router.post("/studio/list-models")
    async def studio_list_models(req: dict):
        """Ask the provider what models it serves, to populate the model dropdown. Body:
        {provider, base_url, api_key}. Returns {ok, models:[id,...]} — key used, never returned."""
        import httpx
        spec = req or {}
        provider = (spec.get("provider") or "openai").strip().lower()
        api_key = spec.get("api_key") or ""
        base_url = (spec.get("base_url") or "").strip().rstrip("/")
        try:
            if provider == "anthropic":
                url = f"{base_url or 'https://api.anthropic.com'}/v1/models"
                headers = {"x-api-key": api_key, "anthropic-version": "2023-06-01"}
            elif provider == "openai":
                if not base_url:
                    raise ValueError("base_url required")
                url = f"{base_url}/models"
                headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
            else:
                raise ValueError(f"unknown provider {provider!r}")
            async with httpx.AsyncClient(timeout=15.0) as client:
                r = await client.get(url, headers=headers)
                r.raise_for_status()
                data = r.json().get("data", [])
            models = sorted(m.get("id") for m in data if m.get("id"))
            return {"ok": True, "models": models}
        except Exception as e:  # noqa: BLE001
            log.info("[studio] list-models failed for %s: %s", provider, e)
            return {"ok": False, "models": [], "error": _clean_error(e)}

    @router.post("/studio/create")
    async def studio_create(spec: dict):
        """Stand up an agent from a wizard spec. Writes a secret-free agent.yaml + a correct-by-
        construction capabilities.env (config_writer auto-completes every flag's dependency
        closure) + persona.yaml under config_dir/<agent_id>, hands the secrets to the host's
        store, and (if start_agent was injected) boots it. Body = the config_writer spec
        (agent_id, enabled, persona, llm, embedding, ...). Returns {ok, agent_id, written,
        auto_enabled, secret_keys, started} — auto_enabled is the deps the writer pulled in."""
        import os

        from nmem.agent_core import capabilities as caps
        from nmem.agent_core.config_writer import write_agent

        agent_id = (spec or {}).get("agent_id", "").strip()
        if not agent_id:
            return {"ok": False, "error": "agent_id required"}
        # Path-safety: agent_id becomes a directory + a DB name + a memory-author key. Reject
        # anything that could escape config_dir (../, absolute, slashes, dots) or carry shell/SQL
        # metacharacters — a strict slug, validated BEFORE any os.path.join / write.
        if not _AGENT_ID_RE.match(agent_id):
            return {"ok": False, "error": "agent_id must be 1-64 chars of [A-Za-z0-9_], "
                                          "starting alphanumeric (no hyphens, slashes, dots, or spaces)"}
        # the writer auto-completes the dependency closure (so the on-disk env is always
        # dependency-complete); report what it pulled in beyond the user's explicit selection.
        enabled = {f for f in (spec.get("enabled") or []) if f in caps.CAPABILITIES}
        unknown = sorted(set(spec.get("enabled") or []) - enabled)
        closure = set(enabled)
        for f in list(enabled):
            closure |= caps.requires_closure(f)
        auto_enabled = sorted(closure - enabled)
        try:
            agent_dir = os.path.join(config_dir, agent_id)
            result = write_agent(agent_dir, spec)
        except Exception as e:  # noqa: BLE001
            log.warning("[studio] create failed for %s: %s", agent_id, e, exc_info=True)
            return {"ok": False, "error": str(e)}

        secrets = result.get("secrets") or {}
        stored = False
        if secrets and store_secrets is not None:
            try:
                store_secrets(secrets)
                stored = True
            except Exception as e:  # noqa: BLE001
                log.warning("[studio] store_secrets failed for %s: %s", agent_id, e, exc_info=True)

        started = False
        if start_agent is not None:
            try:
                res = start_agent(spec, agent_dir)
                if hasattr(res, "__await__"):
                    await res
                started = True
            except Exception as e:  # noqa: BLE001
                # start_agent (e.g. the appliance's provision+stage) may have removed the config
                # it couldn't stand up — so this is a FAILED create, not a create with started=false.
                log.warning("[studio] start_agent failed for %s: %s", agent_id, e, exc_info=True)
                return {"ok": False, "agent_id": agent_id,
                        "error": f"agent could not be started: {e}"}

        return {"ok": True, "agent_id": agent_id, "written": result.get("written", []),
                "auto_enabled": auto_enabled, "unknown_flags": unknown,
                "secret_keys": sorted(secrets),            # NAMES only, never values
                "secrets_stored": stored, "started": started}

    return router


def studio_index_html() -> str:
    """The productionised wizard SPA (built from docs/mockups/studio-wizard.html into
    studio_ui/index.html), read from the installed package so it ships in the wheel and is
    served straight from an editable checkout too."""
    from importlib.resources import files
    return (files("nmem.agent_core") / "studio_ui" / "index.html").read_text(encoding="utf-8")


def agent_dashboard_html() -> str:
    """The agent-mode dashboard SPA (studio_ui/dashboard.html) — a face over /health + the
    /admin/* ops endpoints. Served at ``/`` once the appliance is running its agent."""
    from importlib.resources import files
    return (files("nmem.agent_core") / "studio_ui" / "dashboard.html").read_text(encoding="utf-8")


def create_studio_app(*, config_dir: str = ".", store_secrets: Callable[[dict], None] | None = None,
                      start_agent: Callable | None = None, get_runtime: Callable | None = None):
    """A ready-to-serve FastAPI app = the /studio/* router + the wizard SPA at ``/``. This is
    what the studio docker image runs; an existing agent app can instead ``include_router`` just
    the router. FastAPI is imported lazily here, exactly like the router, so the headless core
    stays framework-free."""
    from fastapi import FastAPI
    from fastapi.responses import HTMLResponse

    app = FastAPI(title="nmem-studio")
    app.include_router(make_studio_router(get_runtime, config_dir=config_dir,
                                          store_secrets=store_secrets, start_agent=start_agent))
    cache: dict = {}

    @app.get("/", response_class=HTMLResponse)
    async def index():
        if "html" not in cache:
            cache["html"] = studio_index_html()
        return cache["html"]

    return app


def _clean_error(e: Exception) -> str:
    """A user-facing error string that never leaks an Authorization header / api_key.
    httpx errors can carry the request; keep only the status + provider message."""
    import httpx
    if isinstance(e, httpx.HTTPStatusError):
        body = ""
        try:
            body = e.response.text[:300]
        except Exception:  # noqa: BLE001
            pass
        return f"HTTP {e.response.status_code}: {body}".strip()
    if isinstance(e, httpx.RequestError):
        return f"connection error: {type(e).__name__}"
    return str(e)[:300]
