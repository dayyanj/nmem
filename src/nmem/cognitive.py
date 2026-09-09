"""
Cognitive capabilities — deja vu, counterfactual reasoning, curiosity signals.

These features make agents genuinely learn from experience rather than just
recall facts.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from nmem.types import DelegationRecord, CuriositySignalInfo

if TYPE_CHECKING:
    from nmem.db.session import DatabaseManager
    from nmem.providers.embedding.base import EmbeddingProvider
    from nmem.providers.llm.base import LLMProvider

logger = logging.getLogger(__name__)


# Weight of accumulated recurrence in a curiosity signal's composite score.
# A problem re-encountered many times becomes maximally salient even if any
# single observation was mild — this is nmem's per-topic pressure buildup.
_CURIOSITY_RECURRENCE_WEIGHT = 0.4
# Recurrence added each time the same signal is re-emitted (saturates at 1.0).
_CURIOSITY_RECURRENCE_STEP = 0.25


def _curiosity_composite(
    novelty: float,
    uncertainty: float,
    conflict: float,
    business_impact: float,
    recurrence: float = 0.0,
) -> float:
    """Composite salience of a curiosity signal, including recurrence.

    With recurrence=0 this is byte-for-byte the original formula, so freshly
    emitted signals are unchanged. Repeated observations of the same problem
    raise recurrence, which lifts the composite toward its 1.0 ceiling.
    """
    base = (
        novelty * 0.3
        + uncertainty * 0.2
        + conflict * 0.2
        + business_impact * 0.3
    )
    return min(1.0, base + recurrence * _CURIOSITY_RECURRENCE_WEIGHT)


# Sentinel: "do not filter curiosity by project at all" (legacy global reads
# when an engine was constructed without a config).
_NO_SCOPE = object()


class CognitiveEngine:
    """Cognitive capabilities for agent memory."""

    def __init__(
        self,
        db: DatabaseManager,
        embedding: EmbeddingProvider,
        llm: LLMProvider,
        config=None,
    ):
        self._db = db
        self._embedding = embedding
        self._llm = llm
        # Optional NmemConfig — gives curiosity the same instance project-scope
        # contract as commitments (emit stamps it, reads default to it).
        self._config = config

    def _resolve_curiosity_scope(self, project_scope):
        """Resolve the read-scope sentinel: ``...`` = this engine's configured
        project scope (``None`` → ``IS NULL``); a concrete value (incl. ``None``)
        is honored as-is; with no config and no explicit scope, do not filter."""
        if project_scope is not ...:
            return project_scope
        if self._config is not None:
            return self._config.project_scope
        return _NO_SCOPE

    async def find_similar_experience(
        self,
        instruction: str,
        agent_id: str,
        *,
        threshold: float = 0.8,
        top_k: int = 1,
    ) -> list[DelegationRecord]:
        """Search past delegations for similar instructions (deja vu).

        Args:
            instruction: Current task instruction.
            agent_id: Agent that would execute the task.
            threshold: Minimum cosine similarity.
            top_k: Maximum matches.

        Returns:
            List of similar past DelegationRecord objects.
        """
        from sqlalchemy import text as sa_text

        emb = await asyncio.to_thread(self._embedding.embed, instruction[:500])
        embedding_str = f"[{','.join(str(x) for x in emb)}]"

        try:
            async with self._db.session() as session:
                result = await session.execute(
                    sa_text("""
                        SELECT id, delegating_agent, target_agent, task_type,
                               instruction, status, result_summary,
                               created_at, completed_at,
                               1 - (embedding <=> CAST(:embedding AS vector)) AS similarity
                        FROM nmem_delegations
                        WHERE target_agent = :agent_id
                          AND embedding IS NOT NULL
                          AND 1 - (embedding <=> CAST(:embedding AS vector)) > :threshold
                        ORDER BY embedding <=> CAST(:embedding AS vector)
                        LIMIT :top_k
                    """),
                    {
                        "embedding": embedding_str,
                        "agent_id": agent_id,
                        "threshold": threshold,
                        "top_k": top_k,
                    },
                )
                rows = result.all()
                return [
                    DelegationRecord(
                        id=r[0], delegating_agent=r[1], target_agent=r[2],
                        task_type=r[3], instruction=r[4], status=r[5],
                        result_summary=r[6], created_at=r[7], completed_at=r[8],
                    )
                    for r in rows
                ]
        except Exception as e:
            logger.debug("Deja vu search failed: %s", e)
            return []

    async def generate_counterfactual(
        self,
        action: str,
        failure: str,
        agent_id: str,
    ) -> str | None:
        """Generate an alternative approach for a failed action.

        Uses LLM to reason about what might have worked instead.

        Args:
            action: The action that was taken.
            failure: What went wrong.
            agent_id: Agent identifier.

        Returns:
            Alternative approach text, or None if LLM unavailable.
        """
        system = (
            "You are analyzing a failed agent action. Suggest ONE alternative approach "
            "that might have succeeded. Be specific and actionable. Max 200 characters."
        )
        user = f"Action: {action}\nFailure: {failure}"

        try:
            result = await self._llm.complete(
                system, user,
                max_tokens=128,
                temperature=0.4,
                timeout=10.0,
            )
            return result.strip() if result.strip() else None
        except Exception as e:
            logger.debug("Counterfactual generation failed: %s", e)
            return None

    async def record_delegation(
        self,
        delegating_agent: str,
        target_agent: str,
        task_type: str,
        instruction: str,
        *,
        context_data: dict | None = None,
    ) -> int:
        """Record a task delegation for future deja vu matching.

        Args:
            delegating_agent: Agent delegating the task.
            target_agent: Agent receiving the task.
            task_type: Type of task.
            instruction: Task instruction.
            context_data: Optional context.

        Returns:
            Delegation record ID.
        """
        from nmem.db.models import DelegationModel

        emb = await asyncio.to_thread(self._embedding.embed, instruction[:500])

        async with self._db.session() as session:
            record = DelegationModel(
                delegating_agent=delegating_agent,
                target_agent=target_agent,
                task_type=task_type,
                instruction=instruction,
                context_data=context_data,
                embedding=emb,
            )
            session.add(record)
            await session.flush()
            return record.id

    async def complete_delegation(
        self,
        delegation_id: int,
        status: str,
        result_summary: str | None = None,
        result_data: dict | None = None,
    ) -> None:
        """Update a delegation with its outcome.

        Args:
            delegation_id: Delegation record ID.
            status: "completed", "failed", "refused".
            result_summary: Summary of what happened.
            result_data: Structured result data.
        """
        from datetime import datetime, timezone
        from sqlalchemy import select
        from nmem.db.models import DelegationModel

        async with self._db.session() as session:
            stmt = select(DelegationModel).where(DelegationModel.id == delegation_id)
            result = await session.execute(stmt)
            record = result.scalar_one_or_none()
            if record:
                record.status = status
                record.result_summary = result_summary
                record.result_data = result_data
                record.completed_at = datetime.now(timezone.utc)

    async def emit_curiosity(
        self,
        source_agent: str,
        trigger_type: str,
        summary: str,
        *,
        novelty_score: float = 0.5,
        uncertainty_score: float = 0.5,
        conflict_score: float = 0.0,
        business_impact: float = 0.5,
        entity_type: str | None = None,
        entity_id: str | None = None,
    ) -> CuriositySignalInfo:
        """Emit a curiosity signal for exploration.

        Curiosity signals represent detected gaps, contradictions, or
        unusual patterns that warrant investigation.

        Args:
            source_agent: Agent that detected the signal.
            trigger_type: "contradiction", "missing_information", "unusual_pattern", etc.
            summary: Human-readable description.
            novelty_score: How novel (0-1).
            uncertainty_score: How uncertain (0-1).
            conflict_score: Degree of contradiction (0-1).
            business_impact: Estimated business relevance (0-1).
            entity_type: Related entity type.
            entity_id: Related entity ID.

        Returns:
            CuriositySignalInfo with computed composite score.
        """
        from datetime import datetime

        from sqlalchemy import select
        from nmem.db.models import CuriositySignalModel

        # Stamp the engine's configured project scope so reads can scope to it
        # (the same instance contract as commitments).
        scope = self._config.project_scope if self._config is not None else None
        scope_cond = (
            CuriositySignalModel.project_scope.is_(None)
            if scope is None
            else CuriositySignalModel.project_scope == scope
        )

        async with self._db.session() as session:
            # Dedup: does a live (pending) signal for the same problem already
            # exist? Keyed by (trigger_type, entity) when an entity is given,
            # else by (trigger_type, summary), and scoped to this project so the
            # same problem in two projects stays two signals. Re-emitting the
            # same problem should sharpen one signal — accumulating recurrence —
            # rather than spawn duplicates that each only ever decay.
            if entity_type and entity_id:
                match = (
                    CuriositySignalModel.entity_type == entity_type,
                    CuriositySignalModel.entity_id == entity_id,
                )
            else:
                match = (CuriositySignalModel.summary == summary,)

            existing = (
                await session.execute(
                    select(CuriositySignalModel)
                    .where(
                        CuriositySignalModel.status == "pending",
                        CuriositySignalModel.trigger_type == trigger_type,
                        scope_cond,
                        *match,
                    )
                    .order_by(CuriositySignalModel.composite_score.desc())
                    .limit(1)
                )
            ).scalars().first()

            if existing is not None:
                # Reinforce: bump recurrence, take the stronger of each component
                # (a repeat is at least as salient), recompute composite.
                existing.recurrence_score = min(
                    1.0, existing.recurrence_score + _CURIOSITY_RECURRENCE_STEP
                )
                existing.novelty_score = max(existing.novelty_score, novelty_score)
                existing.uncertainty_score = max(existing.uncertainty_score, uncertainty_score)
                existing.conflict_score = max(existing.conflict_score, conflict_score)
                existing.business_impact = max(existing.business_impact, business_impact)
                existing.composite_score = _curiosity_composite(
                    existing.novelty_score,
                    existing.uncertainty_score,
                    existing.conflict_score,
                    existing.business_impact,
                    existing.recurrence_score,
                )
                # Re-observation refreshes staleness so an actively-recurring
                # problem is not decayed away by _decay_curiosity_signals, which
                # keys off created_at (naive UTC, matching that method).
                existing.created_at = datetime.utcnow()
                await session.flush()

                return CuriositySignalInfo(
                    id=existing.id,
                    source_agent=existing.source_agent,
                    trigger_type=existing.trigger_type,
                    summary=existing.summary,
                    composite_score=existing.composite_score,
                    novelty_score=existing.novelty_score,
                    uncertainty_score=existing.uncertainty_score,
                    conflict_score=existing.conflict_score,
                    recurrence_score=existing.recurrence_score,
                    business_impact=existing.business_impact,
                    status=existing.status,
                    entity_type=existing.entity_type,
                    entity_id=existing.entity_id,
                    created_at=existing.created_at,
                )

            composite = _curiosity_composite(
                novelty_score, uncertainty_score, conflict_score, business_impact,
            )
            record = CuriositySignalModel(
                source_agent=source_agent,
                trigger_type=trigger_type,
                summary=summary,
                novelty_score=novelty_score,
                uncertainty_score=uncertainty_score,
                conflict_score=conflict_score,
                business_impact=business_impact,
                composite_score=composite,
                entity_type=entity_type,
                entity_id=entity_id,
                project_scope=scope,
            )
            session.add(record)
            await session.flush()

            return CuriositySignalInfo(
                id=record.id,
                source_agent=source_agent,
                trigger_type=trigger_type,
                summary=summary,
                composite_score=composite,
                novelty_score=novelty_score,
                uncertainty_score=uncertainty_score,
                conflict_score=conflict_score,
                recurrence_score=0.0,
                business_impact=business_impact,
                status="pending",
                entity_type=entity_type,
                entity_id=entity_id,
                created_at=record.created_at,
            )

    def _curiosity_scope_conditions(self, project_scope):
        """Build the pending-status + project-scope filter shared by the list and
        count queries so they always describe the same backlog. See
        ``_resolve_curiosity_scope`` for the ``project_scope`` contract."""
        from nmem.db.models import CuriositySignalModel

        resolved = self._resolve_curiosity_scope(project_scope)
        conds = [CuriositySignalModel.status == "pending"]
        if resolved is not _NO_SCOPE:
            conds.append(
                CuriositySignalModel.project_scope.is_(None)
                if resolved is None
                else CuriositySignalModel.project_scope == resolved
            )
        return conds

    async def count_pending_curiosity(
        self, *, min_composite: float = 0.0, project_scope=...,
    ) -> int:
        """Total pending curiosity signals at or above ``min_composite``.

        Independent of any ranking fetch limit, so callers (e.g. the wake
        snapshot's omission notice) can report the true backlog size rather than
        just what a bounded fetch returned. See ``_curiosity_scope_conditions``
        for the ``project_scope`` contract.
        """
        from sqlalchemy import func, select
        from nmem.db.models import CuriositySignalModel

        conds = self._curiosity_scope_conditions(project_scope)
        conds.append(CuriositySignalModel.composite_score >= min_composite)
        async with self._db.session() as session:
            total = (
                await session.execute(
                    select(func.count(CuriositySignalModel.id)).where(*conds)
                )
            ).scalar()
            return int(total or 0)

    async def list_pending_curiosity(
        self,
        *,
        min_composite: float = 0.0,
        limit: int = 20,
        project_scope=...,
    ) -> list[CuriositySignalInfo]:
        """List pending curiosity signals, strongest first.

        Read surface for consumers that act on the exploration queue (e.g.
        nmem-sym mirrors these into per-problem "concerns"). Only 'pending'
        signals are returned; those above `min_composite` are the ones worth
        spending attention on.
        """
        from sqlalchemy import select
        from nmem.db.models import CuriositySignalModel

        conds = self._curiosity_scope_conditions(project_scope)
        conds.append(CuriositySignalModel.composite_score >= min_composite)
        async with self._db.session() as session:
            rows = (
                await session.execute(
                    select(CuriositySignalModel)
                    .where(*conds)
                    .order_by(CuriositySignalModel.composite_score.desc())
                    .limit(limit)
                )
            ).scalars().all()

            return [
                CuriositySignalInfo(
                    id=r.id,
                    source_agent=r.source_agent,
                    trigger_type=r.trigger_type,
                    summary=r.summary,
                    composite_score=r.composite_score,
                    novelty_score=r.novelty_score,
                    uncertainty_score=r.uncertainty_score,
                    conflict_score=r.conflict_score,
                    recurrence_score=r.recurrence_score,
                    business_impact=r.business_impact,
                    status=r.status,
                    entity_type=r.entity_type,
                    entity_id=r.entity_id,
                    created_at=r.created_at,
                )
                for r in rows
            ]

    async def resolve_curiosity(
        self,
        signal_id: int,
        *,
        outcome: str = "addressed",
        resolved_by: str = "nmem",
    ) -> bool:
        """Mark a curiosity signal resolved. Returns True if a pending row was updated.

        Called when a consumer has acted on the signal (e.g. nmem-sym's drive
        system spent a targeted action on the concern mirroring it), closing
        the exploration loop so the signal is no longer re-served.
        """
        from datetime import datetime

        from sqlalchemy import select
        from nmem.db.models import CuriositySignalModel

        async with self._db.session() as session:
            record = (
                await session.execute(
                    select(CuriositySignalModel).where(
                        CuriositySignalModel.id == signal_id,
                    )
                )
            ).scalar_one_or_none()
            if record is None or record.status != "pending":
                return False
            record.status = "resolved"
            record.resolution_outcome = outcome
            record.resolved_by = resolved_by
            record.resolved_at = datetime.utcnow()
            return True
