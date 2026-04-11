"""
nmem REST API — FastAPI application factory.

Creates a FastAPI app with lifespan that initializes a MemorySystem on
startup and closes it on shutdown. The app is the single transport layer
that exposes nmem over HTTP.

Usage:
    from nmem.api.main import create_app
    app = create_app()  # uses NMEM_* env vars

    # Or with explicit config:
    from nmem import NmemConfig
    config = NmemConfig(database_url="sqlite+aiosqlite:///test.db")
    app = create_app(config=config)
"""

from __future__ import annotations

import logging
import time
import uuid
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from nmem import MemorySystem, NmemConfig
from nmem.api.errors import register_error_handlers

logger = logging.getLogger(__name__)


def get_mem(request: Request) -> MemorySystem:
    """Dependency: return the MemorySystem bound to this request.

    The MemorySystem is resolved from ``request.state.mem``, which is
    populated by the ``_inject_mem`` middleware on every request. For the
    default single-tenant nmem app, the middleware copies a single instance
    from ``app.state.mem``. Wrapper apps (e.g. nmem-cloud) can replace the
    middleware to inject a per-tenant instance instead, without having to
    use ``app.dependency_overrides``.

    Raises:
        RuntimeError: if ``request.state.mem`` is missing (usually because
            the app was not constructed via ``create_app`` or a custom
            wrapper failed to populate it).
    """
    mem = getattr(request.state, "mem", None)
    if mem is None:
        raise RuntimeError(
            "MemorySystem not found on request.state. Either the app "
            "was not constructed via create_app(), or a wrapping middleware "
            "failed to inject the tenant-specific MemorySystem."
        )
    return mem


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[dict]:
    """Initialize MemorySystem on startup, close on shutdown.

    Stores the instance on ``app.state.mem`` so the per-request middleware
    can copy it onto ``request.state.mem``.
    """
    config = getattr(app.state, "nmem_config", None) or NmemConfig()
    mem = MemorySystem(config)
    await mem.initialize()
    app.state.mem = mem
    app.state.nmem_config = config

    scope_info = (
        f", scope={config.project_scope}" if config.project_scope else ""
    )
    logger.info("nmem REST API initialized%s", scope_info)

    try:
        yield {"mem": mem, "config": config}
    finally:
        current = getattr(app.state, "mem", None)
        if current is not None:
            await current.close()
            app.state.mem = None
        logger.info("nmem REST API shut down")


def create_app(config: NmemConfig | None = None) -> FastAPI:
    """Create and configure the FastAPI app.

    Args:
        config: Optional explicit NmemConfig. If None, reads from env vars.

    Returns:
        Configured FastAPI application.
    """
    app = FastAPI(
        title="nmem API",
        description=(
            "REST API for nmem — cognitive memory for AI agents. "
            "6-tier hierarchy with hybrid search, consolidation, and "
            "cross-agent knowledge promotion."
        ),
        version="0.2.0",
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
    )

    # Inject config into app state for lifespan
    if config is not None:
        app.state.nmem_config = config

    # CORS — permissive by default, can be locked down via config
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Request ID middleware + timing
    @app.middleware("http")
    async def add_request_id(request: Request, call_next):
        rid = request.headers.get("X-Request-ID") or str(uuid.uuid4())
        request.state.request_id = rid
        request.state.start_time = time.monotonic()
        response = await call_next(request)
        duration_ms = int((time.monotonic() - request.state.start_time) * 1000)
        response.headers["X-Request-ID"] = rid
        response.headers["X-Response-Time-Ms"] = str(duration_ms)
        return response

    # MemorySystem injection — copies app.state.mem onto request.state.mem.
    # Wrapper apps (e.g. nmem-cloud) can replace this middleware to inject
    # a per-tenant MemorySystem resolved from a pool.
    @app.middleware("http")
    async def inject_mem(request: Request, call_next):
        mem = getattr(request.app.state, "mem", None)
        if mem is not None:
            request.state.mem = mem
        return await call_next(request)

    # Error handlers
    register_error_handlers(app)

    # Routes
    from nmem.api.routes import memory, admin, links, stats
    app.include_router(memory.router)
    app.include_router(admin.router)
    app.include_router(links.router)
    app.include_router(stats.router)

    return app


# Module-level app for uvicorn to import directly
app = create_app()
