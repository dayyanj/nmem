"""
Tier 1: Working Memory — ephemeral per-session context.

Slots hold current task, decisions, scratchpad, and context for the active session.
Cleared on session end. Optionally summarized to journal on close.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from sqlalchemy import select, delete, and_

from nmem.db.models import WorkingMemory
from nmem.types import WorkingSlot

if TYPE_CHECKING:
    from nmem.db.session import DatabaseManager
    from nmem.config import NmemConfig

logger = logging.getLogger(__name__)


class WorkingMemoryTier:
    """Tier 1: Ephemeral per-session working memory."""

    def __init__(self, db: DatabaseManager, config: NmemConfig):
        self._db = db
        self._config = config

    async def set(
        self,
        session_id: str,
        agent_id: str,
        slot: str,
        content: str,
        priority: int = 5,
        context_thread_id: str | None = None,
    ) -> WorkingSlot:
        """Set or update a working memory slot.

        Args:
            session_id: Current session identifier.
            agent_id: Agent identifier.
            slot: Slot name (e.g., "current_task", "decision", "scratchpad").
            content: Slot content.
            priority: Priority 1-10 (1=highest). Default 5.
            context_thread_id: Optional context thread for clustering.

        Returns:
            The created/updated WorkingSlot.
        """
        async with self._db.session() as session:
            # Upsert: check if slot exists
            stmt = select(WorkingMemory).where(
                and_(
                    WorkingMemory.session_id == str(session_id),
                    WorkingMemory.agent_id == agent_id,
                    WorkingMemory.slot == slot,
                )
            )
            result = await session.execute(stmt)
            existing = result.scalar_one_or_none()

            if existing:
                existing.content = content
                existing.priority = priority
                if context_thread_id:
                    existing.context_thread_id = context_thread_id
            else:
                record = WorkingMemory(
                    session_id=str(session_id),
                    agent_id=agent_id,
                    slot=slot,
                    content=content,
                    priority=priority,
                    context_thread_id=context_thread_id,
                )
                session.add(record)

        return WorkingSlot(
            session_id=str(session_id),
            agent_id=agent_id,
            slot=slot,
            content=content,
            priority=priority,
            context_thread_id=context_thread_id,
        )

    async def get(self, session_id: str, agent_id: str) -> list[WorkingSlot]:
        """Get all working memory slots for a session/agent, ordered by priority.

        Returns:
            List of WorkingSlot objects, priority-ordered (1=highest first).
        """
        async with self._db.session() as session:
            stmt = (
                select(WorkingMemory)
                .where(
                    and_(
                        WorkingMemory.session_id == str(session_id),
                        WorkingMemory.agent_id == agent_id,
                    )
                )
                .order_by(WorkingMemory.priority)
            )
            result = await session.execute(stmt)
            rows = result.scalars().all()
            return [
                WorkingSlot(
                    session_id=row.session_id,
                    agent_id=row.agent_id,
                    slot=row.slot,
                    content=row.content,
                    priority=row.priority,
                    context_thread_id=row.context_thread_id,
                    created_at=row.created_at,
                    updated_at=row.updated_at,
                )
                for row in rows
            ]

    async def clear(
        self, session_id: str, agent_id: str, slot: str | None = None
    ) -> int:
        """Clear working memory slots.

        Args:
            session_id: Session identifier.
            agent_id: Agent identifier.
            slot: Specific slot to clear, or None to clear all.

        Returns:
            Number of slots cleared.
        """
        async with self._db.session() as session:
            conditions = [
                WorkingMemory.session_id == str(session_id),
                WorkingMemory.agent_id == agent_id,
            ]
            if slot:
                conditions.append(WorkingMemory.slot == slot)

            stmt = delete(WorkingMemory).where(and_(*conditions))
            result = await session.execute(stmt)
            return result.rowcount  # type: ignore[return-value]

    async def build_prompt(
        self, session_id: str, agent_id: str, max_chars: int | None = None
    ) -> str:
        """Build a prompt section from working memory.

        Returns:
            Formatted working memory text, priority-ordered.
        """
        max_chars = max_chars or self._config.working.max_chars_in_prompt
        slots = await self.get(session_id, agent_id)
        if not slots:
            return ""

        lines: list[str] = []
        chars = 0
        for s in slots:
            line = f"- [{s.slot}] {s.content}"
            if chars + len(line) > max_chars:
                break
            lines.append(line)
            chars += len(line)
        return "\n".join(lines)

    async def flush_to_journal(
        self,
        session_id: str,
        agent_id: str,
        journal_tier,
        *,
        clear_after: bool = True,
    ) -> int:
        """Flush working memory slots to a journal session summary.

        Creates a single journal entry from all working memory slots,
        then optionally clears the working memory.

        Returns:
            Number of slots flushed.
        """
        slots = await self.get(session_id, agent_id)
        if not slots:
            return 0

        lines = [f"[{s.slot}] {s.content}" for s in slots]
        content = "\n".join(lines)
        title = f"Session {session_id[:8]} working memory ({len(slots)} slots)"

        await journal_tier.add(
            agent_id=agent_id,
            entry_type="session_summary",
            title=title,
            content=content,
            importance=None,
            session_id=session_id,
            tags=["working_memory_flush", f"session:{session_id}"],
        )

        if clear_after:
            await self.clear(session_id, agent_id)

        return len(slots)
