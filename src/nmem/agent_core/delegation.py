"""Brokerless durable agent-to-agent delegation over the nmem-exchange bus (P1).

See ``_design/delegation-exchange-plan.md`` for the full design. This module is the
CONTRACT layer (P1a): the wire envelopes, the two local-ledger schemas, the executor
protocol, and the task-type registry. The behavioural pieces build on top:

  * ``DelegationInbox``  (P1b) — worker side: durable local inbox + executor loop + lease
    recovery. Graduated from the (retired) technical_writer ``consumer.py``.
  * ``DelegationClient`` (P1c) — requester side: transactional outbox + resend loop +
    result correlation.
  * ``PeerExchange.register_kind`` (P1d) — routes ``task.*`` kinds on the shared bus.

Durability model (because the exchange transport AUTO-ACKS on delivery — it is a bus, not a
queue): end-to-end delivery is guaranteed by the transactional-outbox + idempotent-consumer
pattern. The requester's outbox re-sends until a result arrives; the worker's inbox INSERTs
idempotently on ``task_id``. Both ledgers live in the agent's OWN database, so cross-agent
isolation is preserved — the bus only carries request/result envelopes.

Nothing here runs until the runtime wires it (P1d), gated ``delegation.enabled`` (default off).
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
import uuid
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Protocol, runtime_checkable

log = logging.getLogger(__name__)

# A bus-send callable the inbox/client use to publish over the shared exchange:
#   async send(channel: str, kind: str, body: bytes, *, in_reply_to: str | None = None) -> str | None
SendFn = Callable[..., Awaitable[Any]]

# ── message kinds (the bus ``kind`` = the router key) ───────────────────────────
KIND_REQUEST = "task.request"
KIND_RESULT = "task.result"
KIND_PROGRESS = "task.progress"

# ── terminal result statuses ────────────────────────────────────────────────────
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"


class Retryable(Exception):
    """An INFRASTRUCTURE failure (DB down, LLM/bus blip, timeout) — the task is fine, retry
    it. Graduated from the tw consumer: the inbox leaves a Retryable'd task claimable (lease
    lapses → reclaimed) up to ``max_attempts``, then dead-letters. A *terminal* task failure
    (unsupported type, ungroundable input) is NOT a Retryable — it returns ``ok=False``."""


def new_task_id() -> str:
    """A requester-minted id, STABLE across resends → the idempotency key on the worker inbox."""
    return uuid.uuid4().hex


# ── wire envelopes (JSON bodies over the bus) ───────────────────────────────────

@dataclass
class TaskRequest:
    """``kind=task.request`` body. ``reply_to`` is the channel the worker sends the result on;
    ``attempt`` is telemetry only (dedup is by ``task_id``). The requester is the envelope's
    signed ``from`` (``meta['from']``), not carried in the body."""
    task_id: str
    task_type: str
    payload: dict[str, Any]
    reply_to: str
    deadline_ts: float | None = None
    attempt: int = 1

    def to_body(self) -> bytes:
        return json.dumps({
            "task_id": self.task_id, "task_type": self.task_type, "payload": self.payload,
            "reply_to": self.reply_to, "deadline_ts": self.deadline_ts, "attempt": self.attempt,
        }).encode()

    @classmethod
    def from_body(cls, body: bytes | str) -> "TaskRequest":
        d = json.loads(body.decode() if isinstance(body, bytes) else body)
        return cls(
            task_id=str(d["task_id"]), task_type=str(d["task_type"]),
            payload=dict(d.get("payload") or {}), reply_to=str(d["reply_to"]),
            deadline_ts=(float(d["deadline_ts"]) if d.get("deadline_ts") is not None else None),
            attempt=int(d.get("attempt", 1)))

    def expired(self, *, now: float | None = None) -> bool:
        return self.deadline_ts is not None and (now or time.time()) > self.deadline_ts


@dataclass
class TaskResult:
    """``kind=task.result`` body, sent ``in_reply_to`` the request's ``msg_id``. Terminal."""
    task_id: str
    status: str                      # STATUS_COMPLETED | STATUS_FAILED
    result: dict[str, Any] | None = None
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.status == STATUS_COMPLETED

    def to_body(self) -> bytes:
        return json.dumps({
            "task_id": self.task_id, "status": self.status,
            "result": self.result, "error": self.error,
        }).encode()

    @classmethod
    def from_body(cls, body: bytes | str) -> "TaskResult":
        d = json.loads(body.decode() if isinstance(body, bytes) else body)
        return cls(
            task_id=str(d["task_id"]), status=str(d["status"]),
            result=(dict(d["result"]) if isinstance(d.get("result"), dict) else d.get("result")),
            error=(str(d["error"]) if d.get("error") is not None else None))

    @classmethod
    def completed(cls, task_id: str, result: dict[str, Any] | None = None) -> "TaskResult":
        return cls(task_id=task_id, status=STATUS_COMPLETED, result=result)

    @classmethod
    def failed(cls, task_id: str, error: str) -> "TaskResult":
        return cls(task_id=task_id, status=STATUS_FAILED, error=error)


@dataclass
class TaskProgress:
    """``kind=task.progress`` body (optional, advisory). Resets the requester's resend timer."""
    task_id: str
    note: str = ""
    pct: float | None = None

    def to_body(self) -> bytes:
        return json.dumps({"task_id": self.task_id, "note": self.note, "pct": self.pct}).encode()

    @classmethod
    def from_body(cls, body: bytes | str) -> "TaskProgress":
        d = json.loads(body.decode() if isinstance(body, bytes) else body)
        return cls(task_id=str(d["task_id"]), note=str(d.get("note") or ""),
                   pct=(float(d["pct"]) if d.get("pct") is not None else None))


# ── executor contract (the ONE per-agent piece — same shape as tw kb_executor) ──

@dataclass
class ExecOutcome:
    """What a worker's executor returns. ``ok=True`` → success (``result`` sent back);
    ``ok=False`` → a TERMINAL task failure (``reason`` sent back, NOT retried). For an
    INFRASTRUCTURE failure the executor raises :class:`Retryable` instead (→ reclaimed)."""
    ok: bool
    result: dict[str, Any] | None = None
    reason: str | None = None


class AskExecutor:
    """A generic READ_ONLY delegation executor: answer a delegated QUESTION via the agent's own
    grounded cognition (``chat.system_prompt`` + its backend). The trivial-but-real capability a thin
    appliance exposes first — a peer delegates ``ask`` with ``payload={question|prompt}`` and gets
    ``{answer}`` back. A backend/LLM failure is INFRA → raise :class:`Retryable` (reclaimed), not a
    terminal task failure. Unknown task types never reach here (the inbox rejects them at intake)."""

    def __init__(self, backend, persona, *, max_tokens: int = 600) -> None:
        self._backend = backend
        self._persona = persona
        self._max_tokens = int(max_tokens)

    async def run(self, task_type: str, payload: dict, *, task_id: str) -> "ExecOutcome":
        question = (payload.get("question") or payload.get("prompt") or "").strip()
        if not question:
            return ExecOutcome(ok=False, reason="empty question")
        from nmem.agent_core.chat import system_prompt
        sysp = system_prompt(self._persona)
        try:
            answer = await self._backend.chat(
                [{"role": "system", "content": sysp}, {"role": "user", "content": question}],
                max_tokens=self._max_tokens)
        except Exception as e:  # noqa: BLE001 — LLM/backend blip is INFRA, not a bad task → retry
            raise Retryable(f"ask backend failure: {e}") from e
        return ExecOutcome(ok=True, result={"answer": answer})


@runtime_checkable
class Executor(Protocol):
    """A worker agent injects one of these. Refinery agents' executors call the shared
    ``refinery-core`` API; a cognitive agent's executor may run its tool loop.

    ``task_id`` is the STABLE idempotency key: the inbox dedups delivery, but a task whose
    executor succeeded then failed to record its terminal row is re-executed after lease/retry
    recovery — so an executor with an observable side effect MUST key that effect on ``task_id``
    (e.g. an upsert / an idempotency token on the downstream API) to avoid doing it twice."""
    async def run(self, task_type: str, payload: dict[str, Any], *, task_id: str) -> ExecOutcome: ...


# ── task-type registry (accept-list + capability class for the approval gate) ───

@dataclass
class TaskTypeSpec:
    """One task type a worker accepts. ``capability_class`` (from ``nmem_act``) drives the
    approval gate: MUTATING/HIGH_RISK route through the agent's autonomy/approval flow before
    the executor runs. Stored as the class NAME so this module needn't import nmem_act at
    definition time (resolved lazily by the inbox when gating)."""
    task_type: str
    capability_class: str = "READ_ONLY"     # READ_ONLY | MUTATING | HIGH_RISK
    description: str = ""


class TaskTypeRegistry:
    """A worker's declared task types. Unknown types are rejected at intake with a terminal
    ``task.result(failed, "unsupported task_type")`` — never silently dropped."""

    def __init__(self, specs: list[TaskTypeSpec] | None = None) -> None:
        self._specs: dict[str, TaskTypeSpec] = {s.task_type: s for s in (specs or [])}

    def register(self, spec: TaskTypeSpec) -> None:
        self._specs[spec.task_type] = spec

    def accepts(self, task_type: str) -> bool:
        return task_type in self._specs

    def spec(self, task_type: str) -> TaskTypeSpec | None:
        return self._specs.get(task_type)

    def capability_class(self, task_type: str):
        """Resolve to the real ``nmem_act.CapabilityClass`` (lazy import); READ_ONLY if
        unknown or nmem_act is absent (fail-safe toward the gate treating it as harmless-read
        only when it genuinely is; the inbox still rejects unknown types before this)."""
        from nmem_act import CapabilityClass
        name = (self._specs.get(task_type) or TaskTypeSpec(task_type)).capability_class
        return getattr(CapabilityClass, name, CapabilityClass.READ_ONLY)


# ── local-ledger schemas (each in the agent's OWN db; self-provisioned on boot) ─

# Worker side: the durable inbox. Graduates the tw ``nmem_delegations`` lifecycle to a local
# table; ``owner_token`` + ``lease_until`` are the execution lease (recovery reclaims own
# expired-lease rows — a plain DB scan replaces XAUTOCLAIM PEL recovery).
INBOX_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS agent_task_inbox (
    task_id         TEXT PRIMARY KEY,
    requester       TEXT NOT NULL,
    task_type       TEXT NOT NULL,
    payload         JSONB NOT NULL DEFAULT '{}',
    reply_to        TEXT NOT NULL,
    msg_id          TEXT,
    status          TEXT NOT NULL DEFAULT 'queued',   -- queued|executing|completed|failed|dead
    owner_token     TEXT,
    lease_until     TIMESTAMP WITH TIME ZONE,          -- crash-detection window (>= task_timeout)
    next_attempt_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),  -- retry backoff gate
    attempts        INTEGER NOT NULL DEFAULT 0,
    result          JSONB,
    error           TEXT,
    created_at      TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_task_inbox_claimable
    ON agent_task_inbox (next_attempt_at)
    WHERE status = 'queued';
CREATE INDEX IF NOT EXISTS idx_task_inbox_leased
    ON agent_task_inbox (lease_until)
    WHERE status = 'executing';
"""

# Requester side: the transactional outbox. The resend loop re-sends 'pending' rows past
# next_resend_at until a result arrives or the deadline passes (→ 'gaveup').
OUTBOX_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS agent_task_outbox (
    task_id         TEXT PRIMARY KEY,
    target          TEXT NOT NULL,
    task_type       TEXT NOT NULL,
    payload         JSONB NOT NULL DEFAULT '{}',
    request_channel TEXT NOT NULL,
    reply_channel   TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'pending',   -- pending|done|gaveup
    attempts        INTEGER NOT NULL DEFAULT 0,
    next_resend_at  TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    deadline_ts     TIMESTAMP WITH TIME ZONE,
    result          JSONB,
    error           TEXT,
    notified        BOOLEAN NOT NULL DEFAULT FALSE,     -- on_complete callback durably delivered?
    created_at      TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_task_outbox_resend
    ON agent_task_outbox (next_resend_at)
    WHERE status = 'pending';
CREATE INDEX IF NOT EXISTS idx_task_outbox_notify
    ON agent_task_outbox (updated_at)
    WHERE status IN ('done', 'gaveup') AND NOT notified;
"""


async def provision(pool) -> None:
    """Create both ledgers (idempotent). Called by inbox/client setup on first boot."""
    await pool.execute(INBOX_TABLE_SQL)
    await pool.execute(OUTBOX_TABLE_SQL)


# ── P1b: worker side — durable local inbox + executor loop + lease recovery ──────

# Approval hook: async (task_type, payload, capability_class) -> bool. Returns False → deny.
ApproveFn = Callable[[str, dict, Any], Awaitable[bool]]


class DelegationInbox:
    """Worker side of the brokerless queue. Graduated from the retired technical_writer
    ``consumer.py`` (same correctness contract), with the durable substrate now a LOCAL table
    (``agent_task_inbox`` in the agent's own DB) and the "PEL" now local lease recovery.

    Two moving parts:
      * ``on_request`` — the bus handler for ``task.request``: persist idempotently on
        ``task_id`` (dedups outbox resends + bus redelivery); re-answer if already terminal
        (so a lost result is recovered by the requester's resend); reject unsupported types.
      * ``run_forever`` — the executor loop: reclaim expired leases → atomic claim (owner_token
        + lease) → approval gate → run the injected executor → send ``task.result`` → mark the
        row terminal (ack-after-outcome). Infra failures (Retryable / timeout / crash) leave the
        row ``executing`` so the lapsing lease reclaims it; ``attempts`` past ``max_attempts``
        dead-letters. Cancellation (shutdown) never finalizes → recovered next boot.
    """

    def __init__(self, pool, executor: Executor, registry: TaskTypeRegistry, *, send: SendFn,
                 task_timeout: float = 900.0, lease_seconds: float | None = None,
                 max_attempts: int = 3, retry_backoff: float = 1.0, retry_backoff_max: float = 300.0,
                 poll_interval: float = 1.0, approve: ApproveFn | None = None) -> None:
        self._pool = pool
        self._executor = executor
        self._registry = registry
        self._send = send
        self._timeout = float(task_timeout)
        # The lease is the CRASH-detection window, NOT the retry timer. It MUST exceed the task
        # timeout, else a still-running task looks abandoned and a second loop/boot could reclaim
        # and double-execute it (codex P1). Retries after an infra failure are an EXPLICIT
        # re-queue with next_attempt_at backoff, so the lease can be comfortably long.
        self._lease = max(float(lease_seconds) if lease_seconds else self._timeout * 2.0,
                          self._timeout + 5.0)
        self._max_attempts = int(max_attempts)
        self._retry_backoff = float(retry_backoff)
        self._retry_backoff_max = float(retry_backoff_max)
        self._poll = float(poll_interval)
        self._approve = approve
        self._stop = asyncio.Event()

    async def setup(self) -> None:
        await self._pool.execute(INBOX_TABLE_SQL)

    # ── bus handler ────────────────────────────────────────────────
    async def on_request(self, meta: dict, body) -> None:
        """Registered for KIND_REQUEST on the shared bus."""
        try:
            req = TaskRequest.from_body(body)
        except Exception:  # noqa: BLE001
            log.warning("[inbox] malformed task.request from %s — dropping", meta.get("from"))
            return
        msg_id = meta.get("msg_id")
        # Consult existing state FIRST (codex #4): a resend of an already-finished task must be
        # re-answered from the stored terminal row even if the type was since un-registered.
        try:
            row = await self._pool.fetchrow(
                "SELECT status, result, error FROM agent_task_inbox WHERE task_id=$1", req.task_id)
        except Exception:  # noqa: BLE001 — DB blip; the requester's outbox will resend
            log.warning("[inbox] state read failed for %s — requester will resend", req.task_id,
                        exc_info=True)
            return
        if row is not None:
            if row["status"] in ("completed", "failed", "dead"):
                await self._safe_send(req.reply_to, self._result_from_row(req.task_id, row), msg_id)
            return   # queued/executing → in progress, nothing to do (dedups resend/redelivery)
        # New task: reject unsupported types at intake (never silently drop, never persist).
        if not self._registry.accepts(req.task_type):
            await self._safe_send(req.reply_to,
                                  TaskResult.failed(req.task_id, f"unsupported task_type: {req.task_type}"),
                                  msg_id)
            return
        try:
            await self._pool.execute(
                "INSERT INTO agent_task_inbox (task_id, requester, task_type, payload, reply_to, msg_id) "
                "VALUES ($1,$2,$3,$4::jsonb,$5,$6) ON CONFLICT (task_id) DO NOTHING",
                req.task_id, meta.get("from") or "", req.task_type, json.dumps(req.payload),
                req.reply_to, msg_id)
        except Exception:  # noqa: BLE001 — persist failed; the requester's outbox will resend
            log.warning("[inbox] persist failed for %s — requester will resend", req.task_id, exc_info=True)

    # ── executor loop ──────────────────────────────────────────────
    async def run_forever(self) -> None:
        await self.setup()
        await self._reclaim_expired()   # boot recovery
        while not self._stop.is_set():
            try:
                await self._reclaim_expired()   # periodic recovery (Backlog #3)
                row = await self._claim()
                if row is None:
                    await asyncio.sleep(self._poll)
                    continue
                await self._execute(row)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                log.warning("[inbox] loop error", exc_info=True)
                await asyncio.sleep(self._poll)

    async def stop(self) -> None:
        self._stop.set()

    async def _reclaim_expired(self) -> None:
        # Only a CRASHED execution (lease elapsed with no live loop to renew/finish it) is
        # reclaimed. A live task can't hit this: the lease exceeds the task timeout.
        await self._pool.execute(
            "UPDATE agent_task_inbox SET status='queued', owner_token=NULL, lease_until=NULL, "
            "next_attempt_at=now(), updated_at=now() "
            "WHERE status='executing' AND lease_until < now()")

    async def _claim(self):
        tok = uuid.uuid4().hex
        return await self._pool.fetchrow(
            "UPDATE agent_task_inbox SET status='executing', owner_token=$1, "
            "lease_until = now() + make_interval(secs => $2::float8), attempts = attempts + 1, "
            "updated_at = now() "
            "WHERE task_id = (SELECT task_id FROM agent_task_inbox "
            "WHERE status='queued' AND next_attempt_at <= now() "
            "ORDER BY next_attempt_at, created_at FOR UPDATE SKIP LOCKED LIMIT 1) "
            "RETURNING task_id, requester, task_type, payload, reply_to, msg_id, attempts, owner_token",
            tok, self._lease)

    async def _release_for_retry(self, task_id: str, tok: str, attempts: int) -> None:
        """Infra failure → put the row back to 'queued' with a backing-off next_attempt_at
        (conditioned on OUR token). Explicit, so the lease governs ONLY crash detection."""
        backoff = min(self._retry_backoff * (2.0 ** max(0, attempts - 1)), self._retry_backoff_max)
        await self._pool.execute(
            "UPDATE agent_task_inbox SET status='queued', owner_token=NULL, lease_until=NULL, "
            "next_attempt_at = now() + make_interval(secs => $2::float8), updated_at=now() "
            "WHERE task_id=$1 AND owner_token=$3", task_id, backoff, tok)

    async def _execute(self, row) -> None:
        task_id, tok = row["task_id"], row["owner_token"]
        tt, reply_to, msg_id = row["task_type"], row["reply_to"], row["msg_id"]
        payload = row["payload"]
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except Exception:  # noqa: BLE001
                payload = {}

        # Renew the lease for as long as THIS loop holds the row — so the approval gate (which
        # can be unbounded, e.g. a human) or a long executor never lets the lease lapse under a
        # LIVE worker. Only a truly dead loop stops renewing → its lease expires → crash-reclaim.
        # This removes the "approval/executor outlasts the static lease" reclaim (codex #3). The
        # residual — a worker STALLED past its lease despite the heartbeat, with a 2nd worker on
        # the same inbox — is the inherent at-least-once property of a lease queue, backstopped by
        # the single-worker-per-inbox invariant AND the Executor's task_id idempotency contract +
        # the token-conditioned terminal write (only one claimant can ever record the result).
        hb = asyncio.create_task(self._heartbeat(task_id, tok))
        try:
            if row["attempts"] > self._max_attempts:
                if await self._finish(task_id, tok, "dead", None, "retries exhausted"):
                    await self._safe_send(reply_to, TaskResult.failed(task_id, "retries exhausted"), msg_id)
                return

            gate_err = await self._gate(tt, payload)
            if gate_err is not None:
                if await self._finish(task_id, tok, "failed", None, gate_err):
                    await self._safe_send(reply_to, TaskResult.failed(task_id, gate_err), msg_id)
                return

            try:
                outcome = await asyncio.wait_for(
                    self._executor.run(tt, payload, task_id=task_id), timeout=self._timeout)
            except asyncio.CancelledError:
                raise                              # shutdown — leave 'executing'; crash-reclaim on boot
            except asyncio.TimeoutError:
                log.warning("[inbox] %s executor timeout — re-queue with backoff", task_id)
                await self._release_for_retry(task_id, tok, row["attempts"])
                return
            except Retryable as e:
                log.warning("[inbox] %s retryable: %s — re-queue with backoff", task_id, e)
                await self._release_for_retry(task_id, tok, row["attempts"])
                return
            except Exception:  # noqa: BLE001
                log.warning("[inbox] %s executor crashed — re-queue with backoff", task_id, exc_info=True)
                await self._release_for_retry(task_id, tok, row["attempts"])
                return

            if not isinstance(outcome, ExecOutcome):
                if await self._finish(task_id, tok, "failed", None, "executor returned non-ExecOutcome"):
                    await self._safe_send(reply_to,
                                          TaskResult.failed(task_id, "internal: bad executor return"), msg_id)
                return

            if outcome.ok:
                if await self._finish(task_id, tok, "completed", outcome.result, None):
                    await self._safe_send(reply_to, TaskResult.completed(task_id, outcome.result), msg_id)
            else:
                reason = outcome.reason or "task failed"
                if await self._finish(task_id, tok, "failed", None, reason):
                    await self._safe_send(reply_to, TaskResult.failed(task_id, reason), msg_id)
        finally:
            hb.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await hb

    async def _heartbeat(self, task_id: str, tok: str) -> None:
        """Renew our lease every ~lease/3 while we still own the row (executing). Stops when the
        row leaves our ownership (0 rows updated) or the loop is cancelled/dies."""
        interval = max(self._lease / 3.0, 1.0)
        while True:
            await asyncio.sleep(interval)
            try:
                res = await self._pool.execute(
                    "UPDATE agent_task_inbox SET lease_until = now() + make_interval(secs => $2::float8) "
                    "WHERE task_id=$1 AND owner_token=$3 AND status='executing'",
                    task_id, self._lease, tok)
            except Exception:  # noqa: BLE001 — a renew blip; next tick retries
                continue
            if res.rsplit(" ", 1)[-1] != "1":
                return   # we no longer own it (reclaimed/finished) — stop renewing

    async def _gate(self, task_type: str, payload: dict) -> str | None:
        """None = allow; a string = deny reason. READ_ONLY passes without nmem_act; MUTATING/
        HIGH_RISK require an approver (fail-closed if none)."""
        spec = self._registry.spec(task_type)
        cap_name = spec.capability_class if spec else "READ_ONLY"
        if cap_name == "READ_ONLY":
            return None
        if self._approve is None:
            return f"approval required ({cap_name}) but no approver configured"
        try:
            cap = self._registry.capability_class(task_type)
        except Exception:  # noqa: BLE001 — nmem_act absent; pass the name to the approver
            cap = cap_name
        try:
            ok = await self._approve(task_type, payload, cap)
        except Exception as e:  # noqa: BLE001 — fail-closed
            return f"approval error: {e}"
        return None if ok else "approval denied"

    async def _finish(self, task_id: str, tok: str, status: str,
                      result: dict | None, error: str | None) -> bool:
        """Terminal write conditioned on OUR token — never clobber a concurrent reclaim.
        Returns True iff we still owned the row (→ only then do we send the result)."""
        res = await self._pool.execute(
            "UPDATE agent_task_inbox SET status=$2, result=$3::jsonb, error=$4, updated_at=now() "
            "WHERE task_id=$1 AND owner_token=$5",
            task_id, status, (json.dumps(result) if result is not None else None), error, tok)
        return res.rsplit(" ", 1)[-1] == "1"

    @staticmethod
    def _result_from_row(task_id: str, row) -> "TaskResult":
        if row["status"] == "completed":
            result = row["result"]
            if isinstance(result, str):
                try:
                    result = json.loads(result)
                except Exception:  # noqa: BLE001
                    result = None
            return TaskResult.completed(task_id, result)
        return TaskResult.failed(task_id, row["error"] or "failed")

    async def _safe_send(self, channel: str, result: "TaskResult", in_reply_to) -> None:
        try:
            await self._send(channel, KIND_RESULT, result.to_body(), in_reply_to=in_reply_to)
        except Exception:  # noqa: BLE001 — best-effort; requester resend recovers a lost result
            log.warning("[inbox] send result failed for %s", result.task_id, exc_info=True)


# ── P1c: requester side — transactional outbox + resend loop + result correlation ──

# Maps a target agent_id → the (2-member DM) exchange channel to delegate over. The worker
# sends its result back on the SAME channel (reply_to), which the requester is a member of.
ChannelForFn = Callable[[str], str]


class DelegationClient:
    """Requester side of the brokerless queue. ``delegate()`` writes a durable outbox row THEN
    sends ``task.request``; a resend loop re-sends any still-``pending`` row past its (backing-off)
    ``next_resend_at`` until a ``task.result`` arrives or the deadline passes (→ ``gaveup``). This
    is what makes delivery at-least-once over a bus that only auto-acks on delivery. ``on_result``
    resolves the outbox row (idempotently) + any waiter + the optional completion callback."""

    def __init__(self, pool, *, send: SendFn, channel_for: ChannelForFn, agent_id: str,
                 resend_interval: float = 30.0, resend_backoff: float = 2.0,
                 resend_max_interval: float = 600.0, default_deadline: float = 3600.0,
                 poll_interval: float = 5.0,
                 on_complete: Callable[[str, "TaskResult"], Awaitable[None]] | None = None) -> None:
        self._pool = pool
        self._send = send
        self._channel_for = channel_for
        self._agent_id = agent_id
        self._resend_interval = float(resend_interval)
        self._resend_backoff = float(resend_backoff)
        self._resend_max = float(resend_max_interval)
        self._default_deadline = float(default_deadline)
        self._poll = float(poll_interval)
        self._on_complete = on_complete
        self._waiters: dict[str, asyncio.Future] = {}
        self._stop = asyncio.Event()

    async def setup(self) -> None:
        await self._pool.execute(OUTBOX_TABLE_SQL)

    async def delegate(self, target: str, task_type: str, payload: dict, *,
                       deadline: float | None = None) -> str:
        """Durably enqueue + send a task to ``target``. Returns the ``task_id``."""
        task_id = new_task_id()
        channel = self._channel_for(target)
        ddl = time.time() + (deadline if deadline is not None else self._default_deadline)
        await self._pool.execute(
            "INSERT INTO agent_task_outbox (task_id, target, task_type, payload, request_channel, "
            "reply_channel, deadline_ts, next_resend_at) VALUES "
            "($1,$2,$3,$4::jsonb,$5,$5,to_timestamp($6), now() + make_interval(secs => $7::float8)) "
            "ON CONFLICT (task_id) DO NOTHING",
            task_id, target, task_type, json.dumps(payload), channel, ddl, self._resend_interval)
        req = TaskRequest(task_id=task_id, task_type=task_type, payload=payload,
                          reply_to=channel, deadline_ts=ddl, attempt=1)
        await self._safe_send(channel, req.to_body())
        return task_id

    async def delegate_and_wait(self, target: str, task_type: str, payload: dict, *,
                                timeout: float = 120.0, deadline: float | None = None):
        """Delegate + await the result (or None on timeout — the outbox keeps resending)."""
        task_id = await self.delegate(target, task_type, payload, deadline=deadline)
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._waiters[task_id] = fut
        # The result can land DURING delegate() (a fast/in-process bus) — i.e. before the waiter
        # existed. Reconcile against the durable outbox row so we never miss an early result.
        row = await self._pool.fetchrow(
            "SELECT status, result, error FROM agent_task_outbox WHERE task_id=$1", task_id)
        if row is not None and row["status"] in ("done", "gaveup") and not fut.done():
            fut.set_result(self._result_from_outbox(task_id, row))
        try:
            return await asyncio.wait_for(fut, timeout=timeout)
        except asyncio.TimeoutError:
            return None
        finally:
            self._waiters.pop(task_id, None)

    @staticmethod
    def _result_from_outbox(task_id: str, row) -> "TaskResult":
        """Rebuild the TaskResult from a terminal outbox row (error set → failed, else completed)."""
        if row["error"]:
            return TaskResult.failed(task_id, row["error"])
        result = row["result"]
        if isinstance(result, str):
            try:
                result = json.loads(result)
            except Exception:  # noqa: BLE001
                result = None
        return TaskResult.completed(task_id, result)

    # ── bus handlers ───────────────────────────────────────────────
    async def on_result(self, meta: dict, body) -> None:
        """Registered for KIND_RESULT."""
        try:
            res = TaskResult.from_body(body)
        except Exception:  # noqa: BLE001
            return
        # Mark terminal (idempotent). The on_complete callback is delivered DURABLY by the
        # notification loop (survives a crash/callback-error between commit and delivery — codex
        # #1), not inline here. The in-memory waiter (delegate_and_wait) is resolved directly.
        await self._pool.execute(
            "UPDATE agent_task_outbox SET status='done', result=$2::jsonb, error=$3, updated_at=now() "
            "WHERE task_id=$1 AND status='pending'",
            res.task_id, (json.dumps(res.result) if res.result is not None else None), res.error)
        fut = self._waiters.get(res.task_id)
        if fut is not None and not fut.done():
            fut.set_result(res)

    async def on_progress(self, meta: dict, body) -> None:
        """Registered for KIND_PROGRESS (advisory) — push out the resend timer while work runs."""
        try:
            prog = TaskProgress.from_body(body)
        except Exception:  # noqa: BLE001
            return
        await self._pool.execute(
            "UPDATE agent_task_outbox SET next_resend_at = now() + make_interval(secs => $2::float8), "
            "updated_at=now() WHERE task_id=$1 AND status='pending'", prog.task_id, self._resend_interval)

    # ── resend loop ────────────────────────────────────────────────
    async def run_forever(self) -> None:
        await self.setup()
        while not self._stop.is_set():
            try:
                await self._gaveup_expired()
                await self._resend_due()
                await self._deliver_notifications()
            except Exception:  # noqa: BLE001
                log.warning("[client] loop error", exc_info=True)
            await asyncio.sleep(self._poll)

    async def _deliver_notifications(self) -> None:
        """Durably deliver on_complete for terminal (done|gaveup) rows not yet notified. Marks
        notified only AFTER the callback returns → at-least-once (the callback MUST be idempotent);
        a crash or a raising callback leaves the row for the next tick. Covers deadline gaveup too
        (codex #5)."""
        if self._on_complete is None:
            return
        rows = await self._pool.fetch(
            "SELECT task_id, result, error FROM agent_task_outbox "
            "WHERE status IN ('done','gaveup') AND NOT notified ORDER BY updated_at LIMIT 50")
        for r in rows:
            res = self._result_from_outbox(r["task_id"], r)
            try:
                await self._on_complete(r["task_id"], res)
            except Exception:  # noqa: BLE001 — leave notified=false → retried; bump updated_at so a
                # persistently-failing callback rotates to the BACK and never starves healthy rows
                # queued behind it (codex #1 residual — fair scheduling).
                log.warning("[client] on_complete failed for %s", r["task_id"], exc_info=True)
                await self._pool.execute(
                    "UPDATE agent_task_outbox SET updated_at=now() WHERE task_id=$1", r["task_id"])
                continue
            await self._pool.execute(
                "UPDATE agent_task_outbox SET notified=TRUE, updated_at=now() WHERE task_id=$1",
                r["task_id"])

    async def stop(self) -> None:
        self._stop.set()

    async def _gaveup_expired(self) -> None:
        rows = await self._pool.fetch(
            "UPDATE agent_task_outbox SET status='gaveup', error='deadline exceeded', updated_at=now() "
            "WHERE status='pending' AND deadline_ts IS NOT NULL AND deadline_ts < now() RETURNING task_id")
        for r in rows:
            fut = self._waiters.get(r["task_id"])
            if fut is not None and not fut.done():
                fut.set_result(TaskResult.failed(r["task_id"], "deadline exceeded"))

    async def _resend_due(self) -> None:
        rows = await self._pool.fetch(
            "SELECT task_id, task_type, payload, request_channel, deadline_ts, attempts "
            "FROM agent_task_outbox WHERE status='pending' AND next_resend_at < now() "
            "ORDER BY next_resend_at LIMIT 50")
        for r in rows:
            payload = r["payload"]
            if isinstance(payload, str):
                try:
                    payload = json.loads(payload)
                except Exception:  # noqa: BLE001
                    payload = {}
            ddl = r["deadline_ts"].timestamp() if r["deadline_ts"] is not None else None
            interval = min(self._resend_interval * (self._resend_backoff ** (int(r["attempts"]) + 1)),
                           self._resend_max)
            await self._pool.execute(
                "UPDATE agent_task_outbox SET attempts = attempts + 1, "
                "next_resend_at = now() + make_interval(secs => $2::float8), updated_at=now() "
                "WHERE task_id=$1 AND status='pending'", r["task_id"], interval)
            req = TaskRequest(task_id=r["task_id"], task_type=r["task_type"], payload=payload,
                              reply_to=r["request_channel"], deadline_ts=ddl,
                              attempt=int(r["attempts"]) + 2)
            await self._safe_send(r["request_channel"], req.to_body())

    async def _safe_send(self, channel: str, body: bytes) -> None:
        try:
            await self._send(channel, KIND_REQUEST, body)
        except Exception:  # noqa: BLE001 — the resend loop will retry
            log.warning("[client] send task.request failed on %s", channel, exc_info=True)
