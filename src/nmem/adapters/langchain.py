"""
LangChain adapter for nmem.

Provides a LangChain-compatible memory class that can be plugged into
any LangChain chain or agent.

Usage:
    from nmem.adapters.langchain import NmemLangChainMemory
    memory = NmemLangChainMemory(mem_system=mem, agent_id="my_agent")
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from nmem.memory import MemorySystem

# Stub for future implementation
# TODO: Phase 5 — implement full LangChain BaseMemory + BaseChatMessageHistory


class NmemLangChainMemory:
    """LangChain-compatible memory backed by nmem.

    This adapter exposes nmem's hierarchical memory through LangChain's
    memory interface, enabling use with any LangChain chain or agent.

    Args:
        mem_system: Initialized MemorySystem instance.
        agent_id: Agent identifier for memory scoping.
        session_id: Session identifier for working memory.
    """

    def __init__(
        self,
        mem_system: MemorySystem,
        agent_id: str,
        session_id: str | None = None,
    ):
        self._mem = mem_system
        self._agent_id = agent_id
        self._session_id = session_id

    @property
    def memory_variables(self) -> list[str]:
        """Return memory variables provided to the chain."""
        return ["memory_context"]

    async def aload_memory_variables(self, inputs: dict[str, Any]) -> dict[str, str]:
        """Load memory context for chain input.

        Uses the latest user input as query for relevance-ranked retrieval.
        """
        query = inputs.get("input", inputs.get("question", ""))
        ctx = await self._mem.prompt.build(
            agent_id=self._agent_id,
            session_id=self._session_id,
            query=query if query else None,
        )
        return {"memory_context": ctx.full_injection}

    async def asave_context(
        self, inputs: dict[str, Any], outputs: dict[str, str]
    ) -> None:
        """Save interaction to journal."""
        user_input = inputs.get("input", inputs.get("question", ""))
        response = outputs.get("output", outputs.get("answer", ""))
        if user_input and response:
            await self._mem.journal.add(
                agent_id=self._agent_id,
                entry_type="interaction",
                title=user_input[:200],
                content=f"Q: {user_input[:300]}\nA: {response[:300]}",
                importance=3,
                session_id=self._session_id,
            )
