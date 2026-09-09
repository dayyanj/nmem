"""Hive B-i acceptance — agency-scoping isolation on ONE shared graph DB.

The gate for Path B's *bounded half* (docs/path-b-hive-scoping-design.md §7): two agents'
agency must never cross when they share one graph. This file covers the SHARP edge — the
`SymbolGoalStore` owner filter and the once-destructive table-wide `recover_orphaned`
(§3.3 / §10.2, the single most dangerous line in B-i). Companion to `test_agent_core_hive.py`
(which covers the keeper/HiveConfig half, B-ii/B-iii).

**TEST-FIRST.** It is written against the agreed target signature
`SymbolGoalStore(pool, *, source_type, owner_agent=None)` (§10.1). It is RED until the
agent_core session lands that owner filter; the `owner_agent` column it relies on already
exists (nmem-sym migration 017). When these three go green, sales_head may go `shared_world`.

Needs a real Postgres with an nmem-sym schema → gated on `NMEM_TEST_PG_DSN` (same knob as
`test_agent_core_hive.py`). Uses distinctive owner names + cleans up after itself, so it is
safe to run against a live/shared graph DB.

TODO (once the `SymbolBridge(agent_id=…)` seam lands — §3.2): extend with the full §7
assertions — A never sees B's concerns / pending_utterances / obligations / episodes, and a
node A extracts IS visible to B (graph reads are shared, agency reads are not).
"""
from __future__ import annotations

import asyncio
import contextlib
import os

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("NMEM_TEST_PG_DSN"),
    reason="hive agency-scope acceptance needs a real Postgres (set NMEM_TEST_PG_DSN)")

_A = "hive_test_owner_A"
_B = "hive_test_owner_B"


@contextlib.asynccontextmanager
async def _pool():
    import asyncpg
    dsn = os.environ["NMEM_TEST_PG_DSN"].replace("+asyncpg", "")   # asyncpg wants the bare scheme
    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=2)
    try:
        if not await pool.fetchval("SELECT to_regclass('symbol_goals')"):
            pytest.skip("symbol_goals absent — run nmem-sym migrations on the test DB first")
        # Idempotent: mirrors migration 017 so the test is self-sufficient on a not-yet-migrated DB.
        await pool.execute("ALTER TABLE symbol_goals ADD COLUMN IF NOT EXISTS owner_agent TEXT")
        await _clean(pool)
        yield pool
    finally:
        with contextlib.suppress(Exception):
            await _clean(pool)
        await pool.close()


async def _clean(pool) -> None:
    await pool.execute("DELETE FROM symbol_goals WHERE owner_agent = ANY($1::text[])", [_A, _B])


async def _goal(pool, objective: str, owner: str, status: str = "pending") -> int:
    return await pool.fetchval(
        "INSERT INTO symbol_goals (objective, status, source_type, owner_agent) "
        "VALUES ($1, $2, 'drive_intent', $3) RETURNING id", objective, status, owner)


def _store(pool, owner):
    from nmem.agent_core.goal_store import SymbolGoalStore
    return SymbolGoalStore(pool, source_type="drive_intent", owner_agent=owner)


# ── the assertions (each self-contained via asyncio.run — no pytest-asyncio dependency) ──

def test_actionable_is_owner_scoped():
    async def go():
        async with _pool() as pool:
            ga = await _goal(pool, "A's review goal", _A)
            gb = await _goal(pool, "B's review goal", _B)
            ids = {g.id for g in await _store(pool, _A).actionable(50)}
            assert ga in ids, "A must see its own goal"
            assert gb not in ids, "A must NOT see B's goal on the shared graph"
    asyncio.run(go())


def test_claim_is_owner_scoped():
    async def go():
        async with _pool() as pool:
            ga = await _goal(pool, "A", _A)
            gb = await _goal(pool, "B", _B)
            store_a = _store(pool, _A)
            assert await store_a.claim(ga) is True, "A can claim its own goal"
            assert await store_a.claim(gb) is False, "A must NOT be able to claim B's goal"
    asyncio.run(go())


def test_recover_orphaned_never_touches_another_agents_goals():
    """THE destructive one. A's startup recovery must reset only A's stranded goals — never
    B's in-flight work. Table-wide recover_orphaned (today) fails this."""
    async def go():
        async with _pool() as pool:
            ga = await _goal(pool, "A stranded", _A, status="pursuing")
            gb = await _goal(pool, "B in-flight", _B, status="pursuing")
            await _store(pool, _A).recover_orphaned()
            assert await pool.fetchval("SELECT status FROM symbol_goals WHERE id=$1", ga) == "pending", \
                "A's stranded goal is recovered"
            assert await pool.fetchval("SELECT status FROM symbol_goals WHERE id=$1", gb) == "pursuing", \
                "B's in-flight goal MUST be untouched by A's recovery"
    asyncio.run(go())


class _FakeEmbedder:
    """create_goal only needs `.encode(text).tolist()` — avoid loading a real model."""
    def encode(self, text):
        import numpy as np
        return np.zeros(384, dtype="float32")


def test_created_goal_is_owner_stamped_end_to_end():
    """B-i Phase 2: the CREATE path stamps owner_agent, so an agent's own drive/seed goals
    are visible to its owner-scoped pursuit — and invisible to another agent's."""
    async def go():
        async with _pool() as pool:
            from nmem_sym.goals import create_goal
            emb = _FakeEmbedder()
            ga = await create_goal(pool, emb, "A's drive goal", source_type="drive_intent", owner_agent=_A)
            gb = await create_goal(pool, emb, "B's drive goal", source_type="drive_intent", owner_agent=_B)
            assert await pool.fetchval("SELECT owner_agent FROM symbol_goals WHERE id=$1", ga) == _A, \
                "create_goal must stamp the owner"
            ids_a = {g.id for g in await _store(pool, _A).actionable(50)}
            assert ga in ids_a and gb not in ids_a, "A pursues its own created goal, never B's"
    asyncio.run(go())


def test_resolve_is_owner_scoped():
    """B-i §14: resolving through the owner-scoped store must never resolve another agent's
    goal — and (since resolve emits viz + credits A2 procedures) must emit/reward NOTHING when
    the row isn't ours. The store's resolve threads owner_agent → resolve_goal short-circuits."""
    async def go():
        async with _pool() as pool:
            ga = await _goal(pool, "A", _A, status="pursuing")
            gb = await _goal(pool, "B", _B, status="pursuing")
            store_a = _store(pool, _A)
            await store_a.resolve(gb, achieved=True)   # A tries to resolve B's goal
            assert await pool.fetchval("SELECT status FROM symbol_goals WHERE id=$1", gb) == "pursuing", \
                "B's goal MUST be untouched by A's resolve"
            await store_a.resolve(ga, achieved=True)   # A resolves its own
            assert await pool.fetchval("SELECT status FROM symbol_goals WHERE id=$1", ga) == "achieved", \
                "A resolves its own goal"
    asyncio.run(go())


def test_resolve_does_not_propagate_across_owners():
    """B-i §15 / codex P1: a mixed-owner tree (A's child under B's parent — reachable via a legacy
    NULL-owner tree or an explicit cross-owner create_goal) must not let A's child resolution
    mutate/achieve B's parent via progress propagation."""
    async def go():
        async with _pool() as pool:
            from nmem_sym.goals import create_goal
            emb = _FakeEmbedder()
            b_parent = await create_goal(pool, emb, "B parent", source_type="external", owner_agent=_B)
            a_child = await create_goal(pool, emb, "A child", source_type="drive_intent",
                                        parent_id=b_parent, owner_agent=_A)
            await _store(pool, _A).resolve(a_child, achieved=True)
            assert await pool.fetchval("SELECT status FROM symbol_goals WHERE id=$1", a_child) == "achieved", \
                "A's own child is resolved"
            assert await pool.fetchval("SELECT status FROM symbol_goals WHERE id=$1", b_parent) == "pending", \
                "B's parent MUST NOT be achieved by A's child (no cross-owner propagation)"
            assert await pool.fetchval("SELECT progress FROM symbol_goals WHERE id=$1", b_parent) == 0.0, \
                "B's parent progress MUST NOT be mutated by A's child"
    asyncio.run(go())


def test_resolve_recursive_propagation_stops_at_owner_boundary():
    """B-i §15 / codex P1 (residual): a 3-level tree G(owner=B) -> P(owner=A) -> C(owner=A).
    Resolving C as A must resolve C and its same-owner parent P, but the propagation must STOP
    at the B boundary — G is never touched (the owner is threaded through update_goal_progress's
    recursive resolve_goal)."""
    async def go():
        async with _pool() as pool:
            from nmem_sym.goals import create_goal
            emb = _FakeEmbedder()
            g = await create_goal(pool, emb, "B root", source_type="external", owner_agent=_B)
            p = await create_goal(pool, emb, "A mid", source_type="external",
                                  parent_id=g, owner_agent=_A)
            c = await create_goal(pool, emb, "A leaf", source_type="drive_intent",
                                  parent_id=p, owner_agent=_A)
            await _store(pool, _A).resolve(c, achieved=True)
            st = lambda gid: pool.fetchval("SELECT status FROM symbol_goals WHERE id=$1", gid)
            assert await st(c) == "achieved", "leaf resolved"
            assert await st(p) == "achieved", "same-owner parent propagates + resolves"
            assert await st(g) == "pending", "B root MUST survive — propagation stops at the owner boundary"
            assert await pool.fetchval("SELECT progress FROM symbol_goals WHERE id=$1", g) == 0.0, \
                "B root progress untouched"
    asyncio.run(go())


class _Node:
    """Duck-typed stand-in for activation.ActivatedNode — decompose only reads .id/.label/.hop."""
    def __init__(self, id, label, hop):
        self.id, self.label, self.hop = id, label, hop


class _Activation:
    def __init__(self, nodes):
        self.activated_nodes = nodes


def test_decompose_stamps_children_with_parent_owner(monkeypatch):
    """B-i §14: decompose_goal must give sub-goals the PARENT's owner, else an agent's own
    decomposition produces NULL-owner children it can never see under owner-scoped pursuit
    (and which pollute every other agent's actionable set). Drives the real child-creation
    loop with a synthetic activation result so no populated graph is needed."""
    import nmem_sym.activation as activation_mod
    import nmem_sym.procedural as procedural_mod
    from nmem_sym.goals import decompose_goal

    async def _fake_spread(*a, **k):
        return _Activation([_Node(id=987654321, label="stepping stone", hop=1)])

    async def _fake_find(*a, **k):
        return []   # no procedure match → child stays a gap (no extra UPDATE path)

    monkeypatch.setattr(activation_mod, "spread_activation", _fake_spread)
    monkeypatch.setattr(procedural_mod, "find_matching_procedures", _fake_find)

    class _Graph:
        def __init__(self, pool):
            self.pool, self._embedder = pool, _FakeEmbedder()

    async def go():
        async with _pool() as pool:
            from nmem_sym.goals import create_goal
            parent = await create_goal(pool, _FakeEmbedder(), "A's parent goal",
                                       source_type="external", owner_agent=_A)
            res = await decompose_goal(pool, _Graph(pool), parent)
            assert res.sub_goals_created >= 1, "the synthetic stepping stone must yield a sub-goal"
            owners = await pool.fetch(
                "SELECT owner_agent FROM symbol_goals WHERE parent_id=$1", parent)
            assert owners, "children must exist"
            assert all(r["owner_agent"] == _A for r in owners), \
                "every sub-goal must inherit the parent's owner (never NULL / another agent)"
    asyncio.run(go())
