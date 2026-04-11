"""Shared FastAPI dependencies for the nmem REST API.

Lives in its own module so route files can import ``get_mem`` without
pulling in :mod:`nmem.api.main` (which triggers ``create_app()`` at
import time and causes a circular import when consumed by wrapper
services like nmem-cloud).
"""

from __future__ import annotations

from fastapi import Request

from nmem import MemorySystem


def get_mem(request: Request) -> MemorySystem:
    """Return the MemorySystem bound to this request.

    Resolved from ``request.state.mem``, which is populated by the
    ``inject_mem`` middleware on the default single-tenant app (see
    :mod:`nmem.api.main`). Wrapper apps may install their own middleware
    that sets ``request.state.mem`` to a per-tenant instance before the
    handler runs.

    Raises:
        RuntimeError: if ``request.state.mem`` is missing (usually because
            no middleware populated it).
    """
    mem = getattr(request.state, "mem", None)
    if mem is None:
        raise RuntimeError(
            "MemorySystem not found on request.state. Either the app was "
            "not constructed via create_app(), or a wrapping middleware "
            "failed to inject the tenant-specific MemorySystem."
        )
    return mem
