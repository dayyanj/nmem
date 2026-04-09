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


class CognitiveEngine:
    """Cognitive capabilities for agent memory."""

    def __init__(
        self,
        db: DatabaseManager,
        embedding: EmbeddingProvider,
        llm: LLMProvider,
    ):
        self._db = db
        self._embedding = embedding
        self._llm = llm

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
        from nmem.db.models import CuriositySignalModel

        composite = (
            novelty_score * 0.3
            + uncertainty_score * 0.2
            + conflict_score * 0.2
            + business_impact * 0.3
        )

        async with self._db.session() as session:
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
                business_impact=business_impact,
                entity_type=entity_type,
                entity_id=entity_id,
                created_at=record.created_at,
            )
