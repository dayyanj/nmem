"""`create_agent_app` — the reusable, config-driven agent HOST (agent-mode FastAPI).

Extracted from `studio_server.build_agent_app` (host-shell-convergence-plan.md Step 1): the generic
FastAPI host that boots an agent on `AgentRuntime` from an already-loaded `(config, persona)`. The
studio APPLIANCE (wizard/provision/restart, SessionAuth, dashboard, `/tools`, `/act`) is ONE caller
that layers its features on top; a bespoke agent (michelle, DJ) is another caller that injects its
own executor/comms/routes. Config DISCOVERY (AGENT_CONFIG vs the studio data-dir scan vs DJAI_CONFIG)
stays in the caller — this host is source-agnostic.

Lifecycle — all on uvicorn's serving loop (asyncpg binds to it), inside the FastAPI lifespan:

    build mem → build graph → build backend        (host OWNS them; closed on shutdown)
      → on_resources_ready(ctx)                     (agent resource setup, e.g. sandbox config)
      → comms_sink = comms_factory(ctx)             (needs live mem + the agent's on_challenge)
      → construct AgentRuntime(mem, graph, backend, build_executor, comms_sink, …)
      → pre_start(ctx)                              (async actor-registry assembly, if any)
      → runtime.start()
      → on_started(ctx)                             (e.g. register a catch-all capability)
      → init_viz(runtime)
    … serve …
    shutdown reverses: on_shutdown → viz.close → runtime.stop → resources close.

`AgentRuntime` can build mem/graph/backend itself, but a host that has a `comms_factory` needs `mem`
BEFORE runtime construction (the peer is built with live memory + an `on_challenge` cognition
callback). So this host always builds them and passes them in — one path that serves both the
pure-thinker and the acting/peering agent.
"""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

log = logging.getLogger("nmem.host")


@dataclass
class HostContext:
    """Handed to every host hook. `runtime` is None until it's constructed (mid-lifespan), so hooks
    that run before construction (on_resources_ready, comms_factory) must not read it; those after
    (pre_start via ctx, on_started, on_shutdown, routes) can. `state` is the host's scratch dict
    (e.g. a caller stashing its assembled actor registry or its peer handle)."""
    config: dict
    persona: Any
    mem: Any = None
    graph: Any = None
    backend: Any = None
    runtime: Any = None
    viz: Any = None
    state: dict = field(default_factory=dict)


def create_agent_app(
    config: dict,
    persona: Any,
    *,
    build_executor: Callable[[HostContext, Any], Any] | None = None,
    build_proposal: Callable | None = None,
    comms_factory: Callable[[HostContext], Awaitable[Any]] | None = None,
    skill_chronic: Callable[[dict], Awaitable[None]] | None = None,
    on_resources_ready: Callable[[HostContext], Awaitable[None]] | None = None,
    pre_start: Callable[[HostContext], Awaitable[None]] | None = None,
    on_started: Callable[[HostContext], Awaitable[None]] | None = None,
    on_shutdown: Callable[[HostContext], Awaitable[None]] | None = None,
    on_stopped: Callable[[HostContext], Awaitable[None]] | None = None,
    chat_handler: Callable[[HostContext, dict], Awaitable[dict]] | None = None,
    extra_health: Callable[[HostContext], dict] | None = None,
    routers: tuple = (),
    extra_routes: Callable[[Any, HostContext], None] | None = None,
    title: str | None = None,
):
    """Build the agent-mode FastAPI app. Returns `(app, ctx)`; the caller may mount more onto `app`
    (studio adds auth/dashboard/`/tools`/`/act`) and reads `ctx.runtime` after startup.

    Hooks (all optional): `build_executor(ctx, bridge)->executor` (the runtime's actuator — michelle's
    direct ReferenceRunner, studio's selector); `comms_factory(ctx)->comms_sink` (built with live mem);
    `build_proposal`/`skill_chronic` (passed through to AgentRuntime); the async lifecycle
    `on_resources_ready`→`pre_start`→`on_started` (startup) and `on_shutdown`→`on_stopped` (teardown,
    the latter running AFTER `runtime.stop()` for cleanup that must outlive the loops); `chat_handler(
    ctx, req)->dict` (overrides the default `/chat` = `runtime.converse(message, history)`);
    `extra_health(ctx)->dict` (merged into `/health`); `routers`/`extra_routes(app, ctx)`."""
    from fastapi import FastAPI

    from nmem.agent_core import (AgentRuntime, build_backend, build_memory,
                                 build_symbol_graph, init_viz, make_ops_router)

    ctx = HostContext(config=config, persona=persona)
    agent_id = getattr(persona, "agent_id", None) or config.get("db", {}).get("config_key", "agent")

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        try:
            # Resources — host-owned, on the serving loop. Order matters: nmem BEFORE the symbol
            # graph (a sym migration ALTERs an nmem-owned table); the graph shares mem's embedder.
            ctx.mem = await build_memory(config)
            if (config.get("symbol_graph", {}) or {}).get("enabled", True):
                ctx.graph = await build_symbol_graph(config, embedder=getattr(ctx.mem, "embedding", None))
            ctx.backend = build_backend(config)
            if on_resources_ready:
                await on_resources_ready(ctx)

            comms_sink = await comms_factory(ctx) if comms_factory else None

            ctx.runtime = AgentRuntime(
                config, persona, mem=ctx.mem, graph=ctx.graph, backend=ctx.backend,
                build_executor=((lambda bridge: build_executor(ctx, bridge)) if build_executor else None),
                build_proposal=build_proposal, comms_sink=comms_sink, skill_chronic=skill_chronic)

            if pre_start:                              # e.g. async actor-registry assembly
                await pre_start(ctx)
            await ctx.runtime.start()
            if on_started:                             # e.g. register a catch-all capability
                await on_started(ctx)
            ctx.viz = init_viz(ctx.runtime)
            log.info("[host] agent %s up: %s", agent_id, ctx.runtime.status)
            yield
        finally:
            log.info("[host] agent %s shutting down", agent_id)
            # Phased teardown; every phase is independent so one failure can't skip the rest.
            # on_shutdown (agent pre-teardown, e.g. peer.close) → viz → runtime.stop → on_stopped
            # (agent cleanup that must run AFTER the loops stop) → resources (host-owned mem/graph).
            async def _viz_close():
                if ctx.viz is not None:
                    await ctx.viz.close()

            async def _runtime_stop():
                if ctx.runtime is not None:
                    await ctx.runtime.stop()

            async def _mem_close():
                # Per-resource guard: a failed graph close must NOT leak mem (codex Step-1 P2).
                for obj in (ctx.graph, ctx.mem):
                    if obj is not None and hasattr(obj, "close"):
                        try:
                            await obj.close()
                        except Exception as e:  # noqa: BLE001
                            log.warning("[host] resource close failed: %s", e)

            for name, closer in (("on_shutdown", (lambda: on_shutdown(ctx)) if on_shutdown else None),
                                 ("viz", _viz_close), ("runtime", _runtime_stop),
                                 ("on_stopped", (lambda: on_stopped(ctx)) if on_stopped else None),
                                 ("resources", _mem_close)):
                if closer is None:
                    continue
                try:
                    await closer()
                except Exception as e:  # noqa: BLE001
                    log.warning("[host] shutdown %s failed: %s", name, e)

    app = FastAPI(title=title or f"nmem agent · {agent_id}", lifespan=lifespan)

    # /health + /admin/* — late-bound to the runtime built in the lifespan. make_ops_router wants a
    # zero-arg `() -> dict`; the host contract is `extra_health(ctx) -> dict`, so bind ctx here.
    _health = (lambda: extra_health(ctx)) if extra_health is not None else None
    app.include_router(make_ops_router(lambda: ctx.runtime, extra_health=_health))

    if chat_handler is not None:
        @app.post("/chat")
        async def chat(req: dict):
            if ctx.runtime is None:
                return {"ok": False, "error": "runtime not ready"}
            return await chat_handler(ctx, req)
    else:
        @app.post("/chat")
        async def chat(req: dict):
            """One grounded turn via `runtime.converse` (message + optional history)."""
            msg = (req or {}).get("message", "").strip()
            if not msg:
                return {"ok": False, "error": "message required"}
            if ctx.runtime is None:
                return {"ok": False, "error": "runtime not ready"}
            try:
                reply = await ctx.runtime.converse(msg, history=(req or {}).get("history") or [])
                return {"ok": True, "reply": reply}
            except Exception as e:  # noqa: BLE001
                log.warning("[host] chat failed: %s", e, exc_info=True)
                return {"ok": False, "error": str(e)}

    for r in routers:
        app.include_router(r)
    if extra_routes is not None:
        extra_routes(app, ctx)

    return app, ctx
