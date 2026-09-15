"""P1d — runtime wiring of the delegation substrate into AgentRuntime.

The acceptance suite (test_delegation.py) proves the inbox/client CORRECTNESS over a loopback
bus. THIS suite proves the RUNTIME SEAM: default-off is byte-identical (no kinds registered, no
loops), and enabling it registers ``task.*`` on the host's peer bus, provisions the ledgers in the
agent's own graph DB, starts the loops, and tears them down cleanly. Ledgers are real Postgres
(isolated schema), gated on NMEM_TEST_PG_DSN.

Run: NMEM_TEST_PG_DSN=postgresql://spwigai:spwigai@127.0.0.1:5432/sales_head_ai \
     ~/sh/venv/bin/python -m pytest tests/test_delegation_wiring.py -q
"""
from __future__ import annotations

import asyncio
import contextlib
import os

import pytest

from nmem.agent_core import delegation as dg
from nmem.agent_core.delegation import ExecOutcome, TaskTypeRegistry, TaskTypeSpec
from nmem.agent_core.persona import Persona
from nmem.agent_core.runtime import AgentRuntime

pytestmark = pytest.mark.skipif(
    not os.environ.get("NMEM_TEST_PG_DSN"),
    reason="delegation wiring needs a real Postgres (set NMEM_TEST_PG_DSN)")

_SCHEMA = "deleg_wiring_test"


@contextlib.asynccontextmanager
async def _pool(schema: str):
    import asyncpg
    dsn = os.environ["NMEM_TEST_PG_DSN"].replace("+asyncpg", "")
    async with contextlib.AsyncExitStack() as stack:
        admin = await asyncpg.create_pool(dsn, min_size=1, max_size=1)
        stack.push_async_callback(admin.close)
        await admin.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
        await admin.execute(f"CREATE SCHEMA {schema}")
        pool = await asyncpg.create_pool(dsn, min_size=1, max_size=3,
                                         server_settings={"search_path": schema})
        stack.push_async_callback(pool.close)
        try:
            yield pool
        finally:
            with contextlib.suppress(Exception):
                await admin.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")


class _FakePeer:
    """Stand-in for a started PeerExchange: records register_kind + send calls."""
    def __init__(self, started=True):
        self.started = started
        self.kinds: dict = {}
        self.reserved: set = set()
        self.sends: list = []

    def register_kind(self, kind, handler):
        self.kinds[kind] = handler
        self.reserved.add(kind)

    def reserve_kind(self, kind):
        self.reserved.add(kind)

    def unregister_kind(self, kind, handler=None):
        if handler is not None and self.kinds.get(kind) is not handler:
            return
        self.kinds.pop(kind, None)

    async def send(self, channel, kind, body, in_reply_to=None):
        self.sends.append((channel, kind, body, in_reply_to))
        return "msg-1"


class _FakeGraph:
    def __init__(self, pool):
        self.pool = pool


class _Echo:
    async def run(self, task_type, payload, *, task_id):
        return ExecOutcome(ok=True, result={"echo": payload})


# Fast timings so stop()'s bounded drain + the loops don't slow the suite.
_FAST = {"enabled": True, "drain_grace": 0.3, "poll_interval": 0.05, "client_poll_interval": 0.05}


def _runtime(pool, *, config, peer=None, executor=None, registry=None, channel_for=None,
             approve=None, on_complete=None, hive=None) -> AgentRuntime:
    if hive is not None:
        config = {**config, "hive": hive}
    rt = AgentRuntime(config, Persona(agent_id="w"), peer=peer, delegation_executor=executor,
                      task_registry=registry, channel_for=channel_for,
                      delegation_approve=approve, on_delegation_complete=on_complete)
    rt.graph = _FakeGraph(pool)   # inject the agent's own DB pool (as start() would)
    return rt


def test_construction_reserves_kinds_before_wiring():
    """The delegation kinds are RESERVED on the peer at runtime CONSTRUCTION (before _wire_delegation
    provisions), so a task.* retry landing during startup is dropped, not journaled. Reserved ≠
    handled: no handler is attached until wiring."""
    async def go():
        async with _pool(_SCHEMA) as pool:
            peer = _FakePeer()
            rt = _runtime(pool, config={"delegation": _FAST}, peer=peer, executor=_Echo(),
                          registry=TaskTypeRegistry([TaskTypeSpec("echo")]),
                          channel_for=lambda t: f"dm:w:{t}")
            # reserved immediately at construction; NOT yet handled
            assert peer.reserved == {dg.KIND_REQUEST, dg.KIND_RESULT, dg.KIND_PROGRESS}
            assert peer.kinds == {}
    asyncio.run(go())


def test_construction_no_reserve_when_disabled():
    """Delegation off → no reservation (task.* would journal, but the operator hasn't opted in)."""
    async def go():
        async with _pool(_SCHEMA) as pool:
            peer = _FakePeer()
            _runtime(pool, config={}, peer=peer, executor=_Echo(),
                     registry=TaskTypeRegistry([TaskTypeSpec("echo")]))
            assert peer.reserved == set()
    asyncio.run(go())


def test_wiring_default_off_is_inert():
    """No delegation config → nothing registered, no loops, status off (byte-identical)."""
    async def go():
        async with _pool(_SCHEMA) as pool:
            peer = _FakePeer()
            rt = _runtime(pool, config={}, peer=peer, executor=_Echo(),
                          registry=TaskTypeRegistry([TaskTypeSpec("echo")]))
            status = await rt._wire_delegation()
            assert status == {"enabled": False, "worker": False, "requester": False}
            assert peer.kinds == {}
            assert rt._deleg_inbox_task is None and rt._deleg_client_task is None
    asyncio.run(go())


def test_wiring_no_peer_skips():
    async def go():
        async with _pool(_SCHEMA) as pool:
            rt = _runtime(pool, config={"delegation": {"enabled": True}}, peer=None,
                          executor=_Echo(), registry=TaskTypeRegistry([TaskTypeSpec("echo")]))
            status = await rt._wire_delegation()
            assert status["enabled"] is False
    asyncio.run(go())


def test_wiring_peer_not_started_skips():
    async def go():
        async with _pool(_SCHEMA) as pool:
            peer = _FakePeer(started=False)
            rt = _runtime(pool, config={"delegation": {"enabled": True}}, peer=peer,
                          executor=_Echo(), registry=TaskTypeRegistry([TaskTypeSpec("echo")]))
            status = await rt._wire_delegation()
            assert status["enabled"] is False
            assert peer.kinds == {}
    asyncio.run(go())


def test_wiring_enabled_but_nothing_to_wire():
    """enabled + started peer but neither executor nor channel_for → nothing to do."""
    async def go():
        async with _pool(_SCHEMA) as pool:
            peer = _FakePeer()
            rt = _runtime(pool, config={"delegation": {"enabled": True}}, peer=peer)
            status = await rt._wire_delegation()
            assert status["enabled"] is False
            assert peer.kinds == {}
    asyncio.run(go())


def test_wiring_worker_and_requester():
    """Full wire: worker registers task.request; requester registers task.result/progress; both
    ledgers provisioned; loops started; stop() tears them down."""
    async def go():
        async with _pool(_SCHEMA) as pool:
            peer = _FakePeer()
            rt = _runtime(pool, config={"delegation": _FAST}, peer=peer,
                          executor=_Echo(), registry=TaskTypeRegistry([TaskTypeSpec("echo")]),
                          channel_for=lambda t: f"dm:w:{t}")
            status = await rt._wire_delegation()
            assert status == {"enabled": True, "worker": True, "requester": True}
            # task.* routed onto the ONE shared bus
            assert set(peer.kinds) == {dg.KIND_REQUEST, dg.KIND_RESULT, dg.KIND_PROGRESS}
            assert peer.kinds[dg.KIND_REQUEST] == rt._deleg_inbox.on_request
            assert peer.kinds[dg.KIND_RESULT] == rt._deleg_client.on_result
            # both ledgers exist in the agent's own DB
            assert await pool.fetchval(
                "SELECT to_regclass('agent_task_inbox')") is not None
            assert await pool.fetchval(
                "SELECT to_regclass('agent_task_outbox')") is not None
            # loops are live
            assert rt._deleg_inbox_task is not None and not rt._deleg_inbox_task.done()
            assert rt._deleg_client_task is not None and not rt._deleg_client_task.done()
            # public convenience delegates through the client (writes a durable outbox row)
            tid = await rt.delegate("peer-x", "echo", {"a": 1})
            assert await pool.fetchval(
                "SELECT status FROM agent_task_outbox WHERE task_id=$1", tid) == "pending"
            # teardown cancels both loops AND detaches handlers from the host-owned peer (which
            # outlives the runtime) — else task.request would keep hitting a dead inbox / closed pool.
            await rt.stop()
            assert rt._deleg_inbox_task is None and rt._deleg_client_task is None
            assert peer.kinds == {}, "stop() must unregister all delegation kinds from the peer bus"
    asyncio.run(go())


def test_wiring_worker_without_registry_refused():
    """A worker MUST declare a task_registry (accept-list + capability classes). An executor with no
    registry is fail-closed to NO worker role — else a MUTATING task queued before a restart would be
    claimed and gated as READ_ONLY, bypassing approval. Requester role (channel_for) still wires."""
    async def go():
        async with _pool(_SCHEMA) as pool:
            peer = _FakePeer()
            rt = _runtime(pool, config={"delegation": _FAST}, peer=peer, executor=_Echo(),
                          channel_for=lambda t: f"dm:w:{t}")   # executor but NO registry
            status = await rt._wire_delegation()
            assert status == {"enabled": True, "worker": False, "requester": True}
            assert dg.KIND_REQUEST not in peer.kinds, "no worker role → task.request must not route"
            assert set(peer.kinds) == {dg.KIND_RESULT, dg.KIND_PROGRESS}
            assert rt._deleg_inbox_task is None
            await rt.stop()
    asyncio.run(go())


def test_wiring_worker_only_no_registry_is_off():
    """Executor without registry AND no channel_for → nothing wireable → OFF (not a bare worker)."""
    async def go():
        async with _pool(_SCHEMA) as pool:
            peer = _FakePeer()
            rt = _runtime(pool, config={"delegation": _FAST}, peer=peer, executor=_Echo())
            status = await rt._wire_delegation()
            assert status == {"enabled": False, "worker": False, "requester": False}
            assert peer.kinds == {}
    asyncio.run(go())


def test_wiring_shared_world_refused():
    """Isolation invariant (codex P1): under hive=shared_world the graph DB is shared, so an
    unscoped inbox would let agents claim each other's tasks. Delegation must fail CLOSED there."""
    async def go():
        async with _pool(_SCHEMA) as pool:
            peer = _FakePeer()
            rt = _runtime(pool, config={"delegation": _FAST},
                          hive={"mode": "shared_world", "agent_id": "w", "graph_role": "contributor"},
                          peer=peer, executor=_Echo(),
                          registry=TaskTypeRegistry([TaskTypeSpec("echo")]),
                          channel_for=lambda t: f"dm:w:{t}")
            status = await rt._wire_delegation()
            assert status == {"enabled": False, "worker": False, "requester": False}
            assert peer.kinds == {}
            assert rt._deleg_inbox_task is None and rt._deleg_client_task is None
    asyncio.run(go())


def test_stop_detaches_only_own_handlers():
    """stop() must remove ONLY the handlers this runtime installed — a handler the host (or another
    subsystem) pre-registered on the shared bus survives teardown."""
    async def go():
        async with _pool(_SCHEMA) as pool:
            peer = _FakePeer()

            async def host_result_handler(meta, body):
                return None
            peer.register_kind(dg.KIND_RESULT, host_result_handler)   # pre-existing, not ours

            rt = _runtime(pool, config={"delegation": _FAST}, peer=peer, executor=_Echo(),
                          registry=TaskTypeRegistry([TaskTypeSpec("echo")]))  # worker only
            await rt._wire_delegation()
            assert dg.KIND_REQUEST in peer.kinds
            await rt.stop()
            # our task.request is gone; the host's task.result handler is untouched
            assert dg.KIND_REQUEST not in peer.kinds
            assert peer.kinds.get(dg.KIND_RESULT) is host_result_handler
    asyncio.run(go())


def test_stop_identity_safe_against_replacement():
    """If a REPLACEMENT runtime re-registers a kind after ours, our stop() must not detach theirs."""
    async def go():
        async with _pool(_SCHEMA) as pool:
            peer = _FakePeer()
            rt = _runtime(pool, config={"delegation": _FAST}, peer=peer, executor=_Echo(),
                          registry=TaskTypeRegistry([TaskTypeSpec("echo")]))
            await rt._wire_delegation()

            async def replacement_handler(meta, body):
                return None
            peer.register_kind(dg.KIND_REQUEST, replacement_handler)   # a new runtime took over

            await rt.stop()
            assert peer.kinds.get(dg.KIND_REQUEST) is replacement_handler, \
                "stop() removed a replacement's handler — identity check failed"
    asyncio.run(go())


def test_wiring_worker_only_registers_request_only():
    """A pure worker (no channel_for) routes task.request but NOT result/progress, and delegate()
    raises (not a requester)."""
    async def go():
        async with _pool(_SCHEMA) as pool:
            peer = _FakePeer()
            rt = _runtime(pool, config={"delegation": _FAST}, peer=peer,
                          executor=_Echo(), registry=TaskTypeRegistry([TaskTypeSpec("echo")]))
            status = await rt._wire_delegation()
            assert status == {"enabled": True, "worker": True, "requester": False}
            assert set(peer.kinds) == {dg.KIND_REQUEST}
            with pytest.raises(RuntimeError):
                await rt.delegate("x", "echo", {})
            await rt.stop()
    asyncio.run(go())
