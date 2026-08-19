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
        skills: object | None = None,
    ):
        self._working = working
        self._journal = journal
        self._ltm = ltm
        self._shared = shared
        self._entity = entity
        self._policy = policy
        self._db = db
        self._config = config
        self._skills = skills
        self._self_engineer = None   # wired by MemorySystem after construction

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

        # Skills: opt-in (config.skills.include_in_prompt). Query-relevant only.
        if query and self._skills is not None:
            skills_cfg = getattr(self._config, "skills", None)
            if getattr(skills_cfg, "include_in_prompt", False):
                tasks["skills"] = self._build_skills_prompt(agent_id, query)

        # Context recipes: opt-in advisory guidance (self_engineering.include_in_prompt).
        if query and self._self_engineer is not None:
            se_cfg = getattr(self._config, "self_engineering", None)
            if getattr(se_cfg, "include_in_prompt", False):
                tasks["context_recipes"] = self._build_recipes_prompt(agent_id, query)

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
            # Only reserve budget for a section when it is actually present —
            # otherwise weight_sum stays 1.0 and existing sections are budgeted
            # byte-for-byte as before (feature off ⇒ no change).
            if results.get("skills"):
                weights["skills"] = 0.15
            if results.get("context_recipes"):
                weights["context_recipes"] = 0.10
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
            skills=results.get("skills", ""),
            context_recipes=results.get("context_recipes", ""),
        )

        # Record token stats for trend tracking (fire-and-forget, never blocks)
        if self._db is not None:
            try:
                from nmem.token_stats import record_prompt_stats
                await record_prompt_stats(self._db, agent_id, ctx)
            except Exception:
                pass  # stats recording must never break prompt building

        return ctx

    async def _build_skills_prompt(self, agent_id: str, query: str) -> str:
        """Render query-relevant skills as a compact prompt section. Empty when
        skills are disabled or none match."""
        try:
            hits = await self._skills.find(query, agent_id=agent_id)
        except Exception as e:
            logger.warning("Failed to build skills prompt: %s", e)
            return ""
        if not hits:
            return ""
        lines: list[str] = []
        for s in hits:
            reliability = (f"{s.success_count}/{s.trial_count}"
                           if s.trial_count else "untested")
            lines.append(f"- {s.name} (reliability: {reliability})")
            if s.outcome:
                lines.append(f"  Outcome: {s.outcome[:160]}")
        return "\n".join(lines)

    async def _build_recipes_prompt(self, agent_id: str, query: str) -> str:
        """Render near-exact-matching context recipes as an advisory block,
        bounded by a TOTAL section budget (not just per-recipe)."""
        try:
            recipes = await self._self_engineer.find_recipe(query, agent_id=agent_id)
        except Exception as e:
            logger.warning("Failed to build recipes prompt: %s", e)
            return ""
        if not recipes:
            return ""
        se_cfg = getattr(self._config, "self_engineering", None)
        total_budget = getattr(se_cfg, "recipes_section_max_chars", 1200)
        lines: list[str] = []
        used = 0
        injected: list[int] = []
        for r in recipes:
            block = f"- **{r['name']}** — {r['body']}"
            if used + len(block) > total_budget:
                break
            lines.append(block)
            used += len(block) + 1
            injected.append(r["id"])
        if injected:
            try:
                await self._self_engineer.mark_injected(injected)
            except Exception:
                pass
        return "\n".join(lines)
