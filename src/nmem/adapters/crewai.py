"""
CrewAI adapter for nmem.

Provides a CrewAI-compatible memory interface.

Usage:
    from nmem.adapters.crewai import NmemCrewAIMemory
    memory = NmemCrewAIMemory(mem_system=mem, agent_id="researcher")
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from nmem.memory import MemorySystem

# Stub for future implementation
# TODO: Phase 5 — implement full CrewAI memory interface


class NmemCrewAIMemory:
    """CrewAI-compatible memory backed by nmem.

    Args:
        mem_system: Initialized MemorySystem instance.
        agent_id: Agent identifier.
    """

    def __init__(self, mem_system: MemorySystem, agent_id: str):
        self._mem = mem_system
        self._agent_id = agent_id

    async def save(self, value: str, metadata: dict[str, Any] | None = None) -> None:
        """Save a memory from CrewAI task execution."""
        await self._mem.journal.add(
            agent_id=self._agent_id,
            entry_type="task_result",
            title=value[:200],
            content=value,
            importance=5,
            tags=list(metadata.keys()) if metadata else None,
        )

    async def search(self, query: str, limit: int = 5) -> list[dict[str, Any]]:
        """Search memories relevant to a query."""
        results = await self._mem.search(
            agent_id=self._agent_id, query=query, top_k=limit
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
