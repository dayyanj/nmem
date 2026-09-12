"""The viz bridge must be fire-and-forget: nmem's MemorySystem._emit awaits its handlers, so a
viz POST awaited inline would block every memory write on the HTTP round-trip. The bridge enqueues
onto a bounded background queue instead — the handler returns instantly even when the hub stalls."""
import asyncio
import time

import pytest

pytest.importorskip("httpx")


def test_nmem_handler_never_blocks_on_a_stalled_viz_hub():
    from nmem.agent_core.viz import VizBridge

    async def go():
        bridge = VizBridge("http://viz.invalid/ingest", "scout", maxsize=100)

        # make the actual POST hang; the handler must NOT wait on it.
        async def _hang(*a, **k):
            await asyncio.sleep(10)
        bridge._client.post = _hang  # type: ignore[method-assign]

        # The bridge now subscribes to the "*" wildcard: _nmem_star(event, data) is called
        # inline by mem._emit for EVERY core event and must only enqueue (never block).
        t0 = time.monotonic()
        for _ in range(50):
            bridge._nmem_star("ltm.saved", {"id": 1, "title": "x"})   # called inline by mem._emit
        elapsed = time.monotonic() - t0
        assert elapsed < 0.5, f"handler blocked ({elapsed:.2f}s) — must only enqueue"
        assert bridge._q.qsize() == 50                 # events buffered, not lost
        # a full queue drops rather than back-pressures cognition
        bridge2 = VizBridge("http://viz.invalid/ingest", "scout", maxsize=3)
        for _ in range(20):
            bridge2._sym("drive.fired", {"k": 1})
        assert bridge2._q.qsize() <= 3
        await bridge.close()
        await bridge2.close()

    asyncio.run(go())


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
