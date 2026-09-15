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

import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

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


@runtime_checkable
class Executor(Protocol):
    """A worker agent injects one of these. Refinery agents' executors call the shared
    ``refinery-core`` API; a cognitive agent's executor may run its tool loop. It MUST be
    idempotent per ``task_id`` where the effect is observable (the inbox dedups delivery, but
    a redelivered-after-partial task can re-run the executor)."""
    async def run(self, task_type: str, payload: dict[str, Any]) -> ExecOutcome: ...


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
    task_id      TEXT PRIMARY KEY,
    requester    TEXT NOT NULL,
    task_type    TEXT NOT NULL,
    payload      JSONB NOT NULL DEFAULT '{}',
    reply_to     TEXT NOT NULL,
    msg_id       TEXT,
    status       TEXT NOT NULL DEFAULT 'queued',   -- queued|executing|completed|failed|dead
    owner_token  TEXT,
    lease_until  TIMESTAMP WITH TIME ZONE,
    attempts     INTEGER NOT NULL DEFAULT 0,
    result       JSONB,
    error        TEXT,
    created_at   TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    updated_at   TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_task_inbox_claimable
    ON agent_task_inbox (status, lease_until)
    WHERE status IN ('queued', 'executing');
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
    created_at      TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_task_outbox_resend
    ON agent_task_outbox (status, next_resend_at)
    WHERE status = 'pending';
"""


async def provision(pool) -> None:
    """Create both ledgers (idempotent). Called by inbox/client setup on first boot."""
    await pool.execute(INBOX_TABLE_SQL)
    await pool.execute(OUTBOX_TABLE_SQL)
