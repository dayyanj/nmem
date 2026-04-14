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
from nmem.types import BriefingResult, SearchResult

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
            db=self._db,
            config=self._config,
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

        # Event handlers (must be created before wiring _on_event callbacks)
        self._event_handlers: dict[str, list[Callable]] = {}

        # Wire event emission callbacks
        self._journal._on_event = self._emit
        self._ltm._on_event = self._emit
        self._shared._on_event = self._emit

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
        """Shutdown — stop consolidator and close database connections.

        Explicitly releases the embedding model so a subsequent MemorySystem
        instance in the same process can reload it without hitting stale
        meta-tensor errors from torch's garbage collector.
        """
        self._consolidator.stop()

        # Release embedding model for GC before closing DB
        if hasattr(self._embedding, "_model") and self._embedding._model is not None:
            self._embedding._model = None
            self._embedding._dimensions = None

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

    async def end_session(
        self,
        session_id: str,
        agent_id: str,
        *,
        flush_to_journal: bool = True,
    ) -> int:
        """End a session: flush working memory to journal and clear it.

        Returns:
            Number of working memory slots flushed.
        """
        flushed = 0
        if flush_to_journal:
            flushed = await self._working.flush_to_journal(
                session_id, agent_id, self._journal
            )
        else:
            await self._working.clear(session_id, agent_id)
        return flushed

    # ── Cross-tier Search ────────────────────────────────────────────────

    async def search(
        self,
        agent_id: str,
        query: str,
        *,
        tiers: tuple[str, ...] | None = None,
        top_k: int = 10,
        project_scope: str | None = ...,
        bump_access: bool = True,
    ) -> list[SearchResult]:
        """Search across multiple memory tiers in parallel.

        Args:
            agent_id: Agent identifier.
            query: Search query text.
            tiers: Which tiers to search (default: all).
            top_k: Maximum total results.
            project_scope: Scope filter. None = global only, "*" = all scopes,
                str = specific scope + global, ... = use config default.
            bump_access: If True (default), increment access_count on returned
                entries. Set False for read-only queries like briefings.

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
            policy=self._policy,
            link_engine=self._link_engine,
            config=self._config,
            tiers=tiers,
            top_k=top_k,
            project_scope=project_scope,
            bump_access=bump_access,
            embedder=self._embedding,
        )

    # ── Priorities (importance-ranked, for planning not retrieval) ──────

    async def priorities(
        self,
        agent_id: str | None = None,
        *,
        min_importance: int = 7,
        since_days: int | None = 30,
        tiers: tuple[str, ...] = ("journal", "ltm"),
        limit: int = 10,
        project_scope: str | None = ...,
    ) -> list[SearchResult]:
        """Return high-importance items for planning and attention.

        Unlike search() which ranks by *relevance* to a query, priorities()
        ranks by *importance* — what needs attention, what's consequential,
        what shouldn't be forgotten.

        This reflects a fundamental cognitive distinction:
          - search() = hippocampal retrieval (association-driven)
          - priorities() = prefrontal attention (importance-driven)

        Use this for:
          - Start-of-session briefing ("what's urgent?")
          - Planning ("what are the high-priority items?")
          - Audit ("what critical decisions were made recently?")

        Do NOT use this for knowledge retrieval — use search() instead.

        Args:
            agent_id: Filter to a specific agent, or None for all.
            min_importance: Minimum importance threshold (default 7).
            since_days: Only include entries from the last N days (None = all time).
            tiers: Which tiers to query (default: journal + ltm).
            limit: Maximum results.
            project_scope: Scope filter.

        Returns:
            List of SearchResult objects, ranked by importance DESC.
        """
        from datetime import datetime, timedelta, timezone

        from sqlalchemy import text as sa_text

        if project_scope is ...:
            project_scope = self._config.project_scope

        results: list[SearchResult] = []

        for tier_name in tiers:
            table = {
                "journal": "nmem_journal_entries",
                "ltm": "nmem_long_term_memory",
                "shared": "nmem_shared_knowledge",
            }.get(tier_name)
            if not table:
                continue

            where_parts = ["importance >= :min_imp"]
            params: dict = {"min_imp": min_importance, "limit": limit}

            if agent_id and tier_name != "shared":
                where_parts.append("agent_id = :agent_id")
                params["agent_id"] = agent_id

            if since_days is not None:
                cutoff = datetime.now(timezone.utc) - timedelta(days=since_days)
                where_parts.append("created_at >= :cutoff")
                params["cutoff"] = cutoff

            if project_scope is not None and project_scope != "*":
                where_parts.append(
                    "(project_scope = :scope OR project_scope IS NULL)"
                )
                params["scope"] = project_scope

            sql = sa_text(
                f"SELECT id, importance, "
                f"SUBSTRING(content FROM 1 FOR 300) as preview, "
                f"created_at "
                f"FROM {table} "
                f"WHERE {' AND '.join(where_parts)} "
                f"ORDER BY importance DESC, created_at DESC "
                f"LIMIT :limit"
            )

            async with self._db.session() as session:
                rows = await session.execute(sql, params)
                for row in rows.all():
                    results.append(SearchResult(
                        tier=tier_name,
                        id=row[0],
                        score=row[1] / 10.0,
                        content=row[2],
                        metadata={
                            "importance": row[1],
                            "created_at": str(row[3]),
                        },
                    ))

        results.sort(key=lambda r: r.score, reverse=True)
        return results[:limit]

    # ── Session Briefing ─────────────────────────────────────────────────

    _WARNING_KEYWORDS = frozenset({
        "never", "do not", "avoid", "warning", "critical",
        "must not", "forbidden",
    })

    async def briefing(
        self,
        agent_id: str = "default",
        *,
        session_id: str | None = None,
        max_tokens: int = 1500,
        query: str | None = None,
        include_priorities: bool = True,
        include_relevant: bool = True,
        include_recent: bool = True,
        project_scope: str | None = ...,
    ) -> BriefingResult:
        """Build a session-start briefing with recognition signals.

        Call at the start of a session for warm-up context. When a query is
        provided, includes topic-relevant facts tagged with recognition
        levels (KNOWN/FAMILIAR/UNCERTAIN).

        Args:
            agent_id: Agent to brief.
            session_id: Current session ID (for working memory inclusion).
            max_tokens: Approximate token budget for the briefing.
            query: Optional topic to focus the briefing on.
            include_priorities: Include importance-ranked priority items.
            include_relevant: Include query-relevant search results (requires query).
            include_recent: Include recent journal activity.
            project_scope: Scope filter. Sentinel (...) = use config default.

        Returns:
            BriefingResult with structured content and recognition breakdown.
        """
        import asyncio
        from sqlalchemy import select, func

        if project_scope is ...:
            project_scope = self._config.project_scope

        max_chars = max_tokens * 4

        # ── 1. Gather data in parallel ──────────────────────────────────
        coros: dict[str, Any] = {
            "policies": self._policy.list(),
        }
        if include_priorities:
            coros["priorities"] = self.priorities(
                agent_id=agent_id, min_importance=7, since_days=30, limit=5,
            )
        if include_recent:
            coros["recent"] = self._journal.recent(agent_id, days=3, limit=5)
        if session_id:
            coros["working"] = self._working.get(session_id, agent_id)
        if query and include_relevant:
            coros["search"] = self.search(
                agent_id, query, top_k=10, bump_access=False,
                project_scope=project_scope,
            )

        keys = list(coros.keys())
        results_raw = await asyncio.gather(*coros.values(), return_exceptions=True)
        data: dict[str, Any] = {}
        for k, v in zip(keys, results_raw):
            data[k] = v if not isinstance(v, Exception) else []

        # ── 2. Count open conflicts ─────────────────────────────────────
        conflict_count = 0
        try:
            from nmem.db.models import MemoryConflictModel
            async with self._db.session() as session:
                result = await session.execute(
                    select(func.count(MemoryConflictModel.id)).where(
                        MemoryConflictModel.status == "open"
                    )
                )
                conflict_count = result.scalar() or 0
        except Exception:
            pass

        # ── 3. Budget adaptation ────────────────────────────────────────
        # At low budgets, only emit warnings + KNOWN one-liners.
        low_budget = max_tokens < 800
        high_budget = max_tokens >= 4000

        budget_warnings = int(max_chars * 0.15)
        budget_known = int(max_chars * (0.40 if high_budget else 0.30))
        budget_familiar = int(max_chars * (0.15 if high_budget else 0.10))
        budget_priorities = int(max_chars * 0.20)
        budget_recent = int(max_chars * 0.15)
        budget_working = int(max_chars * 0.10)

        sections: list[str] = []
        n_known = 0
        n_familiar = 0
        n_uncertain = 0
        n_included = 0
        n_available = 0

        # ── Helper: check if text contains warning keywords ─────────────
        def _is_warning(text: str) -> bool:
            lower = text.lower()
            return any(kw in lower for kw in self._WARNING_KEYWORDS)

        # ── Warnings section (always, if any) ───────────────────────────
        warnings: list[str] = []
        policies = data.get("policies", [])
        for p in policies:
            full_text = f"{p.key} {p.content}"
            if _is_warning(full_text):
                line = f"[!] {p.key}: {p.content[:120]}"
                warnings.append(line)
        priorities = data.get("priorities", [])
        for p in priorities:
            if _is_warning(p.content):
                imp = p.metadata.get("importance", "?") if p.metadata else "?"
                line = f"[!] [imp={imp}] {p.content[:120]}"
                warnings.append(line)

        if warnings:
            lines = ["### Warnings"]
            chars = 0
            for w in warnings:
                if chars + len(w) > budget_warnings:
                    break
                lines.append(w)
                chars += len(w) + 1
            sections.append("\n".join(lines))

        # ── Known Facts (from search results, full content) ─────────────
        search_results: list[SearchResult] = data.get("search", [])
        n_available += len(search_results)
        known_items = [r for r in search_results if r.recognition == "KNOWN"]
        familiar_items = [r for r in search_results if r.recognition == "FAMILIAR"]
        uncertain_items = [r for r in search_results if r.recognition == "UNCERTAIN"]

        if known_items and not low_budget:
            lines = ["### Known Facts (use directly)"]
            chars = 0
            for r in known_items:
                label = r.title or r.key or ""
                if high_budget:
                    line = f"[KNOWN] {label}: {r.content[:300]}"
                else:
                    line = f"[KNOWN] {label}: {r.content[:150]}"
                if chars + len(line) > budget_known:
                    break
                lines.append(line)
                chars += len(line) + 1
                n_known += 1
                n_included += 1
            sections.append("\n".join(lines))
        elif known_items and low_budget:
            # Low budget: one-liners only for KNOWN
            lines = ["### Known Facts"]
            chars = 0
            for r in known_items:
                label = r.title or r.key or ""
                line = f"[KNOWN] {label}"
                if chars + len(line) > budget_known:
                    break
                lines.append(line)
                chars += len(line) + 1
                n_known += 1
                n_included += 1
            sections.append("\n".join(lines))

        # ── Familiar (one-line stubs) ───────────────────────────────────
        if familiar_items and not low_budget:
            lines = ["### Familiar (verify if unsure)"]
            chars = 0
            for r in familiar_items:
                label = r.title or r.key or ""
                preview = r.content[:80].replace("\n", " ")
                line = f"[FAMILIAR] {label}: {preview}"
                if chars + len(line) > budget_familiar:
                    break
                lines.append(line)
                chars += len(line) + 1
                n_familiar += 1
                n_included += 1
            sections.append("\n".join(lines))

        # Count uncertain items (not included in output but tracked)
        n_uncertain = len(uncertain_items)

        # ── Priority Items ──────────────────────────────────────────────
        if include_priorities and priorities and not low_budget:
            lines = ["### Priority Items"]
            chars = 0
            for p in priorities:
                imp = p.metadata.get("importance", "?") if p.metadata else "?"
                line = f"- [imp={imp}] {p.content[:120]}"
                if chars + len(line) > budget_priorities:
                    break
                lines.append(line)
                chars += len(line) + 1
            sections.append("\n".join(lines))

        # ── Recent Activity ─────────────────────────────────────────────
        recent = data.get("recent", [])
        if include_recent and recent and not low_budget:
            lines = ["### Recent Activity"]
            chars = 0
            for e in recent:
                ts = e.created_at.strftime("%m-%d %H:%M") if e.created_at else "?"
                line = f"- [{ts}] ({e.entry_type}) {e.title[:80]}"
                if chars + len(line) > budget_recent:
                    break
                lines.append(line)
                chars += len(line) + 1
            sections.append("\n".join(lines))

        # ── Working Memory ──────────────────────────────────────────────
        working = data.get("working", [])
        if working and not low_budget:
            lines = [f"### Working Memory ({len(working)} slots)"]
            chars = 0
            for s in working:
                line = f"- [{s.slot}] {s.content[:100]}"
                if chars + len(line) > budget_working:
                    break
                lines.append(line)
                chars += len(line) + 1
            sections.append("\n".join(lines))

        # ── Conflicts ───────────────────────────────────────────────────
        if conflict_count > 0:
            sections.append(f"### Open Conflicts: {conflict_count}")

        # ── Assemble ────────────────────────────────────────────────────
        if not sections:
            content_str = "No memory context available for briefing."
        else:
            header = f"## Session Briefing for {agent_id}\n"
            content_str = header + "\n\n".join(sections)

        # Include policy/priority counts in n_available
        n_available += len(policies) + len(priorities) + len(recent) + len(working)

        return BriefingResult(
            content=content_str,
            token_estimate=len(content_str) // 4,
            facts_included=n_included,
            facts_available=n_available,
            recognition_breakdown={
                "KNOWN": n_known,
                "FAMILIAR": n_familiar,
                "UNCERTAIN": n_uncertain,
            },
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
