"""Hive B-ii/B-iii (schema-independent): HiveConfig presets + the single-keeper advisory lock.

The lock is the safety-critical piece (docs/path-b-hive-scoping-design.md §11.1/§12.1): it MUST
elect exactly one keeper across processes, on a dedicated connection that doesn't drop the lock.
The two-connection assertion needs a real Postgres, so it runs only when NMEM_TEST_PG_DSN is set
(the live validation on dj-ai exercises it); the pure-config tests always run."""
import asyncio
import os

import pytest

from nmem.agent_core.hive import HiveConfig, become_keeper, keeper_key


def test_hiveconfig_defaults_isolated():
    h = HiveConfig.from_dict(None)
    assert h.mode == "isolated" and not h.is_shared_world and not h.wants_keeper
    assert h.memory == "own" and h.graph == "own"       # isolated ⇒ both own = today


def test_hiveconfig_shared_world_preset_shares_both():
    h = HiveConfig.from_dict({"mode": "shared_world", "agent_id": "scout", "graph_role": "keeper"})
    assert h.is_shared_world and h.wants_keeper
    assert h.memory == "shared" and h.graph == "shared"  # §11.2: shared_world ⇒ both DSNs shared


def test_hiveconfig_intermediate_override():
    # shared graph but own memory is a valid intermediate (§11.2 nuance)
    h = HiveConfig.from_dict({"mode": "shared_world", "graph": "shared", "memory": "own",
                              "graph_role": "contributor"})
    assert h.graph == "shared" and h.memory == "own" and not h.wants_keeper


def test_keeper_key_is_deterministic_across_processes():
    # MUST NOT use Python's salted hash() — same identifier ⇒ same 64-bit key, always.
    assert keeper_key("agent_nmem") == keeper_key("agent_nmem")
    assert keeper_key("graph_a") != keeper_key("graph_b")
    k = keeper_key("x")
    assert -(2**63) <= k < 2**63                          # fits a Postgres bigint


class _FakeLock:
    """Stand-in for KeeperLock: alive()/verify() honor a settable flag (default healthy)."""
    def __init__(self, alive=True):
        self.held = alive
        self._alive = alive
    def alive(self):
        return self._alive
    async def verify(self):
        self.held = self._alive
        return self._alive


def test_runs_graph_global_is_tied_to_live_lock_possession():
    """B-ii step-3: ``runs_graph_global`` is the per-cycle keeper gate (fed to the bridge callable).
    Isolated ⇒ always True (solo appliance = today). shared_world ⇒ True ONLY while we hold a LIVE lock
    — NOT a stale is_keeper flag — so a keeper whose lock connection dies de-authorizes immediately,
    which is what stops two keepers after a session loss (codex P1). No DB/boot needed."""
    from nmem.agent_core.runtime import AgentRuntime

    def _rt(mode, lock):
        rt = AgentRuntime.__new__(AgentRuntime)
        rt.hive = HiveConfig.from_dict({"mode": mode, "agent_id": "scout",
                                        "graph_role": "keeper" if lock else "contributor"})
        rt._keeper_lock = lock
        return rt

    assert _rt("isolated", None).runs_graph_global is True              # solo = today, no lock needed
    assert _rt("shared_world", _FakeLock(alive=True)).runs_graph_global is True    # holds a live lock
    assert _rt("shared_world", None).runs_graph_global is False         # contributor, no lock
    assert _rt("shared_world", _FakeLock(alive=False)).runs_graph_global is False  # lock conn died → de-authorized


def test_keeper_watch_loop_takes_over_when_lock_frees(monkeypatch):
    """Live failover (tick A): a willing process that doesn't hold the lock re-acquires it via the watch
    tick; on acquire, runs_graph_global flips True (the bridge callable then re-activates the dormant
    hooks, no restart). Patched become_keeper (imported at call time) so no live PG is needed."""
    import asyncio
    from nmem.agent_core.runtime import AgentRuntime

    rt = AgentRuntime.__new__(AgentRuntime)
    rt.is_keeper = False
    rt._keeper_lock = None
    rt.status = {"keeper": False}
    rt.hive = HiveConfig.from_dict({"mode": "shared_world", "agent_id": "s", "graph_role": "keeper"})
    rt._keeper_dsn, rt._keeper_key = "postgresql://x/graph", 123

    calls = {"n": 0}
    async def fake_become(dsn, key):
        calls["n"] += 1
        return None if calls["n"] < 2 else _FakeLock(alive=True)   # held on 1st poll, freed by the 2nd

    monkeypatch.setattr("nmem.agent_core.hive.become_keeper", fake_become)

    async def go():
        task = asyncio.ensure_future(rt._keeper_watch_loop(0.001))
        for _ in range(200):
            if rt.is_keeper:
                break
            await asyncio.sleep(0.005)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(asyncio.wait_for(go(), timeout=3))
    assert rt.is_keeper is True and rt._keeper_lock is not None      # took over live
    assert rt.status["keeper"] is True                              # published status updated (P2)
    assert rt.runs_graph_global is True                             # now authorized


def test_keeper_watch_loop_revokes_authority_when_its_lock_dies(monkeypatch):
    """codex P1: a keeper whose dedicated session dies (verify() False) must REVOKE its own authority
    so it + any taker aren't both running global maintenance. Here re-acquire also fails (lock taken),
    so it ends up a de-authorized contributor — runs_graph_global False, status corrected."""
    import asyncio
    from nmem.agent_core.runtime import AgentRuntime

    rt = AgentRuntime.__new__(AgentRuntime)
    rt.is_keeper = True
    rt._keeper_lock = _FakeLock(alive=False)     # our lock's connection has died
    rt.status = {"keeper": True}
    rt.hive = HiveConfig.from_dict({"mode": "shared_world", "agent_id": "s", "graph_role": "keeper"})
    rt._keeper_dsn, rt._keeper_key = "postgresql://x/graph", 123

    async def fake_become(dsn, key):
        return None                              # someone else holds the freed lock now

    monkeypatch.setattr("nmem.agent_core.hive.become_keeper", fake_become)

    async def go():
        task = asyncio.ensure_future(rt._keeper_watch_loop(0.001))
        for _ in range(200):
            if not rt.is_keeper:
                break
            await asyncio.sleep(0.005)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(asyncio.wait_for(go(), timeout=3))
    assert rt.is_keeper is False and rt._keeper_lock is None        # revoked
    assert rt.status["keeper"] is False                            # no stale /health role (P2)
    assert rt.runs_graph_global is False                           # global maintenance halted here


def test_lifecycle_loop_drives_run_goal_lifecycle_owner_scoped():
    """B-ii Decision 2: _lifecycle_loop drives the agent's OWN goal-lifecycle via the nmem-sym
    entrypoint, owner-scoped. Isolated ⇒ owner_agent=None (the exact unscoped set dreamstate ran
    today — the §18 invariant). A fake bridge records the calls; no DB/boot needed."""
    import asyncio
    from nmem.agent_core.runtime import AgentRuntime

    rt = AgentRuntime.__new__(AgentRuntime)
    rt.hive = HiveConfig.from_dict({"mode": "isolated"})     # isolated ⇒ agent_id None ⇒ owner_agent=None

    calls = []

    class _Bridge:
        async def run_goal_lifecycle(self, *, owner_agent):
            calls.append(owner_agent)
            return {}

    rt.bridge = _Bridge()

    async def go():
        task = asyncio.ensure_future(rt._lifecycle_loop(0.001))
        for _ in range(200):
            if calls:
                break
            await asyncio.sleep(0.005)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(asyncio.wait_for(go(), timeout=3))
    assert calls and all(o is None for o in calls)          # ticked, unscoped (isolated=today)


@pytest.mark.skipif(not os.environ.get("NMEM_TEST_PG_DSN"),
                    reason="needs a Postgres (set NMEM_TEST_PG_DSN)")
def test_single_keeper_by_construction_and_failover():
    dsn = os.environ["NMEM_TEST_PG_DSN"]
    key = keeper_key("hive-test-graph")

    async def go():
        first = await become_keeper(dsn, key)
        assert first is not None and first.held            # 1st wins
        second = await become_keeper(dsn, key)
        assert second is None                              # 2nd cannot while 1st holds (one keeper)
        await first.release()                              # keeper dies / steps down
        third = await become_keeper(dsn, key)
        assert third is not None and third.held            # role frees → another acquires (failover)
        await third.release()
    asyncio.run(go())


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
