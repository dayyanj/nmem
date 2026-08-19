"""
Skills — the conscious record of "a process that worked / one that didn't".

nmem is the front door to the cognitive system: the host records skill outcomes
*here*, nmem stores them durably and vectorized, and forwards to the registered
cognitive backend (nmem-sym), which holds the live plasticity-scored procedure
(LTP/LTD, myelination) in `symbol_procedures`. Lifecycle events flow back via
`record_event`.

This is the exact shape of the commitments subsystem (see commitments.py):
IoC backend registration, DB-as-queue mirroring under an `asyncio.Lock` with an
under-lock re-check, a reverse channel, and Option-B ownership linked by
`sym_procedure_id`. With no backend attached, skills are still recorded,
embedded, searched, decayed and superseded entirely within nmem (and mirror
once a backend registers), so nmem stays standalone.

Everything is gated by `config.skills.enabled` at the manager level: when off,
record/find/reinforce/supersede/mirror are inert. The MCP "skills disabled"
string is just a friendlier surface of the same library-wide guard.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class SkillInfo:
    id: int
    name: str
    what: str
    outcome: str
    worked: bool
    success_count: int
    trial_count: int
    status: str
    salience: float = 1.0
    sym_procedure_id: int | None = None
    superseded_by_id: int | None = None
    agent_id: str | None = None
    project_scope: str | None = None
    similarity: float | None = None       # populated by find()
    created_at: datetime | None = None


# sym procedure lifecycle event → nmem skill terminal status.
_TERMINAL = {"superseded": "superseded", "retired": "retired", "abandoned": "retired"}


class SkillManager:
    """Conscious skills + forwarding to a registered cognitive backend."""

    def __init__(self, db, emit, config=None, embedder=None):
        self._db = db
        self._emit = emit                    # async mem._emit — host-facing events
        self._config = config                # NmemConfig — scope + skills settings
        self._embedder = embedder            # embedding provider (sync .embed)
        self._backend: Any = None            # registered subconscious backend
        # Serializes mirroring so an inline record() and a concurrent
        # flush_pending() can't double-mirror the same skill.
        self._mirror_lock = asyncio.Lock()

    # ── config helpers ────────────────────────────────────────

    @property
    def _enabled(self) -> bool:
        cfg = getattr(self._config, "skills", None)
        return bool(getattr(cfg, "enabled", False))

    def _scope(self) -> str | None:
        return getattr(self._config, "project_scope", None)

    def _skills_cfg(self):
        return getattr(self._config, "skills", None)

    async def _embed(self, text: str) -> list[float] | None:
        """Embed like every other tier. The noop provider returns deterministic
        hash vectors (not None), so find() works even with noop."""
        if self._embedder is None or not text:
            return None
        try:
            return await asyncio.to_thread(self._embedder.embed, text)
        except Exception as e:   # pragma: no cover
            logger.debug("Skill embedding failed: %s", e)
            return None

    # ── backend registration (IoC) ────────────────────────────

    def register_backend(self, backend: Any) -> None:
        """A cognitive backend (nmem-sym's SymbolBridge) plugs itself in here."""
        self._backend = backend
        logger.info("Skills backend registered: %s", type(backend).__name__)
        # Nothing mirrors while skills are disabled — a backend attaching to a
        # skills-off instance must not push rows left over from a prior enabled
        # run (flush_pending/_mirror also self-guard on _enabled).
        if not self._enabled:
            return
        # Mirror skills recorded before the backend attached. connect() is sync,
        # so schedule when a loop is running; if not, the backend's first tick
        # (nmem-sym calls flush_pending()) covers it. flush_pending is
        # idempotent, so both firing is harmless.
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        loop.create_task(self._safe_flush())

    async def _safe_flush(self) -> None:
        try:
            n = await self.flush_pending()
            if n:
                logger.info("Mirrored %d queued skill(s) on backend attach", n)
        except Exception as e:
            logger.warning("Skill flush on backend attach failed: %s", e)

    @property
    def has_backend(self) -> bool:
        return self._backend is not None

    # ── record / find ─────────────────────────────────────────

    async def record(
        self,
        what: str,
        *,
        outcome: str = "",
        worked: bool = True,
        name: str | None = None,
        agent_id: str | None = None,
        project_scope: str | None = ...,     # sentinel → inherit instance scope
    ) -> SkillInfo | None:
        """Record a skill outcome ("worked / didn't work").

        If a near-duplicate active skill exists (cosine ≥ dedup_threshold),
        reinforce it instead of inserting — the conscious-layer LTP/LTD. Returns
        None when skills are disabled.
        """
        if not self._enabled:
            return None
        from nmem.db.models import SkillModel

        if project_scope is ...:
            project_scope = self._scope()
        name = name or (what[:80] if what else "skill")
        embedding = await self._embed(what)

        # Coalesce into a near-duplicate active skill if one exists.
        if embedding is not None:
            dup = await self._find_duplicate(embedding, project_scope, agent_id)
            if dup is not None:
                await self.reinforce(dup, worked)
                return await self.get(dup)

        async with self._db.session() as session:
            row = SkillModel(
                name=name, what=what, outcome=outcome, worked=worked,
                success_count=1 if worked else 0, trial_count=1,
                trigger_embedding=embedding, salience=1.0, status="active",
                agent_id=agent_id, project_scope=project_scope,
            )
            session.add(row)
            await session.flush()
            info = self._to_info(row)

        await self._mirror(info)
        await self._emit("skill.recorded", {
            "id": info.id, "name": info.name, "worked": worked,
            "agent_id": agent_id,
        })
        return info

    async def find(
        self,
        query: str,
        *,
        limit: int | None = None,
        project_scope: str | None = ...,
        agent_id: str | None = None,
    ) -> list[SkillInfo]:
        """Find active skills matching a query by trigger-embedding similarity.

        Scope filter = scoped OR global (like LTM search), so globally useful
        skills aren't hidden inside a scoped project. Empty when disabled.
        """
        if not self._enabled:
            return []
        cfg = self._skills_cfg()
        limit = limit or getattr(cfg, "find_limit", 3)
        threshold = getattr(cfg, "similarity_threshold", 0.35)
        embedding = await self._embed(query)
        if embedding is None:
            return []
        if project_scope is ...:
            project_scope = self._scope()

        from sqlalchemy import text as sa_text
        embedding_str = f"[{','.join(str(x) for x in embedding)}]"
        params: dict[str, Any] = {
            "embedding_vec": embedding_str,
            "threshold": threshold,
            "limit": limit,
        }
        # Scope: "*" = cross-scope (all), None = global only, else scoped OR global
        # (same convention as LTM search — see tiers/ltm.py).
        if project_scope == "*":
            scope_sql = ""
        elif project_scope is None:
            scope_sql = "AND project_scope IS NULL"
        else:
            scope_sql = "AND (project_scope = :scope OR project_scope IS NULL)"
            params["scope"] = project_scope
        agent_sql = ""
        if agent_id is not None:
            agent_sql = "AND (agent_id = :agent_id OR agent_id IS NULL)"
            params["agent_id"] = agent_id

        sql = sa_text(f"""
            SELECT id, 1 - (trigger_embedding <=> CAST(:embedding_vec AS vector)) AS similarity
            FROM nmem_skills
            WHERE status = 'active'
              AND trigger_embedding IS NOT NULL
              {scope_sql}
              {agent_sql}
              AND 1 - (trigger_embedding <=> CAST(:embedding_vec AS vector)) >= :threshold
            ORDER BY (trial_count > 0) DESC,
                     trigger_embedding <=> CAST(:embedding_vec AS vector)
            LIMIT :limit
        """)
        async with self._db.session() as session:
            rows = (await session.execute(sql, params)).all()
            if not rows:
                return []
            ids = [r[0] for r in rows]
            sim_by_id = {r[0]: float(r[1]) for r in rows}
            from sqlalchemy import select
            from nmem.db.models import SkillModel
            models = (await session.execute(
                select(SkillModel).where(SkillModel.id.in_(ids)))).scalars().all()
        by_id = {m.id: m for m in models}
        out: list[SkillInfo] = []
        for sid in ids:   # preserve rank order
            m = by_id.get(sid)
            if m is None:
                continue
            info = self._to_info(m)
            info.similarity = sim_by_id.get(sid)
            out.append(info)
        return out

    async def get(self, skill_id: int) -> SkillInfo | None:
        from sqlalchemy import select
        from nmem.db.models import SkillModel
        async with self._db.session() as session:
            row = (await session.execute(
                select(SkillModel).where(SkillModel.id == skill_id))
            ).scalar_one_or_none()
            return self._to_info(row) if row is not None else None

    async def list(self, status: str = "active") -> list[SkillInfo]:
        if not self._enabled:
            return []
        from sqlalchemy import select
        from nmem.db.models import SkillModel
        scope = self._scope()
        async with self._db.session() as session:
            stmt = select(SkillModel).where(SkillModel.status == status)
            if scope == "*":
                pass  # cross-scope: no filter
            elif scope is None:
                stmt = stmt.where(SkillModel.project_scope.is_(None))
            else:
                stmt = stmt.where(
                    (SkillModel.project_scope == scope)
                    | (SkillModel.project_scope.is_(None)))
            rows = (await session.execute(
                stmt.order_by(SkillModel.updated_at.desc()))).scalars().all()
            return [self._to_info(r) for r in rows]

    # ── plasticity + lifecycle ────────────────────────────────

    async def reinforce(self, skill_id: int, success: bool) -> bool:
        """Bump trial (and success) counts; forward to the backend if linked."""
        if not self._enabled:
            return False
        from sqlalchemy import select
        from nmem.db.models import SkillModel
        sym_id = None
        async with self._db.session() as session:
            row = (await session.execute(
                select(SkillModel).where(SkillModel.id == skill_id))
            ).scalar_one_or_none()
            if row is None or row.status != "active":
                return False
            row.trial_count += 1
            if success:
                row.success_count += 1
            row.worked = success
            sym_id = row.sym_procedure_id
        if self._backend is not None and sym_id is not None:
            try:
                await self._backend.reinforce_skill(sym_id, success)
            except Exception as e:
                logger.warning("Skill %d: reinforce forward failed: %s", skill_id, e)
        await self._emit("skill.reinforced", {"id": skill_id, "success": success})
        return True

    async def supersede(self, old_id: int, new_id: int | None = None) -> bool:
        """Mark a skill superseded (optionally by new_id); forward if linked."""
        if not self._enabled:
            return False
        from sqlalchemy import select
        from nmem.db.models import SkillModel
        sym_id = None
        async with self._db.session() as session:
            row = (await session.execute(
                select(SkillModel).where(SkillModel.id == old_id))
            ).scalar_one_or_none()
            if row is None:
                return False
            row.status = "superseded"
            row.superseded_by_id = new_id
            row.resolved_at = datetime.utcnow()
            sym_id = row.sym_procedure_id
        if self._backend is not None and sym_id is not None:
            try:
                await self._backend.supersede_skill(sym_id)
            except Exception as e:
                logger.warning("Skill %d: supersede forward failed: %s", old_id, e)
        await self._emit("skill.superseded", {"id": old_id, "superseded_by": new_id})
        return True

    # ── reverse channel (backend → nmem) ──────────────────────

    async def record_event(self, kind: str, sym_procedure_id: int,
                           data: dict | None = None) -> None:
        """Backend reports a procedure lifecycle transition; update the record +
        emit for the host. Monotonic + best-effort idempotent:

        - `retired`/`superseded`/`abandoned` are terminal and FULLY sticky —
          once a skill reaches one, no later event (terminal or not) changes its
          status. The first terminal wins.
        - `reinforced` is idempotent when the backend sends the authoritative
          `success_count`/`trial_count` (nmem SETs them, so re-delivery is a
          no-op). Without absolute counts it falls back to incrementing, deduped
          only against an immediately-repeated `event_id` — so the reliable
          idempotency contract is: send absolute counts, or a fresh `event_id`.
        """
        if not self._enabled:
            return
        from sqlalchemy import select
        from nmem.db.models import SkillModel

        data = data or {}
        event_id = data.get("event_id")
        row_id = None
        applied = True
        new_status = _TERMINAL.get(kind)
        async with self._db.session() as session:
            row = (await session.execute(
                select(SkillModel).where(
                    SkillModel.sym_procedure_id == sym_procedure_id))
            ).scalar_one_or_none()
            if row is not None:
                row_id = row.id
                if event_id is not None and row.last_sym_event == str(event_id):
                    applied = False                       # adjacent duplicate
                elif row.status in ("retired", "superseded"):
                    applied = False                       # terminal: first wins, fully sticky
                else:
                    if new_status is not None:
                        row.status = new_status
                        row.resolved_at = datetime.utcnow()
                    elif kind == "reinforced":
                        # Prefer authoritative absolute counts (idempotent SET).
                        if "trial_count" in data:
                            row.trial_count = int(data["trial_count"])
                            if "success_count" in data:
                                row.success_count = int(data["success_count"])
                        else:
                            row.trial_count += 1
                            if data.get("success", True):
                                row.success_count += 1
                    if event_id is not None:
                        row.last_sym_event = str(event_id)
        if applied:
            await self._emit(f"skill.{kind}", {
                "id": row_id, "sym_procedure_id": sym_procedure_id, **data})

    # ── mirroring to the backend ──────────────────────────────

    async def flush_pending(self) -> int:
        """Mirror skills recorded while no backend was attached (the DB is the
        queue: active + no sym id). Called after a backend registers."""
        if self._backend is None or not self._enabled:
            return 0
        from sqlalchemy import select
        from nmem.db.models import SkillModel
        async with self._db.session() as session:
            rows = (await session.execute(
                select(SkillModel).where(
                    SkillModel.status == "active",
                    SkillModel.sym_procedure_id.is_(None)))).scalars().all()
            pending = [self._to_info(r) for r in rows]
        for info in pending:
            await self._mirror(info)
        return len(pending)

    async def _mirror(self, info: SkillInfo) -> None:
        if self._backend is None or not self._enabled:
            return   # queued; flush_pending() mirrors once a backend attaches
        from sqlalchemy import select
        from nmem.db.models import SkillModel
        async with self._mirror_lock:
            # Re-check under the lock: a concurrent record()/flush may already
            # have mirrored this skill (both match "active + null sym"), or it
            # may have been superseded/retired between selection and here — in
            # which case mirroring would register a procedure for a dead record.
            async with self._db.session() as session:
                row = (await session.execute(
                    select(SkillModel.sym_procedure_id, SkillModel.status)
                    .where(SkillModel.id == info.id))).first()
            if row is None or row[0] is not None:
                if row is not None:
                    info.sym_procedure_id = row[0]
                return
            if row[1] != "active":
                return   # superseded/retired before we could mirror
            try:
                obl = await self._backend.register_skill(
                    info.name, info.what, info.outcome,
                    worked=info.worked, success_count=info.success_count,
                    trial_count=info.trial_count)
                sym_id = getattr(obl, "id", obl)
                if sym_id is not None:
                    # The backend call awaited above; a concurrent supersede()/
                    # abandon() (which don't take the mirror lock) may have moved
                    # the row off 'active' in the meantime. Link only if it's
                    # still active + unlinked; otherwise the procedure we just
                    # created is an orphan — abandon it rather than link a dead
                    # skill to a live procedure.
                    linked = await self._set_sym_id(info.id, sym_id)
                    if linked:
                        info.sym_procedure_id = sym_id
                    else:
                        await self._abandon_orphan(sym_id)
            except Exception as e:
                logger.warning("Skill %d: mirror to backend failed: %s", info.id, e)

    # ── helpers ───────────────────────────────────────────────

    async def _find_duplicate(self, embedding: list[float],
                              project_scope: str | None,
                              agent_id: str | None) -> int | None:
        """Return the id of an active skill whose trigger is ≥ dedup_threshold
        similar (the "same skill" bar), else None."""
        cfg = self._skills_cfg()
        threshold = getattr(cfg, "dedup_threshold", 0.85)
        from sqlalchemy import text as sa_text
        embedding_str = f"[{','.join(str(x) for x in embedding)}]"
        params: dict[str, Any] = {"embedding_vec": embedding_str, "threshold": threshold}
        if project_scope is None:
            scope_sql = "AND project_scope IS NULL"
        else:
            scope_sql = "AND project_scope = :scope"
            params["scope"] = project_scope
        sql = sa_text(f"""
            SELECT id
            FROM nmem_skills
            WHERE status = 'active'
              AND trigger_embedding IS NOT NULL
              {scope_sql}
              AND 1 - (trigger_embedding <=> CAST(:embedding_vec AS vector)) >= :threshold
            ORDER BY trigger_embedding <=> CAST(:embedding_vec AS vector)
            LIMIT 1
        """)
        async with self._db.session() as session:
            row = (await session.execute(sql, params)).first()
        return row[0] if row is not None else None

    async def _set_sym_id(self, skill_id: int, sym_id: int) -> bool:
        """Link the skill to its sym procedure, but only while it's still active
        and unlinked. Returns True if the link was written."""
        from sqlalchemy import update
        from nmem.db.models import SkillModel
        async with self._db.session() as session:
            result = await session.execute(
                update(SkillModel)
                .where(SkillModel.id == skill_id,
                       SkillModel.status == "active",
                       SkillModel.sym_procedure_id.is_(None))
                .values(sym_procedure_id=sym_id))
            return result.rowcount > 0

    async def _abandon_orphan(self, sym_id: int) -> None:
        """Best-effort cleanup of a procedure we created for a skill that went
        non-active before we could link it."""
        try:
            if hasattr(self._backend, "abandon_skill"):
                await self._backend.abandon_skill(sym_id)
        except Exception as e:
            logger.debug("Orphan procedure %s cleanup failed: %s", sym_id, e)

    @staticmethod
    def _to_info(row) -> SkillInfo:
        return SkillInfo(
            id=row.id, name=row.name, what=row.what, outcome=row.outcome,
            worked=row.worked, success_count=row.success_count,
            trial_count=row.trial_count, status=row.status, salience=row.salience,
            sym_procedure_id=row.sym_procedure_id,
            superseded_by_id=row.superseded_by_id, agent_id=row.agent_id,
            project_scope=row.project_scope, created_at=row.created_at)
