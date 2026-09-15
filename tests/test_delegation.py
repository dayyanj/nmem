"""P1e — brokerless delegation queue acceptance (DelegationInbox + DelegationClient).

Exercises the durable-queue CORRECTNESS over a loopback bus that can deterministically drop /
duplicate messages, so the outbox-resend + idempotent-inbox guarantees are tested directly (the
real nmem-exchange crypto/transport is its own concern and unchanged). Ledgers are real Postgres,
each "agent" in its OWN schema (isolation), gated on NMEM_TEST_PG_DSN.

Run: NMEM_TEST_PG_DSN=postgresql://spwigai:spwigai@127.0.0.1:5432/sales_head_ai \
     ~/sh/venv/bin/python -m pytest tests/test_delegation.py -q
"""
from __future__ import annotations

import asyncio
import contextlib
import os
import uuid

import pytest

from nmem.agent_core import delegation as d
from nmem.agent_core.delegation import (
    DelegationClient, DelegationInbox, ExecOutcome, KIND_PROGRESS, KIND_REQUEST, KIND_RESULT,
    Retryable, TaskTypeRegistry, TaskTypeSpec,
)

pytestmark = pytest.mark.skipif(
    not os.environ.get("NMEM_TEST_PG_DSN"),
    reason="delegation acceptance needs a real Postgres (set NMEM_TEST_PG_DSN)")

_SCHEMA_A = "deleg_test_client"   # requester (outbox)
_SCHEMA_B = "deleg_test_worker"   # worker (inbox)


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


class _EchoExecutor:
    def __init__(self):
        self.calls = 0
        self.seen_ids: list[str] = []

    async def run(self, task_type, payload, *, task_id):
        self.calls += 1
        self.seen_ids.append(task_id)
        return ExecOutcome(ok=True, result={"echo": payload})


class _FlakyExecutor:
    """Raise Retryable ``n`` times (infra), then succeed."""
    def __init__(self, n):
        self.n, self.calls = n, 0

    async def run(self, task_type, payload, *, task_id):
        self.calls += 1
        if self.calls <= self.n:
            raise Retryable("simulated infra failure")
        return ExecOutcome(ok=True, result={"ok": True})


class _AlwaysRetry:
    async def run(self, task_type, payload, *, task_id):
        raise Retryable("permanent infra failure")


class _LoopbackBus:
    """Routes ``send(channel, kind, body)`` straight to the counterpart's handler, with optional
    deterministic drop / duplicate to simulate an unreliable bus."""
    def __init__(self, *, drop=None, dup=None):
        self.inbox = self.client = None
        self._drop, self._dup = drop, dup
        self._seq: dict[str, int] = {}

    def wire(self, inbox, client):
        self.inbox, self.client = inbox, client

    def send_from(self, agent):
        async def send(channel, kind, body, in_reply_to=None):
            self._seq[kind] = self._seq.get(kind, 0) + 1
            seq = self._seq[kind]
            if self._drop and self._drop(kind, seq):
                return None
            meta = {"from": agent, "channel": channel, "msg_id": uuid.uuid4().hex,
                    "in_reply_to": in_reply_to}
            deliveries = 2 if (self._dup and self._dup(kind, seq)) else 1
            for _ in range(deliveries):
                if kind == KIND_REQUEST:
                    await self.inbox.on_request(meta, body)
                elif kind == KIND_RESULT:
                    await self.client.on_result(meta, body)
                elif kind == KIND_PROGRESS:
                    await self.client.on_progress(meta, body)
            return meta["msg_id"]
        return send


@contextlib.asynccontextmanager
async def _fixture(executor, *, registry=None, bus=None, approve=None, max_attempts=3,
                   on_complete=None, default_deadline=30.0):
    registry = registry or TaskTypeRegistry([TaskTypeSpec("echo", "READ_ONLY")])
    bus = bus or _LoopbackBus()
    async with _pool(_SCHEMA_A) as pool_a, _pool(_SCHEMA_B) as pool_b:
        inbox = DelegationInbox(pool_b, executor, registry, send=bus.send_from("worker"),
                                task_timeout=5.0, max_attempts=max_attempts,
                                retry_backoff=0.05, retry_backoff_max=0.2,
                                poll_interval=0.05, approve=approve)
        client = DelegationClient(pool_a, send=bus.send_from("client"),
                                  channel_for=lambda t: "dm:client:worker", agent_id="client",
                                  resend_interval=0.2, poll_interval=0.05,
                                  default_deadline=default_deadline, on_complete=on_complete)
        await inbox.setup()
        await client.setup()
        bus.wire(inbox, client)
        tasks = [asyncio.create_task(inbox.run_forever()), asyncio.create_task(client.run_forever())]
        try:
            yield inbox, client, pool_b
        finally:
            await inbox.stop()
            await client.stop()
            for t in tasks:
                t.cancel()
            for t in tasks:
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await t


def test_happy_path():
    async def go():
        ex = _EchoExecutor()
        async with _fixture(ex) as (_inbox, client, _pb):
            res = await client.delegate_and_wait("worker", "echo", {"x": 1}, timeout=5)
            assert res is not None and res.ok, res
            assert res.result == {"echo": {"x": 1}}
            assert ex.calls == 1
    asyncio.run(go())


def test_worker_down_then_resend():
    """First request is lost; the outbox resend delivers it → completes."""
    async def go():
        ex = _EchoExecutor()
        bus = _LoopbackBus(drop=lambda kind, seq: kind == KIND_REQUEST and seq == 1)
        async with _fixture(ex, bus=bus) as (_i, client, _pb):
            res = await client.delegate_and_wait("worker", "echo", {"x": 2}, timeout=5)
            assert res is not None and res.ok, res
            assert ex.calls == 1
    asyncio.run(go())


def test_duplicate_delivery_executes_once():
    """A duplicated request must produce exactly ONE execution (idempotent inbox)."""
    async def go():
        ex = _EchoExecutor()
        bus = _LoopbackBus(dup=lambda kind, seq: kind == KIND_REQUEST and seq == 1)
        async with _fixture(ex, bus=bus) as (_i, client, _pb):
            res = await client.delegate_and_wait("worker", "echo", {"x": 3}, timeout=5)
            assert res is not None and res.ok
            assert ex.calls == 1, f"duplicate delivery executed {ex.calls} times"
    asyncio.run(go())


def test_lost_result_is_reanswered():
    """The worker completes but its result is lost; the outbox resends the request and the inbox
    RE-ANSWERS from its stored terminal row (executor runs only once)."""
    async def go():
        ex = _EchoExecutor()
        bus = _LoopbackBus(drop=lambda kind, seq: kind == KIND_RESULT and seq == 1)
        async with _fixture(ex, bus=bus) as (_i, client, _pb):
            res = await client.delegate_and_wait("worker", "echo", {"x": 4}, timeout=5)
            assert res is not None and res.ok, res
            assert ex.calls == 1, f"re-answer re-executed ({ex.calls})"
    asyncio.run(go())


def test_retry_via_backoff_then_success():
    """Two infra failures re-queue the row with backoff; the 3rd try wins (executor runs 3x)."""
    async def go():
        ex = _FlakyExecutor(2)
        async with _fixture(ex) as (_i, client, _pb):
            res = await client.delegate_and_wait("worker", "echo", {"x": 5}, timeout=8)
            assert res is not None and res.ok, res
            assert ex.calls == 3, ex.calls
    asyncio.run(go())


def test_dead_letter_after_max_attempts():
    async def go():
        ex = _AlwaysRetry()
        async with _fixture(ex, max_attempts=3) as (_i, client, pool_b):
            res = await client.delegate_and_wait("worker", "echo", {"x": 6}, timeout=8)
            assert res is not None and not res.ok, res
            assert "retries exhausted" in (res.error or "")
            st = await pool_b.fetchval("SELECT status FROM agent_task_inbox LIMIT 1")
            assert st == "dead", st
    asyncio.run(go())


def test_approval_denied():
    async def go():
        ex = _EchoExecutor()
        reg = TaskTypeRegistry([TaskTypeSpec("write", "MUTATING", "mutating op")])

        async def deny(task_type, payload, cap):
            return False

        async with _fixture(ex, registry=reg, approve=deny) as (_i, client, _pb):
            res = await client.delegate_and_wait("worker", "write", {"x": 7}, timeout=5)
            assert res is not None and not res.ok
            assert "approval denied" in (res.error or ""), res.error
            assert ex.calls == 0, "denied task must not execute"
    asyncio.run(go())


def test_mutating_without_approver_is_fail_closed():
    async def go():
        ex = _EchoExecutor()
        reg = TaskTypeRegistry([TaskTypeSpec("write", "MUTATING")])
        async with _fixture(ex, registry=reg, approve=None) as (_i, client, _pb):
            res = await client.delegate_and_wait("worker", "write", {"x": 8}, timeout=5)
            assert res is not None and not res.ok
            assert "approval required" in (res.error or ""), res.error
            assert ex.calls == 0
    asyncio.run(go())


def test_on_complete_durable_and_retried():
    """A callback-only caller (delegate + on_complete) is notified once, and a FLAKY callback
    (raises first, succeeds next tick) is retried — never lost (codex #1)."""
    async def go():
        got: list = []
        state = {"failed_once": False}

        async def on_complete(task_id, res):
            if not state["failed_once"]:
                state["failed_once"] = True
                raise RuntimeError("callback DB blip")
            got.append((task_id, res.ok, res.result))

        ex = _EchoExecutor()
        async with _fixture(ex, on_complete=on_complete) as (_i, client, _pb):
            task_id = await client.delegate("worker", "echo", {"x": 10})
            for _ in range(100):
                if got:
                    break
                await asyncio.sleep(0.05)
            assert got == [(task_id, True, {"echo": {"x": 10}})], got
            assert ex.seen_ids == [task_id], "executor must receive the stable task_id"
    asyncio.run(go())


def test_deadline_gaveup_notifies_callback():
    """A request that never reaches the worker hits its deadline → gaveup → on_complete still
    fires (codex #5)."""
    async def go():
        got: list = []

        async def on_complete(task_id, res):
            got.append((task_id, res.ok, res.error))

        ex = _EchoExecutor()
        # drop every request → worker never sees it → deadline (0.3s) → gaveup
        bus = _LoopbackBus(drop=lambda kind, seq: kind == KIND_REQUEST)
        async with _fixture(ex, bus=bus, on_complete=on_complete, default_deadline=0.3) as (_i, client, _pb):
            task_id = await client.delegate("worker", "echo", {"x": 11})
            for _ in range(120):
                if got:
                    break
                await asyncio.sleep(0.05)
            assert got and got[0][0] == task_id and got[0][1] is False, got
            assert "deadline" in (got[0][2] or ""), got
            assert ex.calls == 0
    asyncio.run(go())


def test_unsupported_task_type_rejected():
    async def go():
        ex = _EchoExecutor()
        async with _fixture(ex) as (_i, client, _pb):
            res = await client.delegate_and_wait("worker", "nope", {"x": 9}, timeout=5)
            assert res is not None and not res.ok
            assert "unsupported task_type" in (res.error or ""), res.error
            assert ex.calls == 0
    asyncio.run(go())
