"""nmem-viz event bridge — stream an agent's live cognition to a nmem-viz hub.

Graduated from the reference agent's ``service/viz_bridge.py``, but pointed at nmem-viz's ``POST /ingest``
instead of hosting a WebSocket server: the agent becomes a plain HTTP client, so a bundled
appliance needs no extra port, no daemon thread, and no relay wiring on the viz side. The viz
server still serves the full graph SNAPSHOT from the shared database; this bridge adds the LIVE
deltas on top.

Fully additive + best-effort. It attaches only when ``NMEM_VIZ_INGEST_URL`` is set and
``NMEM_SYM_VIZ_ENABLED`` is truthy; a failed POST is dropped, so viz can never perturb cognition.

Flag note: ``init_viz`` reads ``NMEM_SYM_VIZ_ENABLED`` **tolerantly** (1/true/yes/on) and then
force-enables the nmem-sym emitter via ``viz_enable()`` — so it does NOT depend on that module's
own ``== "1"`` env gate (which only accepts the literal ``1``). That keeps the studio's boolean
capability flags (``true``) working end-to-end without editing nmem-sym.
"""
from __future__ import annotations

import logging
import os
import time

log = logging.getLogger(__name__)

_OFF = {"", "0", "false", "no", "off"}


def _on(val: str | None) -> bool:
    return (val or "").strip().lower() not in _OFF


class VizBridge:
    """Forwards nmem-sym cognition events (and, if available, nmem memory-tier events) to a
    nmem-viz hub's /ingest endpoint, stamping each with the agent's system id."""

    def __init__(self, ingest_url: str, system_id: str, *, maxsize: int = 1000):
        import asyncio
        import httpx
        self.ingest_url = ingest_url
        self.system_id = system_id
        self._client = httpx.AsyncClient(timeout=2.0)
        self._sym_handler = None
        self._closed = False
        # Emit on a BOUNDED background queue, NEVER inline: nmem's MemorySystem._emit awaits its
        # handlers, so awaiting the POST here would block every journal/LTM/shared write on the viz
        # HTTP round-trip (up to the 2s timeout) whenever the viz hub stalls. Handlers just enqueue
        # (instant); one drain task does the I/O; a full queue drops events rather than back-pressure
        # cognition.
        self._q: asyncio.Queue = asyncio.Queue(maxsize=maxsize)
        self._drain_task = asyncio.ensure_future(self._drain())

    def _enqueue(self, evt: dict) -> None:
        try:
            self._q.put_nowait(evt)
        except Exception:  # noqa: BLE001 — QueueFull → drop (best-effort viz)
            pass

    async def _drain(self) -> None:
        while True:
            evt = await self._q.get()
            try:
                if evt is None:                    # shutdown sentinel
                    return
                await self._client.post(self.ingest_url, json=evt)
            except Exception:  # noqa: BLE001 — viz is best-effort; never surface into cognition
                pass
            finally:
                self._q.task_done()

    def _sym(self, event_type: str, data: dict):
        # Enqueue + return None so nmem-sym's viz_emit doesn't create_task a coroutine (we own the I/O).
        self._enqueue({"ts": time.time(), "source": "nmem-sym", "type": event_type,
                       "data": {**(data or {}), "_system": self.system_id}})

    def _make_nmem(self, event_type: str):
        async def handler(data):                   # awaited inline by mem._emit → must be instant
            payload = dict(data) if isinstance(data, dict) else {}
            payload["_system"] = self.system_id
            self._enqueue({"ts": time.time(), "source": "nmem", "type": event_type, "data": payload})
        return handler

    def attach(self, mem) -> None:
        from nmem_sym.viz_events import viz_enable, viz_on
        viz_enable()                      # force-on; do not rely on the module's == "1" env gate
        viz_on(self._sym)
        self._sym_handler = self._sym
        # nmem memory-tier events, if this MemorySystem exposes an event hook (labelled nodes)
        if hasattr(mem, "on"):
            for et in ("journal.added", "ltm.saved", "shared.saved"):
                try:
                    mem.on(et)(self._make_nmem(et))
                except Exception as e:  # noqa: BLE001
                    log.debug("[viz] could not hook nmem event %s: %s", et, e)

    async def close(self) -> None:
        import asyncio
        self._closed = True
        if self._sym_handler is not None:
            try:
                from nmem_sym.viz_events import viz_off
                viz_off(self._sym_handler)
            except Exception:  # noqa: BLE001
                pass
        self._enqueue(None)               # stop the drain
        try:
            await asyncio.wait_for(self._drain_task, timeout=3.0)
        except Exception:  # noqa: BLE001
            self._drain_task.cancel()
        try:
            await self._client.aclose()
        except Exception:  # noqa: BLE001
            pass


def init_viz(runtime, *, ingest_url: str | None = None, system_id: str | None = None):
    """Attach a :class:`VizBridge` to a STARTED runtime if configured, else return None.
    Configured = ``NMEM_VIZ_INGEST_URL`` set + ``NMEM_SYM_VIZ_ENABLED`` truthy. Non-fatal."""
    ingest_url = ingest_url or os.environ.get("NMEM_VIZ_INGEST_URL")
    if not ingest_url or not _on(os.environ.get("NMEM_SYM_VIZ_ENABLED")):
        return None
    try:
        import nmem_sym.viz_events  # noqa: F401 — ensure the emitter is importable
    except Exception:  # noqa: BLE001
        log.info("[viz] nmem-sym viz_events unavailable — viz disabled")
        return None
    bridge = VizBridge(ingest_url, system_id or runtime.agent_id)
    bridge.attach(runtime.mem)
    log.info("[viz] streaming live deltas to %s (system=%s)", ingest_url, bridge.system_id)
    return bridge
