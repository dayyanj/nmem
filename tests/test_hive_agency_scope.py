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


def test_run_goal_lifecycle_is_owner_scoped(monkeypatch):
    """B-ii D2: an agent's goal-lifecycle tick (impasse-tick + abandon + reap) touches ONLY its own
    goals — never another agent's. The graph-needing steps (decompose/detect-impasses) are monkey-
    patched to no-op so the SQL-level owner-scoping is exercised without a populated graph."""
    import nmem_sym.goals as g
    from nmem_sym.goals import GoalDecomposition

    async def _noop_decompose(*a, **k):
        return GoalDecomposition(parent_goal_id=0)

    async def _no_impasses(*a, **k):
        return []

    monkeypatch.setattr(g, "decompose_goal", _noop_decompose)
    monkeypatch.setattr(g, "detect_impasses", _no_impasses)

    async def go():
        async with _pool() as pool:
            from nmem_sym.goals import run_goal_lifecycle
            ins = ("INSERT INTO symbol_goals (objective, status, source_type, owner_agent, "
                   "impasse_cycles, priority) VALUES ($1,'active','drive_intent',$2,999,0.0) RETURNING id")
            ga = await pool.fetchval(ins, "A stale", _A)
            gb = await pool.fetchval(ins, "B stale", _B)
            await run_goal_lifecycle(pool, None, owner_agent=_A)   # graph unused (steps patched)
            assert await pool.fetchval("SELECT status FROM symbol_goals WHERE id=$1", ga) == "abandoned", \
                "A's own stale goal is abandoned by A's lifecycle"
            assert await pool.fetchval("SELECT status FROM symbol_goals WHERE id=$1", gb) == "active", \
                "B's goal MUST NOT be abandoned by A's lifecycle"
            assert await pool.fetchval("SELECT impasse_cycles FROM symbol_goals WHERE id=$1", gb) == 999, \
                "B's impasse counter MUST NOT be ticked by A's lifecycle"
    asyncio.run(go())


def test_lifecycle_impasse_resolution_is_owner_scoped(monkeypatch):
    """B-ii D2 / codex L2: resolve_impasse's abandon path (a low-priority stalled goal) must not
    propagate into a FOREIGN-owned parent. Mixed-owner tree: B-owned parent P, A-owned stalled child
    G, an A-owned child of G with procedures (→ the 'stalled' branch). A's lifecycle abandons G but
    must leave P untouched."""
    import nmem_sym.goals as g
    from nmem_sym.goals import GoalDecomposition

    async def _noop_decompose(*a, **k):
        return GoalDecomposition(parent_goal_id=0)

    monkeypatch.setattr(g, "decompose_goal", _noop_decompose)

    async def go():
        async with _pool() as pool:
            from nmem_sym.goals import run_goal_lifecycle
            p = await pool.fetchval(
                "INSERT INTO symbol_goals (objective,status,source_type,owner_agent,progress) "
                "VALUES ('B parent','active','external',$1,0.75) RETURNING id", _B)
            gg = await pool.fetchval(
                "INSERT INTO symbol_goals (objective,status,source_type,owner_agent,parent_id,"
                "priority,impasse_cycles) VALUES ('A child','active','drive_intent',$1,$2,0.0,999) "
                "RETURNING id", _A, p)
            await pool.execute(
                "INSERT INTO symbol_goals (objective,status,source_type,owner_agent,parent_id,"
                "procedure_ids) VALUES ('A grandchild','active','drive_intent',$1,$2,'[1]'::jsonb)",
                _A, gg)
            await run_goal_lifecycle(pool, None, owner_agent=_A)
            assert await pool.fetchval("SELECT status FROM symbol_goals WHERE id=$1", gg) == "abandoned", \
                "A's stalled low-priority child is abandoned"
            assert await pool.fetchval("SELECT progress FROM symbol_goals WHERE id=$1", p) == 0.75, \
                "B parent progress MUST NOT be propagated by A's impasse resolution"
            assert await pool.fetchval("SELECT status FROM symbol_goals WHERE id=$1", p) == "active", \
                "B parent MUST NOT be resolved by A's lifecycle"
    asyncio.run(go())


# ── B-ii D2 §7 isolated-parity gate: run_goal_lifecycle(None) == the OLD dreamstate set ──
#
# The cutover gate. Decision 2 moved goal-lifecycle OFF the keeper's global dreamstate into a
# per-agent `run_goal_lifecycle`. Cutover flips `goal_lifecycle_external=True` (the dreamstate copy
# early-returns) and starts the external driver. This asserts the UNSCOPED path is byte-identical
# to the PRE-D2 dreamstate order, which was (verified in bridge._do_dreamstate + dreamstate.py):
#     reap_orphaned_drive_goals()   (Step 9b, inside dreamstate_once())
#     THEN _dreamstate_goals()      (tick → decompose → detect+resolve → abandon; a post-hook)
# i.e. REAP FIRST. run_goal_lifecycle(None) must reproduce that exact order (it reaps first too).
# If they diverge, cutover silently changes an agent's behavior — e.g. a low-priority impassed
# verify goal whose prediction confirmed: old reaps it 'achieved' (credit_procedures=False) BEFORE
# abandon_stale can see it; reap-last would let abandon_stale abandon it (a false A2 failure trial).
#
# Isolation: the OLD path is table-wide unscoped, so it must see ONLY our seed → a throwaway schema
# whose symbol_goals/symbol_predictions start empty. Identical seed for both runs: one transaction,
# a SAVEPOINT after seeding, run A → snapshot → ROLLBACK TO SAVEPOINT (restores exact rows + ids) →
# run B → snapshot. The single graph-dependent leaf (decompose_goal) is stubbed IDENTICALLY for both
# runs; the stub RECORDS its calls so an orchestration regression that decomposes twice (invisible to
# an idempotent UPDATE) is caught by the call-sequence assertion. viz_emit is no-op'd, and the
# thresholds + utility-plasticity are pinned so branch targeting is deterministic and the A2 reward
# path (which would UPDATE public.symbol_procedures, outside the scratch schema) can never fire.

_PARITY_SCHEMA = "parity_goal_lifecycle_bii_d2"
_OWNER_X = "parity_owner_x"   # a NAMED owner: proves owner_agent=None means ALL owners, not NULL-only


async def _seed_lifecycle_fixture(conn):
    """Seed one goal per lifecycle branch, mixing NULL and named owners (thresholds pinned to
    impasse>=3 / abandon<0.2 by the caller). No return — reap wiring is self-contained."""
    import json

    async def ig(*, objective, status, priority=0.5, impasse_cycles=0, owner=None,
                 source_type="external", source_ref="{}", parent_id=None, procedure_ids="[]"):
        return await conn.fetchval(
            "INSERT INTO symbol_goals (objective, status, priority, impasse_cycles, owner_agent, "
            "source_type, source_ref, parent_id, procedure_ids) "
            "VALUES ($1,$2,$3,$4,$5,$6,$7::jsonb,$8,$9::jsonb) RETURNING id",
            objective, status, priority, impasse_cycles, owner, source_type, source_ref,
            parent_id, procedure_ids)

    # predictions backing the three reap branches
    p_conf = await conn.fetchval("INSERT INTO symbol_predictions (status) VALUES ('confirmed') RETURNING id")
    p_pend = await conn.fetchval("INSERT INTO symbol_predictions (status) VALUES ('pending') RETURNING id")
    p_gone = (p_pend or 0) + 987654          # an id with no row → reap treats target as pruned

    # 1. plain active goal below impasse threshold → only the +1 tick (NAMED owner)
    await ig(objective="tick only", status="active", priority=0.8, impasse_cycles=0, owner=_OWNER_X)
    # 2. pending high-priority → activated + decomposed (stub → 'decomposed')
    await ig(objective="decompose me", status="pending", priority=0.9)
    # 2b. NAMED-owner pending goal → the unscoped path (owner=None) MUST still decompose it; a
    #     regression making the pending SELECT NULL-owner-only would skip it → snapshot diverges.
    await ig(objective="decompose me (named)", status="pending", priority=0.7, owner=_OWNER_X)
    # 3. impassed high-priority WITH a procedure'd child → resolve_impasse re-decomposes ('stalled')
    g3 = await ig(objective="impasse re-decompose", status="pursuing", priority=0.9, impasse_cycles=5)
    await ig(objective="g3 child", status="active", parent_id=g3, procedure_ids="[1]")
    # 3b. THRESHOLD-CROSSING (impasse_cycles=2, threshold=3): only the tick pushes it to 3 so detect
    #     picks it up THIS cycle. Makes the tick-BEFORE-detect order load-bearing — a detect-before-
    #     tick reorder would leave it at 2, undetected, ending 'active' not 'decomposed'. Priority
    #     0.85 (distinct from g3's 0.9) keeps the detect_impasses order — and thus the decompose
    #     call sequence — deterministic (no priority tie).
    g_cross = await ig(objective="impasse crossing", status="pursuing", priority=0.85, impasse_cycles=2)
    await ig(objective="g_cross child", status="active", parent_id=g_cross, procedure_ids="[3]")
    # 4. impassed LOW-priority, NO child → resolve_impasse 'no_procedure', then abandon_stale abandons
    await ig(objective="no-proc then stale-abandon", status="active", priority=0.1, impasse_cycles=5)
    # 5. impassed LOW-priority WITH procedure'd child → resolve_impasse itself abandons ('stalled' path)
    g5 = await ig(objective="impasse abandon", status="pursuing", priority=0.1, impasse_cycles=5)
    await ig(objective="g5 child", status="active", parent_id=g5, procedure_ids="[2]")
    # 6/7/8. reap branches (pursuing so the pending-decompose step never claims them). #6 NAMED owner.
    await ig(objective="reap→achieved", status="pursuing", owner=_OWNER_X, source_type="drive_intent",
             source_ref=json.dumps({"target_key": f"verify:prediction_id={p_conf}"}))
    await ig(objective="reap→abandoned", status="pursuing", source_type="drive_intent",
             source_ref=json.dumps({"target_key": f"verify:prediction_id={p_gone}"}))
    await ig(objective="reap skip (still pending)", status="pursuing", source_type="drive_intent",
             source_ref=json.dumps({"target_key": f"verify:prediction_id={p_pend}"}))


async def _snapshot(conn):
    """Behavioral state, ORDER BY id, timestamps excluded (resolved_at → boolean only). Includes
    owner_agent / procedure_ids / source_ref so an owner-scope or procedure-mutation regression can't
    hide behind the terminal status."""
    rows = await conn.fetch(
        "SELECT id, status, impasse_cycles, impasse_type, priority, progress, parent_id, owner_agent, "
        "procedure_ids::text, source_ref::text, source_type, (resolved_at IS NOT NULL) AS resolved "
        "FROM symbol_goals ORDER BY id")
    return [tuple(r) for r in rows]


def test_lifecycle_parity_unscoped_equals_old_dreamstate_set(monkeypatch):
    """§7 CUTOVER GATE: run_goal_lifecycle(owner_agent=None) is byte-identical to the pre-D2
    dreamstate-embedded lifecycle (reap → _dreamstate_goals body). Same seed, deterministic +
    call-recorded decompose, pinned thresholds, isolated schema so the unscoped old path sees only
    our rows. Asserts BOTH the final DB state AND the decompose call-sequence match."""
    import asyncio
    import os
    from unittest.mock import MagicMock

    import asyncpg
    import nmem_sym.goals as g
    import nmem_sym.config as symconfig

    def _make_decompose_stub(calls):
        async def _decompose_stub(pool, graph, goal_id):
            # Deterministic stand-in for the graph/LLM decompose: mirror its parent effect
            # (status→'decomposed') with no child inserts. Records each call so a double-decompose
            # (which an idempotent UPDATE would otherwise hide) fails the call-sequence assertion.
            calls.append(goal_id)
            await pool.execute(
                "UPDATE symbol_goals SET status='decomposed', updated_at=NOW() WHERE id=$1", goal_id)
            return g.GoalDecomposition(parent_goal_id=goal_id)
        return _decompose_stub

    async def _viz_noop(*a, **k):
        return None

    monkeypatch.setattr(g, "viz_emit", _viz_noop)
    # Pin everything the fixture's branch targeting relies on so a live DB's config can't shift a
    # goal into/out of a branch — AND so the A2 reward path (public.symbol_procedures, outside the
    # scratch schema) can never fire. The old path early-returns iff external → force pre-cutover.
    monkeypatch.setattr(symconfig.settings, "goal_lifecycle_external", False)
    monkeypatch.setattr(symconfig.settings, "utility_plasticity_enabled", False)
    monkeypatch.setattr(symconfig.settings, "goal_impasse_threshold", 3)
    monkeypatch.setattr(symconfig.settings, "goal_priority_abandon_threshold", 0.2)

    async def go():
        dsn = os.environ["NMEM_TEST_PG_DSN"].replace("+asyncpg", "")
        pool = await asyncpg.create_pool(dsn, min_size=1, max_size=2)
        calls_old, calls_new = [], []
        try:
            async with pool.acquire() as ddl:
                if not await ddl.fetchval("SELECT to_regclass('public.symbol_goals')"):
                    pytest.skip("symbol_goals absent — run nmem-sym migrations on the test DB first")
                await ddl.execute(f"DROP SCHEMA IF EXISTS {_PARITY_SCHEMA} CASCADE")
                await ddl.execute(f"CREATE SCHEMA {_PARITY_SCHEMA}")
            try:
                async with pool.acquire() as conn:
                    # isolate to the scratch schema; public still resolves the `vector` type/opclass
                    await conn.execute(f"SET search_path TO {_PARITY_SCHEMA}, public")
                    await conn.execute(g.GOALS_TABLE_SQL)
                    await conn.execute(
                        "CREATE TABLE symbol_predictions (id BIGSERIAL PRIMARY KEY, status TEXT)")

                    graph = MagicMock()
                    graph.pool = conn
                    plugin = g.GoalPlugin.__new__(g.GoalPlugin)   # skip __init__/graph wiring
                    plugin._graph = graph

                    tx = conn.transaction()
                    await tx.start()
                    try:
                        await _seed_lifecycle_fixture(conn)
                        await conn.execute("SAVEPOINT seeded")

                        # ── run A: the OLD dreamstate order — REAP FIRST (Step 9b, bounded by its
                        #    own try/except so a reap failure doesn't abort the hook), then the
                        #    post-dreamstate goal hook (_dreamstate_goals body). ──
                        monkeypatch.setattr(g, "decompose_goal", _make_decompose_stub(calls_old))
                        try:
                            await g.reap_orphaned_drive_goals(conn, None)
                        except Exception:            # pragma: no cover — mirrors Step 9b's boundary
                            pass
                        await plugin._dreamstate_goals()
                        snap_old = await _snapshot(conn)

                        await conn.execute("ROLLBACK TO SAVEPOINT seeded")   # exact seed restored

                        # ── run B: the NEW single per-agent entrypoint, unscoped ──
                        monkeypatch.setattr(g, "decompose_goal", _make_decompose_stub(calls_new))
                        await g.run_goal_lifecycle(conn, graph, owner_agent=None)
                        snap_new = await _snapshot(conn)
                    finally:
                        await tx.rollback()
            finally:
                async with pool.acquire() as ddl:
                    await ddl.execute(f"DROP SCHEMA IF EXISTS {_PARITY_SCHEMA} CASCADE")
        finally:
            await pool.close()

        assert snap_new == snap_old, (
            "run_goal_lifecycle(None) diverged from the old dreamstate lifecycle set — "
            "cutover would change behavior.\n"
            f"  old: {snap_old}\n  new: {snap_new}")
        assert calls_new == calls_old, (
            "decompose_goal was called differently (count/order/ids) — an orchestration regression "
            f"an idempotent snapshot would miss.\n  old: {calls_old}\n  new: {calls_new}")
        assert calls_new, "the fixture must exercise decompose_goal (else the call-parity check is vacuous)"

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
