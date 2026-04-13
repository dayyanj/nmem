"""
Prompt builder — assembles memory context for injection into agent prompts.

Uses tiered verbosity: policies get full text, shared knowledge gets stubs,
journal gets title-only entries. Relevance-ranked when a query is provided.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from nmem.types import PromptContext

if TYPE_CHECKING:
    from nmem.tiers.working import WorkingMemoryTier
    from nmem.tiers.journal import JournalTier
    from nmem.tiers.ltm import LTMTier
    from nmem.tiers.shared import SharedTier
    from nmem.tiers.entity import EntityTier
    from nmem.tiers.policy import PolicyTier

logger = logging.getLogger(__name__)


class PromptBuilder:
    """Builds memory context for prompt injection."""

    def __init__(
        self,
        working: WorkingMemoryTier,
        journal: JournalTier,
        ltm: LTMTier,
        shared: SharedTier,
        entity: EntityTier,
        policy: PolicyTier,
        *,
        db: object | None = None,
        config: object | None = None,
    ):
        self._working = working
        self._journal = journal
        self._ltm = ltm
        self._shared = shared
        self._entity = entity
        self._policy = policy
        self._db = db
        self._config = config

    async def build(
        self,
        agent_id: str,
        session_id: str | None = None,
        query: str | None = None,
        *,
        entity_type: str | None = None,
        entity_id: str | None = None,
        max_total_tokens: int | None = None,
    ) -> PromptContext:
        """Build all memory sections for prompt injection.

        Args:
            agent_id: Agent identifier.
            session_id: Current session ID (for working memory).
            query: Optional query for relevance-ranked retrieval.
            entity_type: Optional entity type for dossier loading.
            entity_id: Optional entity ID for dossier loading.

        Returns:
            PromptContext with all sections populated.
        """
        import asyncio

        # Build all sections in parallel
        tasks = {
            "policy": self._policy.build_prompt(agent_id),
            "shared": self._shared.build_prompt(query=query),
            "ltm": self._ltm.build_prompt(agent_id, query=query),
            "journal": self._journal.build_prompt(agent_id, query=query),
        }

        if session_id:
            tasks["working"] = self._working.build_prompt(session_id, agent_id)

        if entity_type and entity_id:
            tasks["entity"] = self._entity.build_prompt(entity_type, entity_id)

        results = {}
        keys = list(tasks.keys())
        coros = list(tasks.values())
        gathered = await asyncio.gather(*coros, return_exceptions=True)

        for key, result in zip(keys, gathered):
            if isinstance(result, Exception):
                logger.warning("Failed to build %s prompt: %s", key, result)
                results[key] = ""
            else:
                results[key] = result

        # Token budget enforcement: truncate sections proportionally
        budget_tokens = max_total_tokens
        if budget_tokens is None and hasattr(self, '_config') and self._config is not None:
            prompt_cfg = getattr(self._config, 'prompt', None)
            if prompt_cfg:
                budget_tokens = prompt_cfg.max_total_tokens

        if budget_tokens and budget_tokens > 0:
            total_chars = budget_tokens * 4
            weights = {
                "policy": 0.10, "shared": 0.15, "ltm": 0.30,
                "journal": 0.20, "working": 0.10, "entity": 0.15,
            }
            weight_sum = sum(weights.values())
            for section_name, text in results.items():
                section_budget = int(total_chars * weights.get(section_name, 0.10) / weight_sum)
                if len(text) > section_budget:
                    # Truncate at last complete line within budget
                    truncated = text[:section_budget]
                    last_newline = truncated.rfind("\n")
                    if last_newline > 0:
                        truncated = truncated[:last_newline]
                    results[section_name] = truncated

        ctx = PromptContext(
            working=results.get("working", ""),
            journal=results.get("journal", ""),
            ltm=results.get("ltm", ""),
            shared=results.get("shared", ""),
            entity=results.get("entity", ""),
            policy=results.get("policy", ""),
        )

        # Record token stats for trend tracking (fire-and-forget, never blocks)
        if self._db is not None:
            try:
                from nmem.token_stats import record_prompt_stats
                await record_prompt_stats(self._db, agent_id, ctx)
            except Exception:
                pass  # stats recording must never break prompt building

        return ctx
