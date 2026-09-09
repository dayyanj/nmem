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


def test_graph_global_cycles_gated_on_keeper_state():
    """B-ii step-3 (graph-global half): ``runs_graph_global`` decides whether THIS process runs the
    clustering + dreamstate cycles — it's the value fed to BridgeConfig cluster_on_full_cycle /
    dreamstate_on_nightly. Solo/isolated ⇒ always (unchanged); shared_world ⇒ keeper-only. Tested
    on the property directly (it reads only hive + is_keeper) so no DB/boot is needed."""
    from nmem.agent_core.runtime import AgentRuntime

    def _rt(mode, is_keeper):
        rt = AgentRuntime.__new__(AgentRuntime)      # bypass __init__: gate reads only these two
        rt.hive = HiveConfig.from_dict({"mode": mode, "agent_id": "scout",
                                        "graph_role": "keeper" if is_keeper else "contributor"})
        rt.is_keeper = is_keeper
        return rt

    assert _rt("isolated", False).runs_graph_global is True     # solo appliance = today, no change
    assert _rt("shared_world", True).runs_graph_global is True  # elected keeper runs them
    assert _rt("shared_world", False).runs_graph_global is False  # contributor SUPPRESSED (the point)


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
