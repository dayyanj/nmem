"""
LangChain adapter for nmem.

Provides a LangChain-compatible memory class that can be plugged into
any LangChain chain or agent. Inherits from ``BaseMemory`` when
langchain-core is installed, falls back to a standalone class otherwise.

Usage::

    from nmem import MemorySystem, NmemConfig
    from nmem.adapters.langchain import NmemLangChainMemory

    mem = MemorySystem(NmemConfig.from_profile("neutral", database_url="..."))
    await mem.initialize()

    memory = NmemLangChainMemory(mem_system=mem, agent_id="my_agent")

    # In a LangChain chain:
    chain = LLMChain(llm=llm, prompt=prompt, memory=memory)

    # Or load context manually:
    ctx = await memory.aload_memory_variables({"input": "What's the deploy schedule?"})
    print(ctx["memory_context"])  # nmem's hierarchical memory injection

Install: ``pip install nmem[langchain]``
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from nmem.memory import MemorySystem

# Try to inherit from LangChain's BaseMemory for full compatibility.
# If langchain-core isn't installed, fall back to a plain class.
try:
    from langchain_core.memory import BaseMemory as _BaseMemory
except ImportError:
    _BaseMemory = object  # type: ignore[misc,assignment]


class NmemLangChainMemory(_BaseMemory):  # type: ignore[misc]
    """LangChain-compatible memory backed by nmem.

    Exposes nmem's hierarchical memory through LangChain's memory interface,
    enabling use with any LangChain chain or agent. Supports both sync and
    async paths.

    Args:
        mem_system: Initialized MemorySystem instance.
        agent_id: Agent identifier for memory scoping.
        session_id: Session identifier for working memory.
        memory_key: Key name in the chain's input dict (default: "memory_context").
        input_key: Key for user input extraction (tries "input", then "question").
    """

    # Pydantic v2 config for LangChain BaseMemory compatibility
    class Config:
        arbitrary_types_allowed = True

    def __init__(
        self,
        mem_system: "MemorySystem",
        agent_id: str,
        session_id: str | None = None,
        *,
        memory_key: str = "memory_context",
        input_key: str = "input",
    ):
        # If BaseMemory is a real LangChain class, call super().__init__()
        if _BaseMemory is not object:
            super().__init__()
        self._mem = mem_system
        self._agent_id = agent_id
        self._session_id = session_id
        self._memory_key = memory_key
        self._input_key = input_key

    @property
    def memory_variables(self) -> list[str]:
        """Return memory variables provided to the chain."""
        return [self._memory_key]

    # ── Async path (preferred) ──────────────────────────────────────────

    async def aload_memory_variables(
        self, inputs: dict[str, Any],
    ) -> dict[str, str]:
        """Load memory context for chain input (async).

        Uses the latest user input as query for relevance-ranked retrieval.
        """
        query = inputs.get(self._input_key, inputs.get("question", ""))
        ctx = await self._mem.prompt.build(
            agent_id=self._agent_id,
            session_id=self._session_id,
            query=query if query else None,
        )
        return {self._memory_key: ctx.full_injection}

    async def asave_context(
        self, inputs: dict[str, Any], outputs: dict[str, str],
    ) -> None:
        """Save interaction to journal (async)."""
        user_input = inputs.get(self._input_key, inputs.get("question", ""))
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

    async def aclear(self) -> None:
        """Clear working memory for the current session."""
        if self._session_id:
            await self._mem.working.clear(self._session_id, self._agent_id)

    # ── Sync wrappers ───────────────────────────────────────────────────

    def load_memory_variables(self, inputs: dict[str, Any]) -> dict[str, str]:
        """Load memory context for chain input (sync wrapper)."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop and loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                return pool.submit(asyncio.run, self.aload_memory_variables(inputs)).result()
        return asyncio.run(self.aload_memory_variables(inputs))

    def save_context(
        self, inputs: dict[str, Any], outputs: dict[str, str],
    ) -> None:
        """Save interaction to journal (sync wrapper)."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop and loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                pool.submit(asyncio.run, self.asave_context(inputs, outputs)).result()
        else:
            asyncio.run(self.asave_context(inputs, outputs))

    def clear(self) -> None:
        """Clear working memory (sync wrapper)."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop and loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                pool.submit(asyncio.run, self.aclear()).result()
        else:
            asyncio.run(self.aclear())
