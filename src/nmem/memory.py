"""
MemorySystem — the main entry point for nmem.

Wires together all tiers, providers, and background services.

Usage:
    from nmem import MemorySystem, NmemConfig

    mem = MemorySystem(NmemConfig(
        database_url="postgresql+asyncpg://localhost/mydb",
        embedding={"provider": "sentence-transformers"},
        llm={"provider": "openai", "base_url": "http://localhost:11434/v1", "model": "qwen3"},
    ))
    await mem.initialize()

    await mem.journal.add(agent_id="support", entry_type="note", title="...", content="...")
    results = await mem.search(agent_id="support", query="billing issues")
    ctx = await mem.prompt.build(agent_id="support", query="billing issues")
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable, Awaitable

from nmem.config import NmemConfig
from nmem.db.session import DatabaseManager
from nmem.providers.embedding.base import EmbeddingProvider
from nmem.providers.llm.base import LLMProvider
from nmem.tiers.working import WorkingMemoryTier
from nmem.tiers.journal import JournalTier
from nmem.tiers.ltm import LTMTier
from nmem.tiers.shared import SharedTier
from nmem.tiers.entity import EntityTier
from nmem.tiers.policy import PolicyTier
from nmem.prompt import PromptBuilder
from nmem.cognitive import CognitiveEngine
from nmem.consolidation import Consolidator
from nmem.types import SearchResult

logger = logging.getLogger(__name__)


class MemorySystem:
    """Main entry point for nmem — cognitive memory for AI agents.

    Provides access to all 6 memory tiers, cross-tier search,
    prompt building, cognitive features, and background consolidation.

    Args:
        config: NmemConfig instance. If None, loads from environment.
    """

    def __init__(self, config: NmemConfig | None = None, **kwargs: Any):
        self._config = config or NmemConfig(**kwargs)
        self._db = DatabaseManager(self._config.database_url)

        # Initialize providers
        self._embedding = self._create_embedding_provider()
        self._llm = self._create_llm_provider()

        # Initialize tiers
        self._working = WorkingMemoryTier(self._db, self._config)
        self._journal = JournalTier(self._db, self._config, self._embedding, self._llm)
        self._ltm = LTMTier(self._db, self._config, self._embedding, self._llm)
        self._shared = SharedTier(self._db, self._config, self._embedding)
        self._entity = EntityTier(self._db, self._config, self._embedding)
        self._policy = PolicyTier(self._db, self._config)

        # Initialize prompt builder
        self._prompt = PromptBuilder(
            self._working, self._journal, self._ltm,
            self._shared, self._entity, self._policy,
        )

        # Initialize cognitive engine
        self._cognitive = CognitiveEngine(self._db, self._embedding, self._llm)

        # Initialize knowledge link engine
        from nmem.links import KnowledgeLinkEngine
        self._link_engine = KnowledgeLinkEngine(self._db, self._config)

        # Initialize consolidator
        self._consolidator = Consolidator(
            self._db, self._config, self._embedding, self._llm
        )
        self._consolidator._link_engine = self._link_engine

        # Wire journal → consolidator signal
        self._journal._on_high_importance = self._consolidator.signal

        # Wire entity → auto-journal callback
        self._entity._auto_journal_callback = self._auto_journal_entity_access

        # Event handlers
        self._event_handlers: dict[str, list[Callable]] = {}

    # ── Properties ───────────────────────────────────────────────────────

    @property
    def working(self) -> WorkingMemoryTier:
        """Tier 1: Ephemeral per-session working memory."""
        return self._working

    @property
    def journal(self) -> JournalTier:
        """Tier 2: Short-term activity journal with auto-promotion."""
        return self._journal

    @property
    def ltm(self) -> LTMTier:
        """Tier 3: Permanent per-agent long-term memory."""
        return self._ltm

    @property
    def shared(self) -> SharedTier:
        """Tier 4: Cross-agent shared knowledge."""
        return self._shared

    @property
    def entity(self) -> EntityTier:
        """Tier 5: Entity-scoped collaborative memory."""
        return self._entity

    @property
    def policy(self) -> PolicyTier:
        """Tier 6: Governance policy memory."""
        return self._policy

    @property
    def links(self):
        """Knowledge link engine for associative linking."""
        return self._link_engine

    @property
    def prompt(self) -> PromptBuilder:
        """Prompt builder for memory context injection."""
        return self._prompt

    @property
    def cognitive(self) -> CognitiveEngine:
        """Cognitive capabilities (deja vu, counterfactual, curiosity)."""
        return self._cognitive

    @property
    def consolidation(self) -> Consolidator:
        """Background consolidation engine."""
        return self._consolidator

    # ── Entity Auto-Journal ─────────────────────────────────────────────

    async def _auto_journal_entity_access(
        self, agent_id: str, entity_type: str, entity_id: str,
        entity_name: str, result_count: int, top_content: str,
        project_scope: str | None = None,
    ) -> None:
        """Auto-create a journal entry when entity is accessed via search."""
        try:
            await self._journal.add(
                agent_id=agent_id,
                entry_type="entity_reference",
                title=f"Referenced {entity_name} ({entity_type}/{entity_id})",
                content=f"Accessed entity records via search. {result_count} results found. Key info: {top_content}",
                importance=self._config.entity.auto_journal_importance,
                tags=["entity_access", f"entity:{entity_type}/{entity_id}"],
                compress=False,
                project_scope=project_scope,
            )
        except Exception as e:
            logger.debug("Auto-journal for entity access failed (non-fatal): %s", e)

    # ── Lifecycle ────────────────────────────────────────────────────────

    async def initialize(self) -> None:
        """Initialize the memory system — create tables, indexes, validate config.

        Must be called before using any tier operations.
        """
        logger.info(
            "Initializing nmem (db=%s, embedding=%s, llm=%s)",
            self._config.database_url.split("@")[-1] if "@" in self._config.database_url else "local",
            self._config.embedding.provider,
            self._config.llm.provider,
        )
        await self._db.initialize(embedding_dimensions=self._embedding.dimensions)
        logger.info("nmem initialized successfully")

    async def close(self) -> None:
        """Shutdown — stop consolidator and close database connections."""
        self._consolidator.stop()
        await self._db.close()
        logger.info("nmem closed")

    def start_consolidation(self) -> asyncio.Task:
        """Start the background consolidation loop.

        Returns:
            The asyncio.Task running the consolidator.
        """
        return self._consolidator.start()

    def stop_consolidation(self) -> None:
        """Stop the background consolidation loop."""
        self._consolidator.stop()

    # ── Cross-tier Search ────────────────────────────────────────────────

    async def search(
        self,
        agent_id: str,
        query: str,
        *,
        tiers: tuple[str, ...] | None = None,
        top_k: int = 10,
        project_scope: str | None = ...,
    ) -> list[SearchResult]:
        """Search across multiple memory tiers in parallel.

        Args:
            agent_id: Agent identifier.
            query: Search query text.
            tiers: Which tiers to search (default: all).
            top_k: Maximum total results.
            project_scope: Scope filter. None = global only, "*" = all scopes,
                str = specific scope + global, ... = use config default.

        Returns:
            List of SearchResult objects, ranked by score.
        """
        from nmem.search import cross_tier_search

        return await cross_tier_search(
            agent_id=agent_id,
            query=query,
            journal=self._journal,
            ltm=self._ltm,
            shared=self._shared,
            entity=self._entity,
            tiers=tiers,
            top_k=top_k,
            project_scope=project_scope,
        )

    # ── Event System ─────────────────────────────────────────────────────

    def on(self, event: str) -> Callable:
        """Register an event handler.

        Supported events:
            - "journal.added" — journal entry created
            - "ltm.saved" — LTM entry created/updated
            - "shared.saved" — shared knowledge updated
            - "conflict.detected" — memory conflict found
            - "consolidation.promoted" — entries promoted during consolidation

        Usage:
            @mem.on("journal.added")
            async def on_journal(entry: dict):
                print(f"New: {entry['title']}")
        """
        def decorator(fn: Callable) -> Callable:
            if event not in self._event_handlers:
                self._event_handlers[event] = []
            self._event_handlers[event].append(fn)
            return fn
        return decorator

    async def _emit(self, event: str, data: Any = None) -> None:
        """Emit an event to registered handlers."""
        for handler in self._event_handlers.get(event, []):
            try:
                if asyncio.iscoroutinefunction(handler):
                    await handler(data)
                else:
                    handler(data)
            except Exception as e:
                logger.warning("Event handler for '%s' failed: %s", event, e)

    # ── Provider Factory ─────────────────────────────────────────────────

    def _create_embedding_provider(self) -> EmbeddingProvider:
        """Create the configured embedding provider."""
        provider = self._config.embedding.provider

        if provider == "noop":
            from nmem.providers.embedding.noop import NoOpEmbeddingProvider
            return NoOpEmbeddingProvider(self._config.embedding.dimensions)

        if provider == "sentence-transformers":
            from nmem.providers.embedding.sentence_transformers_provider import (
                SentenceTransformersProvider,
            )
            return SentenceTransformersProvider(
                self._config.embedding.model,
                device=self._config.embedding.device,
            )

        if provider == "openai":
            from nmem.providers.embedding.openai_provider import OpenAIEmbeddingProvider
            return OpenAIEmbeddingProvider(
                model=self._config.embedding.model,
                dimensions=self._config.embedding.dimensions,
                api_key=self._config.embedding.api_key,
                base_url=self._config.embedding.base_url,
            )

        raise ValueError(f"Unknown embedding provider: {provider}")

    def _create_llm_provider(self) -> LLMProvider:
        """Create the configured LLM provider."""
        provider = self._config.llm.provider

        if provider == "noop":
            from nmem.providers.llm.noop import NoOpLLMProvider
            return NoOpLLMProvider()

        if provider == "openai":
            from nmem.providers.llm.openai_compat import OpenAICompatibleLLMProvider
            return OpenAICompatibleLLMProvider(
                model=self._config.llm.model,
                api_key=self._config.llm.api_key,
                base_url=self._config.llm.base_url,
            )

        if provider == "anthropic":
            from nmem.providers.llm.anthropic_provider import AnthropicLLMProvider
            return AnthropicLLMProvider(
                model=self._config.llm.model,
                api_key=self._config.llm.api_key,
            )

        raise ValueError(f"Unknown LLM provider: {provider}")
