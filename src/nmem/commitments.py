"""
Commitments — the conscious record of external obligations.

nmem is the front door to the cognitive system: the host imposes commitments
*here*, nmem records them durably, and forwards to the registered cognitive
backend (nmem-sym), which holds the live obligation pressure (deadline curve,
meta-arbiter, temperament). Lifecycle events flow back via `record_event`.

Ownership is Option B (see the nmem-sym dev-note): nmem keeps the narrative
record; nmem-sym owns the live ledger, linked by `sym_obligation_id`. The record
stores everything the backend would need to re-impose, so nmem could become the
source of truth later. With no backend attached, commitments are still recorded
(and mirrored when a backend registers), so nmem stays standalone.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class CommitmentInfo:
    id: int
    requester: str
    description: str
    deadline: datetime | None
    status: str
    source: str
    sym_obligation_id: int | None = None
    authority: float = 0.5
    importance: float = 1.0
    created_at: datetime | None = None


# nmem-sym obligation status/event → nmem commitment status.
_TERMINAL = {"fulfilled": "fulfilled", "breached": "breached", "abandoned": "abandoned"}


class CommitmentManager:
    """Conscious commitments + forwarding to a registered cognitive backend."""

    def __init__(self, db, emit):
        self._db = db
        self._emit = emit                    # async mem._emit — host-facing events
        self._backend: Any = None            # registered subconscious backend
        self._requester_ids: dict[str, int] = {}   # name → backend requester id (cache)
        import asyncio
        # Serializes mirroring so an inline impose() and a concurrent
        # flush_pending() can't double-mirror the same commitment.
        self._mirror_lock = asyncio.Lock()

    # ── backend registration (IoC) ────────────────────────────

    def register_backend(self, backend: Any) -> None:
        """A cognitive backend (nmem-sym's SymbolBridge) plugs itself in here."""
        self._backend = backend
        logger.info("Cognitive backend registered: %s", type(backend).__name__)
        # Mirror commitments imposed before the backend attached. connect() is
        # sync, so schedule it when a loop is running; if not, the backend's first
        # tick (nmem-sym calls flush_pending()) covers it. flush_pending is
        # idempotent, so both firing is harmless.
        import asyncio
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        loop.create_task(self._safe_flush())

    async def _safe_flush(self) -> None:
        try:
            n = await self.flush_pending()
            if n:
                logger.info("Mirrored %d queued commitment(s) on backend attach", n)
        except Exception as e:
            logger.warning("Flush on backend attach failed: %s", e)

    @property
    def has_backend(self) -> bool:
        return self._backend is not None

    # ── impose / lifecycle ────────────────────────────────────

    async def impose(
        self,
        requester: str,
        description: str,
        deadline: datetime | None = None,
        *,
        authority: float = 0.5,
        importance: float = 1.0,
        source: str = "external",
    ) -> CommitmentInfo:
        """Record a commitment and forward it to the subconscious backend."""
        from nmem.db.models import CommitmentModel

        async with self._db.session() as session:
            row = CommitmentModel(
                requester=requester, authority=authority, description=description,
                deadline=deadline, importance=importance, status="open", source=source,
            )
            session.add(row)
            await session.flush()
            info = CommitmentInfo(
                id=row.id, requester=requester, description=description,
                deadline=deadline, status="open", source=source,
                authority=authority, importance=importance, created_at=row.created_at,
            )

        await self._mirror(info)
        await self._emit("commitment.imposed", {
            "id": info.id, "requester": requester, "description": description,
            "source": source,
        })
        return info

    async def confirm(self, commitment_id: int) -> bool:
        """External acknowledgement that a commitment is met → fulfilled.
        Returns False if no such commitment exists."""
        found, sym_id = await self._update_status(commitment_id, "fulfilled", "confirmed")
        if not found:
            return False
        if self._backend is not None and sym_id is not None:
            await self._backend.confirm_obligation(sym_id)
        await self._emit("commitment.fulfilled", {"id": commitment_id})
        return True

    async def abandon(self, commitment_id: int) -> bool:
        found, sym_id = await self._update_status(commitment_id, "abandoned", "abandoned")
        if not found:
            return False
        if self._backend is not None and sym_id is not None:
            await self._backend.abandon_obligation(sym_id)
        return True

    async def renegotiate(self, commitment_id: int, new_deadline: datetime) -> bool:
        from sqlalchemy import select
        from nmem.db.models import CommitmentModel
        async with self._db.session() as session:
            row = (await session.execute(
                select(CommitmentModel).where(CommitmentModel.id == commitment_id))
            ).scalar_one_or_none()
            if row is None:
                return False
            row.deadline = new_deadline
            sym_id = row.sym_obligation_id
        if self._backend is not None and sym_id is not None:
            await self._backend.renegotiate_obligation(sym_id, new_deadline)
        return True

    async def list(self, status: str = "open") -> list[CommitmentInfo]:
        from sqlalchemy import select
        from nmem.db.models import CommitmentModel
        async with self._db.session() as session:
            rows = (await session.execute(
                select(CommitmentModel)
                .where(CommitmentModel.status == status)
                .order_by(CommitmentModel.created_at.desc()))).scalars().all()
            return [self._to_info(r) for r in rows]

    # ── reverse channel (backend → nmem) ──────────────────────

    async def record_event(self, kind: str, sym_obligation_id: int,
                           data: dict | None = None) -> None:
        """Backend reports a lifecycle transition; update the record + emit for
        the host. `kind` ∈ {fulfilled, breached, missed, acting, ...}."""
        from sqlalchemy import select
        from nmem.db.models import CommitmentModel

        row_id = None
        new_status = _TERMINAL.get(kind)
        async with self._db.session() as session:
            row = (await session.execute(
                select(CommitmentModel).where(
                    CommitmentModel.sym_obligation_id == sym_obligation_id))
            ).scalar_one_or_none()
            if row is not None:
                row_id = row.id
                if new_status is not None:
                    row.status = new_status
                    row.resolution_outcome = kind
                    row.resolved_at = datetime.utcnow()
        await self._emit(f"commitment.{kind}", {
            "id": row_id, "sym_obligation_id": sym_obligation_id, **(data or {})})

    # ── mirroring to the backend ──────────────────────────────

    async def flush_pending(self) -> int:
        """Mirror commitments imposed while no backend was attached (the DB is
        the queue: open + no sym id). Called after a backend registers."""
        if self._backend is None:
            return 0
        from sqlalchemy import select
        from nmem.db.models import CommitmentModel
        async with self._db.session() as session:
            rows = (await session.execute(
                select(CommitmentModel).where(
                    CommitmentModel.status == "open",
                    CommitmentModel.sym_obligation_id.is_(None)))).scalars().all()
            pending = [self._to_info(r) for r in rows]
        for info in pending:
            await self._mirror(info)
        return len(pending)

    async def _mirror(self, info: CommitmentInfo) -> None:
        if self._backend is None:
            return   # queued; flush_pending() mirrors once a backend attaches
        from sqlalchemy import select
        from nmem.db.models import CommitmentModel
        async with self._mirror_lock:
            # Re-check under the lock: a concurrent impose()/flush may already
            # have mirrored this commitment (both match "open + null sym").
            async with self._db.session() as session:
                current = await session.scalar(
                    select(CommitmentModel.sym_obligation_id)
                    .where(CommitmentModel.id == info.id))
            if current is not None:
                info.sym_obligation_id = current
                return
            try:
                req_id = self._requester_ids.get(info.requester)
                if req_id is None:
                    req = await self._backend.register_requestor(info.requester, info.authority)
                    req_id = getattr(req, "id", req)
                    if req_id is not None:
                        self._requester_ids[info.requester] = req_id
                obl = await self._backend.impose_obligation(
                    req_id, info.description, info.deadline, importance=info.importance)
                sym_id = getattr(obl, "id", obl)
                if sym_id is not None:
                    info.sym_obligation_id = sym_id
                    await self._set_sym_id(info.id, sym_id)
            except Exception as e:
                logger.warning("Commitment %d: mirror to backend failed: %s", info.id, e)

    # ── helpers ───────────────────────────────────────────────

    async def _update_status(self, commitment_id: int, status: str,
                             outcome: str) -> tuple[bool, int | None]:
        """Returns (found, sym_obligation_id). `found` distinguishes a missing
        commitment from one that exists but has no linked obligation."""
        from sqlalchemy import select
        from nmem.db.models import CommitmentModel
        async with self._db.session() as session:
            row = (await session.execute(
                select(CommitmentModel).where(CommitmentModel.id == commitment_id))
            ).scalar_one_or_none()
            if row is None:
                return (False, None)
            row.status = status
            row.resolution_outcome = outcome
            row.resolved_at = datetime.utcnow()
            return (True, row.sym_obligation_id)

    async def _set_sym_id(self, commitment_id: int, sym_id: int) -> None:
        from sqlalchemy import update
        from nmem.db.models import CommitmentModel
        async with self._db.session() as session:
            await session.execute(
                update(CommitmentModel)
                .where(CommitmentModel.id == commitment_id)
                .values(sym_obligation_id=sym_id))

    @staticmethod
    def _to_info(row) -> CommitmentInfo:
        return CommitmentInfo(
            id=row.id, requester=row.requester, description=row.description,
            deadline=row.deadline, status=row.status, source=row.source,
            sym_obligation_id=row.sym_obligation_id, authority=row.authority,
            importance=row.importance, created_at=row.created_at)
