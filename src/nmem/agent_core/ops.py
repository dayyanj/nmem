"""Optional HTTP ops router — health + the /admin introspection endpoints.

A ready-made FastAPI router an HTTP agent can mount to get, for free, the exact
endpoints that make an nmem agent observable and validatable: a `/health`, and the
`/admin/*` hooks to force a consolidation, run a nightly synthesis or a dreamstate
cycle on demand, probe self-engineering recipes, and drive a recall end-to-end. These
are the same endpoints the reference agent's capability sweep leaned on — so any new agent is
inspectable and sweepable day one, and a fleet exposes one consistent ops surface.

**Optional + framework-free by design.** FastAPI is imported lazily INSIDE
``make_ops_router`` so the headless core never depends on a web framework — a voice or
CLI agent simply never calls this. The router is late-bound to the runtime via a
``get_runtime`` callable, since routers are registered at app-definition time (before
the runtime is started in the lifespan).

    from nmem.agent_core.ops import make_ops_router
    app.include_router(make_ops_router(lambda: my_runtime, extra_health=my_extras))
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable

log = logging.getLogger(__name__)


def make_ops_router(get_runtime: Callable, *, extra_health: Callable[[], dict] | None = None):
    """Build an APIRouter of health + /admin/* ops endpoints bound to a runtime.

    Args:
        get_runtime: returns the live :class:`AgentRuntime` (or None before start).
        extra_health: optional () -> dict merged into /health (agent-specific status,
            e.g. sandbox/peer/graph — the generic base already reports memory/brain/cognition).
    """
    from fastapi import APIRouter   # lazy — keeps the headless core framework-free
    router = APIRouter()

    def _rt():
        return get_runtime() if callable(get_runtime) else get_runtime

    @router.get("/health")
    async def health():
        rt = _rt()
        ok = rt is not None and rt.mem is not None
        brain = None
        try:
            be = rt.backend
            brain = {"provider": type(be).__name__, "model": be.model, "family": be.family}
        except Exception:  # noqa: BLE001
            pass
        base = {
            "status": "healthy" if ok else "starting",
            "memory": ok,
            "brain": brain,
            "cognition": rt.status if rt is not None else {"enabled": False},
        }
        if extra_health is not None:
            try:
                base.update(extra_health() or {})
            except Exception:  # noqa: BLE001
                log.warning("[ops] extra_health failed", exc_info=True)
        return base

    @router.get("/admin/drive_state")
    async def drive_state():
        """Read-only snapshot of the live homeostatic drive pressures (novelty/recall/coherence/…).
        These live in the running process (in-memory), so this is the only external window onto them
        — for the viz, debugging, and eval probes (e.g. novelty-vs-graph-maturity). ``{}`` when
        drives are disabled."""
        rt = _rt()
        if rt is None or getattr(rt, "bridge", None) is None:
            return {"ok": False, "error": "runtime not started"}
        try:
            dom = rt.bridge.dominant_drive()
            return {"ok": True, "drives": rt.bridge.drive_state(),
                    "dominant": {"drive": dom[0], "pressure": dom[1]} if dom else None}
        except Exception as e:  # noqa: BLE001
            log.warning("[ops] drive_state failed: %s", e, exc_info=True)
            return {"ok": False, "error": str(e)}

    @router.post("/admin/consolidate")
    async def consolidate():
        """Force a full consolidation cycle (promote journal→LTM + graph-extraction hooks)."""
        rt = _rt()
        if rt is None or rt.mem is None:
            return {"ok": False, "error": "runtime not started"}
        try:
            stats = await rt.mem._consolidator.run_full_cycle()
            return {"ok": True, "stats": str(stats)}
        except Exception as e:  # noqa: BLE001
            log.warning("[ops] consolidate failed: %s", e, exc_info=True)
            return {"ok": False, "error": str(e)}

    @router.post("/admin/nightly")
    async def nightly():
        """Run one nightly-synthesis cycle (carries commitment detection + self-engineering
        distillation + retrospective + policy alignment — NOT run by /admin/consolidate)."""
        rt = _rt()
        if rt is None or rt.mem is None:
            return {"ok": False, "error": "runtime not started"}
        try:
            stats = await rt.mem._consolidator.run_nightly_synthesis()
            return {"ok": True, "stats": str(stats)}
        except Exception as e:  # noqa: BLE001
            log.warning("[ops] nightly failed: %s", e, exc_info=True)
            return {"ok": False, "error": str(e)}

    @router.post("/admin/dreamstate")
    async def dreamstate():
        """Run one nmem-sym dreamstate cycle (explore, detect + bridge structural holes,
        generate hypotheses)."""
        rt = _rt()
        if rt is None or rt.graph is None:
            return {"ok": False, "error": "runtime not started (or no symbol graph)"}
        try:
            from nmem_sym.dreamstate import DreamstateScheduler
            g = rt.graph
            sched = DreamstateScheduler(
                g.pool, g._embedder,
                vllm_backends=getattr(g, "vllm_backends", None),
                vllm_model=getattr(g, "vllm_model", None))
            stats = await sched.run_once()
            return {"ok": True, "stats": str(stats)}
        except Exception as e:  # noqa: BLE001
            log.warning("[ops] dreamstate failed: %s", e, exc_info=True)
            return {"ok": False, "error": str(e)}

    @router.post("/admin/probe_recipes")
    async def probe_recipes(req: dict):
        """Read-only: which self-engineering context recipes would inject for a query.
        Body: {"query": "..."}."""
        rt = _rt()
        if rt is None or rt.mem is None:
            return {"ok": False, "error": "runtime not started"}
        query = (req or {}).get("query", "").strip()
        if not query:
            return {"ok": False, "error": "query required"}
        try:
            recipes = await rt.mem.self_engineering.find_recipe(query, agent_id=rt.agent_id)
            return {"ok": True, "query": query, "matched": len(recipes),
                    "recipes": [{"name": r.get("name"), "situation": r.get("situation"),
                                 "body": (r.get("body") or "")[:200]} for r in recipes]}
        except Exception as e:  # noqa: BLE001
            log.warning("[ops] probe_recipes failed: %s", e, exc_info=True)
            return {"ok": False, "error": str(e)}

    @router.post("/admin/seed_recall")
    async def seed_recall(req: dict):
        """Force a recall for a query end-to-end: seed a recall concern, let the drive
        fire, report what the memory.surfaced consumer received. Body: {"query","wait"}."""
        rt = _rt()
        if rt is None or rt.bridge is None or not getattr(rt.bridge, "seed_recall", None):
            return {"ok": False, "error": "recall drive not available (flag off or not started)"}
        query = (req or {}).get("query", "").strip()
        if not query:
            return {"ok": False, "error": "query required"}
        from nmem.agent_core import recall
        try:
            before = recall.buffer_size()
            seeded = rt.bridge.seed_recall(query, pressure=1.0)
            await asyncio.sleep(float((req or {}).get("wait", 4.0)))
            after = recall.buffer_size()
            return {"ok": True, "seeded": seeded, "buffer_before": before, "buffer_after": after,
                    "surfaced": after > before,
                    "surfaced_context": recall.context_for(query, consume=False)[:800]}
        except Exception as e:  # noqa: BLE001
            log.warning("[ops] seed_recall failed: %s", e, exc_info=True)
            return {"ok": False, "error": str(e)}

    return router
