"""P1g-a — delegation substrate over the REAL nmem-exchange transport (Redis Streams + crypto).

The loopback suite (test_delegation.py) proves queue CORRECTNESS with a stubbed bus. THIS suite
proves the same guarantees ride the ACTUAL transport a live agent uses: real per-agent x25519/ed25519
identities, sealed envelopes, Redis-stream fan-out via consumer groups, and the PeerExchange kind
router. Two agents in their OWN Postgres schemas (isolation) on an ISOLATED Redis logical db + a
unique channel per test — never the live michelle↔DJ-AI bus.

Gated on a real Postgres (NMEM_TEST_PG_DSN) AND a reachable Redis (NMEX_TEST_REDIS_URL, default
redis://127.0.0.1:6379/15). Run:
  NMEM_TEST_PG_DSN=postgresql://spwigai:spwigai@127.0.0.1:5432/sales_head_ai \
  NMEX_TEST_REDIS_URL=redis://127.0.0.1:6379/15 \
  ~/sh/venv/bin/python -m pytest tests/test_delegation_redis_integration.py -q
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import os
import uuid
from types import SimpleNamespace

import pytest

from nmem.agent_core.delegation import (
    DelegationClient, DelegationInbox, ExecOutcome, KIND_PROGRESS, KIND_REQUEST, KIND_RESULT,
    TaskTypeRegistry, TaskTypeSpec,
)
from nmem.agent_core.peer import PeerExchange

_REDIS_URL = os.environ.get("NMEX_TEST_REDIS_URL", "redis://127.0.0.1:6379/15")
_A, _B = "agent-a", "agent-b"

_HAVE_NMEX = __import__("importlib").util.find_spec("nmem_exchange") is not None

pytestmark = [
    pytest.mark.skipif(not os.environ.get("NMEM_TEST_PG_DSN"),
                       reason="delegation redis integration needs a real Postgres (set NMEM_TEST_PG_DSN)"),
    pytest.mark.skipif(not _HAVE_NMEX, reason="nmem_exchange not installed"),
]


def _redis_reachable() -> bool:
    try:
        import redis.asyncio as aioredis
    except Exception:
        return False

    async def _ping():
        r = aioredis.from_url(_REDIS_URL)
        try:
            await r.ping()
            return True
        finally:
            await r.aclose()
    try:
        return asyncio.run(_ping())
    except Exception:
        return False


_REDIS_OK = _HAVE_NMEX and _redis_reachable()
pytestmark.append(pytest.mark.skipif(
    not _REDIS_OK, reason=f"no reachable Redis at {_REDIS_URL} (set NMEX_TEST_REDIS_URL)"))


# ── ledger pool (each agent its OWN schema) ─────────────────────────────────────
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


class _J:
    def __init__(self):
        self.added = []

    async def add(self, **kw):
        self.added.append(kw)


class _FakeMem:
    def __init__(self):
        self.journal = _J()


async def _noop_challenge(sender, text):
    return "(unused)"


class _CountingEcho:
    """Echoes payload; counts executions PER task_id so idempotency is observable over redelivery."""
    def __init__(self):
        self.calls: dict[str, int] = {}

    async def run(self, task_type, payload, *, task_id):
        self.calls[task_id] = self.calls.get(task_id, 0) + 1
        return ExecOutcome(ok=True, result={"echo": payload})


def _keyring(tmp_path):
    """Two fresh identities; write private keyfiles, return (keyfiles, public keyring)."""
    from nmem_exchange import crypto
    keyfiles, keyring = {}, {}
    for aid in (_A, _B):
        idn = crypto.generate_identity(aid)
        kf = str(tmp_path / f"{aid}.json")
        with open(kf, "w") as f:
            json.dump(idn.export_secret(), f)
        keyfiles[aid] = kf
        keyring[aid] = idn.public_bundle()
    return keyfiles, keyring


def _cfg(agent_id, keyfile, keyring, channel):
    return {"exchange": {
        "enabled": True,
        "identity": {"agent_id": agent_id, "keyfile": keyfile},
        "transport": {"kind": "redis", "url": _REDIS_URL},
        "keyring": keyring,
        "channels": {channel: {"policy": "encrypted", "roster": [_A, _B]}},
    }}


async def _spawn(agent_id, keyfile, keyring, channel, pool, *, executor=None, registry=None,
                 is_requester=False, approve=None):
    """Build + start a real PeerExchange, then wire the delegation role(s) onto it exactly as the
    runtime does (register_kind + run_forever)."""
    peer = PeerExchange(_cfg(agent_id, keyfile, keyring, channel), mem=_FakeMem(),
                        agent_id=agent_id, on_challenge=_noop_challenge)
    await peer.start()

    async def send(ch, kind, body, in_reply_to=None):
        return await peer.send(ch, kind, body, in_reply_to=in_reply_to)

    inbox = client = None
    tasks = []
    if executor is not None:
        inbox = DelegationInbox(pool, executor, registry, send=send, task_timeout=5.0,
                                max_attempts=3, retry_backoff=0.1, retry_backoff_max=0.5,
                                poll_interval=0.1, approve=approve)
        await inbox.setup()
        peer.register_kind(KIND_REQUEST, inbox.on_request)
        tasks.append(asyncio.create_task(inbox.run_forever()))
    if is_requester:
        client = DelegationClient(pool, send=send, channel_for=lambda t: channel, agent_id=agent_id,
                                  resend_interval=0.5, resend_backoff=1.5, poll_interval=0.1,
                                  default_deadline=30.0)
        await client.setup()
        peer.register_kind(KIND_RESULT, client.on_result)
        peer.register_kind(KIND_PROGRESS, client.on_progress)
        tasks.append(asyncio.create_task(client.run_forever()))
    return SimpleNamespace(peer=peer, inbox=inbox, client=client, tasks=tasks)


async def _shutdown(agent):
    for comp in (agent.inbox, agent.client):
        if comp is not None:
            with contextlib.suppress(Exception):
                await comp.stop()
    for t in agent.tasks:
        t.cancel()
    for t in agent.tasks:
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await t
    with contextlib.suppress(Exception):
        await agent.peer.close()


async def _drop_stream(channel):
    import redis.asyncio as aioredis
    r = aioredis.from_url(_REDIS_URL)
    try:
        await r.delete(f"nmex:ch:{channel}")
    finally:
        await r.aclose()


def test_happy_path_over_real_redis(tmp_path):
    """A real request round-trips A→(sealed over Redis)→B→execute→(sealed result)→A."""
    async def go():
        channel = f"p1ga:{uuid.uuid4().hex[:8]}"
        keyfiles, keyring = _keyring(tmp_path)
        reg = TaskTypeRegistry([TaskTypeSpec("echo", "READ_ONLY")])
        async with _pool("deleg_rt_a") as pool_a, _pool("deleg_rt_b") as pool_b:
            ex = _CountingEcho()
            worker = await _spawn(_B, keyfiles[_B], keyring, channel, pool_b, executor=ex, registry=reg)
            client = await _spawn(_A, keyfiles[_A], keyring, channel, pool_a, is_requester=True)
            try:
                res = await client.client.delegate_and_wait(_B, "echo", {"x": 1}, timeout=20)
                assert res is not None and res.ok, res
                assert res.result == {"echo": {"x": 1}}
                assert sum(ex.calls.values()) == 1
            finally:
                await _shutdown(client)
                await _shutdown(worker)
                await _drop_stream(channel)
    asyncio.run(go())


def test_worker_restart_durability_over_real_redis(tmp_path):
    """Delegate while the worker is DOWN; the outbox resends over the real bus, a FRESH worker
    process (same agent_id, same isolated DB) claims it on restart and completes it — executed
    exactly once despite redelivery. Proves outbox-resend + real redelivery + inbox idempotency."""
    async def go():
        channel = f"p1ga:{uuid.uuid4().hex[:8]}"
        keyfiles, keyring = _keyring(tmp_path)
        reg = TaskTypeRegistry([TaskTypeSpec("echo", "READ_ONLY")])
        async with _pool("deleg_rt_a") as pool_a, _pool("deleg_rt_b") as pool_b:
            client = await _spawn(_A, keyfiles[_A], keyring, channel, pool_a, is_requester=True)
            ex = _CountingEcho()   # SHARED across both worker incarnations → true call count
            try:
                # 1) delegate with NO worker running → request lands in the stream, unconsumed.
                task_id = await client.client.delegate(_B, "echo", {"x": 42})
                await asyncio.sleep(1.5)   # let the outbox resend a couple of times over Redis
                assert await pool_a.fetchval(
                    "SELECT status FROM agent_task_outbox WHERE task_id=$1", task_id) == "pending"

                # 2) bring a worker up now → it claims the (resent) request and completes it.
                worker = await _spawn(_B, keyfiles[_B], keyring, channel, pool_b, executor=ex,
                                      registry=reg)
                try:
                    for _ in range(200):
                        st = await pool_a.fetchval(
                            "SELECT status FROM agent_task_outbox WHERE task_id=$1", task_id)
                        if st == "done":
                            break
                        await asyncio.sleep(0.1)
                    assert st == "done", f"outbox never resolved (status={st})"
                    assert ex.calls.get(task_id) == 1, f"executed {ex.calls.get(task_id)}× (want 1)"
                    row = await pool_b.fetchrow(
                        "SELECT status FROM agent_task_inbox WHERE task_id=$1", task_id)
                    assert row["status"] == "completed", row["status"]
                finally:
                    await _shutdown(worker)
            finally:
                await _shutdown(client)
                await _drop_stream(channel)
    asyncio.run(go())


def test_mutating_denied_over_real_redis(tmp_path):
    """A MUTATING task type routes through the approval gate BEFORE the executor, over the real
    bus: denied → failed('approval denied'), executor never runs."""
    async def go():
        channel = f"p1ga:{uuid.uuid4().hex[:8]}"
        keyfiles, keyring = _keyring(tmp_path)
        reg = TaskTypeRegistry([TaskTypeSpec("write", "MUTATING", "mutating op")])

        async def deny(task_type, payload, cap):
            return False

        async with _pool("deleg_rt_a") as pool_a, _pool("deleg_rt_b") as pool_b:
            ex = _CountingEcho()
            worker = await _spawn(_B, keyfiles[_B], keyring, channel, pool_b, executor=ex,
                                  registry=reg, approve=deny)
            client = await _spawn(_A, keyfiles[_A], keyring, channel, pool_a, is_requester=True)
            try:
                res = await client.client.delegate_and_wait(_B, "write", {"x": 7}, timeout=20)
                assert res is not None and not res.ok
                assert "approval denied" in (res.error or ""), res.error
                assert sum(ex.calls.values()) == 0
            finally:
                await _shutdown(client)
                await _shutdown(worker)
                await _drop_stream(channel)
    asyncio.run(go())
