"""
CrewAI adapter for nmem.

Provides a CrewAI-compatible memory interface that can be plugged into
any CrewAI agent or crew.

Usage::

    from nmem import MemorySystem, NmemConfig
    from nmem.adapters.crewai import NmemCrewAIMemory

    mem = MemorySystem(NmemConfig.from_profile("refinery", database_url="..."))
    await mem.initialize()

    memory = NmemCrewAIMemory(mem_system=mem, agent_id="researcher")

    # Save a memory from task execution:
    await memory.save("Competitor X launched a new pricing tier at $29/mo")

    # Search memories:
    results = await memory.search("competitor pricing", limit=5)

    # Build context for prompt injection:
    context = await memory.build_context("preparing market analysis")

Install: ``pip install nmem[crewai]``
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from nmem.memory import MemorySystem


class NmemCrewAIMemory:
    """CrewAI-compatible memory backed by nmem.

    Wraps nmem's hierarchical memory system for use with CrewAI agents.
    Supports save, search, context building, and reset operations.

    Args:
        mem_system: Initialized MemorySystem instance.
        agent_id: Agent identifier for memory scoping.
        session_id: Optional session ID for working memory.
    """

    def __init__(
        self,
        mem_system: "MemorySystem",
        agent_id: str,
        session_id: str | None = None,
    ):
        self._mem = mem_system
        self._agent_id = agent_id
        self._session_id = session_id

    async def save(
        self,
        value: str,
        metadata: dict[str, Any] | None = None,
        *,
        importance: int | None = None,
        entry_type: str = "task_result",
    ) -> None:
        """Save a memory from CrewAI task execution.

        Args:
            value: The content to remember.
            metadata: Optional metadata dict (keys become tags).
            importance: Explicit importance (None = auto-scored).
            entry_type: Journal entry type.
        """
        await self._mem.journal.add(
            agent_id=self._agent_id,
            entry_type=entry_type,
            title=value[:200],
            content=value,
            importance=importance,
            tags=list(metadata.keys()) if metadata else None,
            session_id=self._session_id,
        )

    async def search(
        self, query: str, limit: int = 5,
    ) -> list[dict[str, Any]]:
        """Search memories relevant to a query.

        Returns a list of dicts with content, score, tier, and metadata.
        """
        results = await self._mem.search(
            agent_id=self._agent_id, query=query, top_k=limit,
        )
        return [
            {
                "content": r.content,
                "score": r.score,
                "tier": r.tier,
                "metadata": r.metadata,
            }
            for r in results
        ]

    async def build_context(self, query: str | None = None) -> str:
        """Build a full memory context string for prompt injection.

        Returns the same hierarchical injection that nmem's prompt
        builder produces — policies, shared knowledge, LTM, journal,
        working memory — ranked by relevance to the query.
        """
        ctx = await self._mem.prompt.build(
            agent_id=self._agent_id,
            session_id=self._session_id,
            query=query,
        )
        return ctx.full_injection

    async def reset(self) -> None:
        """Clear working memory for the current session."""
        if self._session_id:
            await self._mem.working.clear(self._session_id, self._agent_id)
