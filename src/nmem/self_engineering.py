"""
Self-engineering — nmem distills its own reusable context.

During consolidation (opt-in), nmem compresses reliable skills + their original
memories into compact **context recipes**: advisory prompt fragments it injects
into its OWN assembled context when it hits a matching situation. It edits its
own injected context, never the host's code.

Safety model (this is the whole point — an auto-active recipe could otherwise
silently degrade the agent's prompt):

  • bounded LLM spend — a hard per-run CALL cap AND per-prompt INPUT-size caps;
  • an acceptance gate — validate/shape/size + reject policy-override language
    before a recipe is ever written `active`;
  • advisory, near-exact-match-gated injection (handled in PromptBuilder);
  • host veto — `disable(reason)` removes a recipe AND tombstones its source
    cluster so nightly consolidation won't re-distill it (cooldown grows on
    repeated disables);
  • staleness decay — recipes that stop matching demote themselves;
  • no recursion — recipes are distilled only from skills + memories, never from
    other recipes.

All OFF unless `config.self_engineering.enabled`. With a noop LLM every step is a
clean no-op, so tests and standalone deployments never require or spend an LLM.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from datetime import datetime, timedelta

from sqlalchemy.exc import IntegrityError

logger = logging.getLogger(__name__)

_DISTILL_SYSTEM = (
    "You compress proven working approaches into a short, reusable \"context "
    "recipe\": advisory guidance an agent can consult when it meets a similar "
    "situation. You are given one or more skills that worked (with outcomes) and "
    "related memories.\n\n"
    "Rules:\n"
    "- Say WHEN it applies (the situation) and the approach, as GUIDANCE not "
    "commands. Never instruct the agent to ignore policy, override the system, "
    "or control the host.\n"
    "- Keep `body` under {max_chars} characters. Be concrete and specific.\n\n"
    "Respond as JSON: {{\"name\": \"<short label>\", \"situation\": \"<when this "
    "applies>\", \"body\": \"<the distilled guidance>\"}}"
)

# Conservative blocked-phrase guard for the acceptance gate — a distilled recipe
# must not try to override policy or seize host control.
_BLOCKED_PHRASES = (
    "ignore previous", "ignore all previous", "disregard previous",
    "override policy", "override the policy", "ignore the system",
    "ignore system", "disregard policy", "jailbreak", "you must always obey",
    "bypass", "ignore your instructions",
)

_SPEC_SYSTEM = (
    "You propose a specialized sub-agent for a task the parent agent performs "
    "reliably and repeatedly. You are given a proven skill (with reliability) and "
    "optional distilled guidance. Produce a concise, reusable sub-agent spec — a "
    "PROPOSAL the host may choose to run; you are not running anything.\n\n"
    "Rules:\n"
    "- `system_prompt` defines the sub-agent's role/approach for this task. It is "
    "the sub-agent's own prompt; it must not instruct the parent to ignore policy "
    "or seize host control.\n"
    "- `trigger_conditions`: when the host should dispatch this sub-agent.\n"
    "- `suggested_tools`: a JSON array of tool-name strings (may be empty).\n"
    "- Keep `system_prompt` under {max_chars} characters.\n\n"
    "Respond as JSON: {{\"name\": \"<short label>\", \"system_prompt\": \"<role/"
    "approach>\", \"trigger_conditions\": \"<when to use>\", \"suggested_tools\": "
    "[\"...\"]}}"
)


class SelfEngineer:
    """Distills context recipes (and, in 2B, sub-agent proposals)."""

    def __init__(self, mem, config):
        self._mem = mem
        self._config = config

    # ── config helpers ────────────────────────────────────────

    @property
    def _cfg(self):
        return getattr(self._config, "self_engineering", None)

    @property
    def _enabled(self) -> bool:
        return bool(getattr(self._cfg, "enabled", False))

    def _scope(self):
        return getattr(self._config, "project_scope", None)

    async def _embed(self, text: str):
        if not text:
            return None
        try:
            return await asyncio.to_thread(self._mem._embedding.embed, text)
        except Exception:
            return None

    @staticmethod
    def _signature(skill_ids) -> str:
        canon = json.dumps(sorted(int(i) for i in skill_ids))
        return hashlib.sha256(canon.encode()).hexdigest()[:64]

    # ── nightly entry point (registered, self-gated) ──────────

    async def run_nightly(self) -> None:
        if not self._enabled:
            return
        try:
            await self.distill_recipes()
        except Exception as e:
            logger.warning("Recipe distillation failed: %s", e)
        try:
            await self.decay_recipes()
        except Exception as e:
            logger.warning("Recipe decay failed: %s", e)
        try:
            await self.propose_subagents()
        except Exception as e:
            logger.warning("Sub-agent proposal failed: %s", e)

    # ── 2A: distill ───────────────────────────────────────────

    async def distill_recipes(self) -> int:
        """Distill up to max_llm_calls_per_run recipes from reliable skills.
        Returns the number of recipes written."""
        if not self._enabled:
            return 0
        cfg = self._cfg
        max_calls = getattr(cfg, "max_llm_calls_per_run", 3)
        candidates = await self._candidate_skills()
        written = 0
        calls = 0
        for skill in candidates:
            if calls >= max_calls:
                break
            sig = self._signature([skill["id"]])
            if await self._has_recipe(sig):
                continue
            if await self._is_suppressed(sig, skill["project_scope"], skill["agent_id"]):
                continue
            memories = await self._related_memories(skill)
            result = await self._distill_one(skill, memories)
            calls += 1
            if result is None:
                continue
            ok, reason = self._accept(result)
            if not ok:
                await self._mem._emit("recipe.rejected", {
                    "source_skill_ids": [skill["id"]], "reason": reason})
                continue
            rid = await self._persist_recipe(skill, sig, result, memories)
            if rid is not None:
                written += 1
                await self._mem._emit("recipe.distilled", {
                    "id": rid, "name": result.get("name"),
                    "source_skill_ids": [skill["id"]],
                    "project_scope": skill["project_scope"]})
        return written

    async def _candidate_skills(self) -> list[dict]:
        from sqlalchemy import text as sa_text
        cfg = self._cfg
        # reliability = success/trial ≥ min_reliability, trial ≥ min_trials
        params = {
            "min_trials": getattr(cfg, "min_trials", 3),
            "min_rel": getattr(cfg, "min_reliability", 0.7),
            "lim": getattr(cfg, "max_llm_calls_per_run", 3) * 3,
        }
        sql = sa_text("""
            SELECT id, name, what, outcome, success_count, trial_count,
                   agent_id, project_scope
            FROM nmem_skills
            WHERE status = 'active'
              AND trial_count >= :min_trials
              AND success_count >= :min_rel * trial_count
            ORDER BY success_count DESC, trial_count DESC, id ASC
            LIMIT :lim
        """)
        async with self._mem._db.session() as session:
            rows = (await session.execute(sql, params)).all()
        return [dict(r._mapping) for r in rows]

    async def _related_memories(self, skill: dict) -> list[str]:
        cfg = self._cfg
        k = getattr(cfg, "max_related_memories", 5)
        cap = getattr(cfg, "max_input_chars", 600)
        try:
            results = await self._mem.search(
                skill["agent_id"] or "default", skill["what"],
                top_k=k, bump_access=False,
                project_scope=skill["project_scope"], source="self_engineering")
        except Exception:
            return []
        return [r.content[:cap] for r in results[:k] if r.content]

    async def _distill_one(self, skill: dict, memories: list[str]) -> dict | None:
        cfg = self._cfg
        cap = getattr(cfg, "max_input_chars", 600)
        system = _DISTILL_SYSTEM.format(max_chars=getattr(cfg, "recipe_max_chars", 800))
        parts = [
            f"Skill: {skill['name']}",
            f"What worked: {(skill['what'] or '')[:cap]}",
            f"Outcome: {(skill['outcome'] or '')[:cap]}",
            f"Reliability: {skill['success_count']}/{skill['trial_count']}",
        ]
        if memories:
            parts.append("Related memories:")
            parts.extend(f"- {m}" for m in memories)
        user = "\n".join(parts)
        try:
            result = await self._mem._llm.complete_json(
                system, user,
                max_tokens=self._config.llm.synthesis_max_tokens,
                temperature=0.3, timeout=30.0)
        except Exception as e:
            logger.debug("Recipe LLM call failed: %s", e)
            return None
        if not isinstance(result, dict):
            return None   # noop / parse failure → clean no-op, no metering mutation
        # Meter the configured ceiling (estimate), like other consolidation steps.
        try:
            from nmem.token_stats import record_llm_usage
            await record_llm_usage(self._mem._db, "context_recipe",
                                   self._config.llm.synthesis_max_tokens)
        except Exception:
            pass
        return result

    def _accept(self, result: dict) -> tuple[bool, str]:
        """Acceptance gate — validate/shape/size + reject override language.
        Malformed (non-string) fields are cleanly rejected, never raised."""
        name = result.get("name")
        situation = result.get("situation")
        body = result.get("body")
        if not all(isinstance(x, str) for x in (name, situation, body)):
            return False, "missing_fields"
        name, situation, body = name.strip(), situation.strip(), body.strip()
        if not (name and situation and body):
            return False, "missing_fields"
        cap = getattr(self._cfg, "recipe_max_chars", 800)
        # Size the persisted fields, not just body — situation is embedded + stored.
        if len(body) > cap or len(situation) > cap:
            return False, "oversized"
        blob = f"{name}\n{situation}\n{body}".lower()
        if any(p in blob for p in _BLOCKED_PHRASES):
            return False, "blocked_language"
        return True, ""

    async def _persist_recipe(self, skill: dict, sig: str,
                              result: dict, memories: list[str]) -> int | None:
        from nmem.db.models import ContextRecipeModel
        situation = result["situation"].strip()
        embedding = await self._embed(situation)
        async with self._mem._db.session() as session:
            row = ContextRecipeModel(
                name=result["name"].strip()[:200],
                situation=situation,
                body=result["body"].strip(),
                trigger_embedding=embedding,
                source_signature=sig,
                source_skill_ids=[skill["id"]],
                evidence={
                    "skill": skill["name"],
                    "reliability": f"{skill['success_count']}/{skill['trial_count']}",
                    "n_memories": len(memories),
                },
                status="active", salience=1.0,
                agent_id=skill["agent_id"], project_scope=skill["project_scope"],
            )
            session.add(row)
            await session.flush()
            return row.id

    async def _has_recipe(self, sig: str) -> bool:
        from sqlalchemy import select
        from nmem.db.models import ContextRecipeModel
        async with self._mem._db.session() as session:
            row = (await session.execute(
                select(ContextRecipeModel.id).where(
                    ContextRecipeModel.source_signature == sig,
                    ContextRecipeModel.status.in_(("active", "superseded"))))
            ).first()
        return row is not None

    async def _is_suppressed(self, sig, project_scope, agent_id) -> bool:
        from sqlalchemy import select
        from nmem.db.models import RecipeTombstoneModel
        async with self._mem._db.session() as session:
            row = (await session.execute(
                select(RecipeTombstoneModel.suppressed_until).where(
                    RecipeTombstoneModel.source_signature == sig,
                    RecipeTombstoneModel.project_scope.is_(None) if project_scope is None
                    else RecipeTombstoneModel.project_scope == project_scope,
                    RecipeTombstoneModel.agent_id.is_(None) if agent_id is None
                    else RecipeTombstoneModel.agent_id == agent_id))
            ).first()
        if row is None or row[0] is None:
            return False
        return row[0] > datetime.utcnow()

    # ── 2A: find (injection support) ──────────────────────────

    async def find_recipe(self, query: str, *, agent_id=None,
                          project_scope=...) -> list[dict]:
        """Near-exact match over active recipes for injection. Empty when off."""
        if not self._enabled:
            return []
        cfg = self._cfg
        limit = getattr(cfg, "find_limit", 2)
        threshold = getattr(cfg, "min_match_similarity", 0.6)
        embedding = await self._embed(query)
        if embedding is None:
            return []
        if project_scope is ...:
            project_scope = self._scope()

        from sqlalchemy import text as sa_text
        emb_str = f"[{','.join(str(x) for x in embedding)}]"
        params = {"emb": emb_str, "th": threshold, "lim": limit}
        if project_scope == "*":
            scope_sql = ""
        elif project_scope is None:
            scope_sql = "AND project_scope IS NULL"
        else:
            scope_sql = "AND (project_scope = :scope OR project_scope IS NULL)"
            params["scope"] = project_scope
        # Agent filter, like skills.find — a recipe distilled for agent A must not
        # inject into agent B's prompt (agent-null recipes are shared).
        agent_sql = ""
        if agent_id is not None:
            agent_sql = "AND (agent_id = :agent_id OR agent_id IS NULL)"
            params["agent_id"] = agent_id
        sql = sa_text(f"""
            SELECT id, name, situation, body,
                   1 - (trigger_embedding <=> CAST(:emb AS vector)) AS sim
            FROM nmem_context_recipes
            WHERE status = 'active' AND trigger_embedding IS NOT NULL
              {scope_sql}
              {agent_sql}
              AND 1 - (trigger_embedding <=> CAST(:emb AS vector)) >= :th
            ORDER BY trigger_embedding <=> CAST(:emb AS vector)
            LIMIT :lim
        """)
        async with self._mem._db.session() as session:
            rows = (await session.execute(sql, params)).all()
            if rows:
                from sqlalchemy import update
                from nmem.db.models import ContextRecipeModel
                await session.execute(
                    update(ContextRecipeModel)
                    .where(ContextRecipeModel.id.in_([r[0] for r in rows]))
                    .values(last_matched_at=datetime.utcnow()))
        return [{"id": r[0], "name": r[1], "situation": r[2], "body": r[3],
                 "similarity": float(r[4])} for r in rows]

    async def mark_injected(self, recipe_ids: list[int]) -> None:
        if not self._enabled or not recipe_ids:
            return
        from sqlalchemy import update
        from nmem.db.models import ContextRecipeModel
        async with self._mem._db.session() as session:
            await session.execute(
                update(ContextRecipeModel)
                .where(ContextRecipeModel.id.in_(recipe_ids))
                .values(last_injected_at=datetime.utcnow()))

    # ── 2A: host veto + inspection ────────────────────────────

    async def disable(self, recipe_id: int, reason: str = "") -> bool:
        """Host veto: disable a recipe AND tombstone its source cluster so it
        isn't re-distilled (cooldown grows on repeated disables)."""
        if not self._enabled:
            return False
        from sqlalchemy import select
        from nmem.db.models import ContextRecipeModel
        async with self._mem._db.session() as session:
            row = (await session.execute(
                select(ContextRecipeModel).where(ContextRecipeModel.id == recipe_id))
            ).scalar_one_or_none()
            if row is None:
                return False
            row.status = "disabled"
            row.disabled_reason = reason or None
            sig = row.source_signature
            scope = row.project_scope
            agent = row.agent_id
        await self._tombstone(sig, scope, agent, reason)
        await self._mem._emit("recipe.disabled", {"id": recipe_id, "reason": reason})
        return True

    async def _tombstone(self, sig, project_scope, agent_id, reason) -> None:
        from sqlalchemy import select
        from nmem.db.models import RecipeTombstoneModel
        base = getattr(self._cfg, "tombstone_base_cooldown_days", 14)
        async with self._mem._db.session() as session:
            row = (await session.execute(
                select(RecipeTombstoneModel).where(
                    RecipeTombstoneModel.source_signature == sig,
                    RecipeTombstoneModel.project_scope.is_(None) if project_scope is None
                    else RecipeTombstoneModel.project_scope == project_scope,
                    RecipeTombstoneModel.agent_id.is_(None) if agent_id is None
                    else RecipeTombstoneModel.agent_id == agent_id))
            ).scalar_one_or_none()
            if row is None:
                count = 1
                row = RecipeTombstoneModel(
                    source_signature=sig, project_scope=project_scope,
                    agent_id=agent_id, disable_count=count, last_reason=reason or None,
                    suppressed_until=datetime.utcnow() + timedelta(days=base))
                session.add(row)
            else:
                row.disable_count += 1
                count = row.disable_count
                row.last_reason = reason or None
                # cooldown grows with the number of times this cluster misfired
                row.suppressed_until = datetime.utcnow() + timedelta(days=base * count)

    async def list(self, status: str = "active") -> list[dict]:
        if not self._enabled:
            return []
        from sqlalchemy import select
        from nmem.db.models import ContextRecipeModel
        async with self._mem._db.session() as session:
            rows = (await session.execute(
                select(ContextRecipeModel)
                .where(ContextRecipeModel.status == status)
                .order_by(ContextRecipeModel.updated_at.desc()))).scalars().all()
        return [{"id": r.id, "name": r.name, "situation": r.situation,
                 "body": r.body, "status": r.status,
                 "disabled_reason": r.disabled_reason,
                 "evidence": r.evidence} for r in rows]

    # ── 2A: staleness decay ───────────────────────────────────

    async def decay_recipes(self) -> None:
        """Demote active recipes that haven't matched/injected within the stale
        window so they stop surfacing (superseded). No-op when disabled."""
        if not self._enabled:
            return
        from sqlalchemy import text as sa_text
        stale_days = getattr(self._cfg, "decay_stale_days", 60)
        cutoff = datetime.utcnow() - timedelta(days=stale_days)
        async with self._mem._db.session() as session:
            await session.execute(sa_text("""
                UPDATE nmem_context_recipes
                SET status = 'superseded', updated_at = NOW()
                WHERE status = 'active'
                  AND COALESCE(last_matched_at, last_injected_at, created_at) < :cutoff
            """), {"cutoff": cutoff})

    # ── 2B: sub-agent proposals ───────────────────────────────

    async def propose_subagents(self) -> int:
        """Distill rich, PROPOSE-ONLY sub-agent specs from highly-reliable skills.
        nmem never runs them. Gated on `propose_subagents`; bounded by its own
        `max_llm_calls_per_run`. Returns the number of proposals written."""
        if not self._enabled or not getattr(self._cfg, "propose_subagents", False):
            return 0
        cfg = self._cfg
        max_calls = getattr(cfg, "max_llm_calls_per_run", 3)
        candidates = await self._proposal_candidates()
        written = 0
        calls = 0
        for skill in candidates:
            if calls >= max_calls:
                break
            sig = self._signature([skill["id"]])
            if await self._has_proposal(sig):
                continue
            recipe_id, recipe_body = await self._healthy_recipe(sig)
            result = await self._distill_spec(skill, recipe_body)
            calls += 1
            if result is None:
                continue
            ok, reason = self._accept_spec(result)
            if not ok:
                await self._mem._emit("subagent.rejected", {
                    "source_skill_ids": [skill["id"]], "reason": reason})
                continue
            pid = await self._persist_proposal(skill, sig, result, recipe_id, recipe_body)
            if pid is not None:
                written += 1
                await self._mem._emit("subagent.proposed", {
                    "id": pid, "name": result.get("name"),
                    "source_skill_ids": [skill["id"]],
                    "project_scope": skill["project_scope"]})
        return written

    async def _proposal_candidates(self) -> list[dict]:
        from sqlalchemy import text as sa_text
        cfg = self._cfg
        params = {
            "min_trials": getattr(cfg, "min_trials", 3),
            "min_rel": getattr(cfg, "subagent_min_reliability", 0.8),
            "lim": getattr(cfg, "max_llm_calls_per_run", 3) * 3,
        }
        sql = sa_text("""
            SELECT id, name, what, outcome, success_count, trial_count,
                   agent_id, project_scope
            FROM nmem_skills
            WHERE status = 'active'
              AND trial_count >= :min_trials
              AND success_count >= :min_rel * trial_count
            ORDER BY success_count DESC, trial_count DESC, id ASC
            LIMIT :lim
        """)
        async with self._mem._db.session() as session:
            rows = (await session.execute(sql, params)).all()
        return [dict(r._mapping) for r in rows]

    async def _healthy_recipe(self, sig: str) -> tuple[int | None, str]:
        """The active (non-disabled/superseded) recipe for this cluster, if any."""
        from sqlalchemy import select
        from nmem.db.models import ContextRecipeModel
        async with self._mem._db.session() as session:
            row = (await session.execute(
                select(ContextRecipeModel.id, ContextRecipeModel.body).where(
                    ContextRecipeModel.source_signature == sig,
                    ContextRecipeModel.status == "active"))
            ).first()
        return (row[0], row[1]) if row is not None else (None, "")

    async def _has_proposal(self, sig: str) -> bool:
        from sqlalchemy import select
        from nmem.db.models import SubagentProposalModel
        async with self._mem._db.session() as session:
            row = (await session.execute(
                select(SubagentProposalModel.id).where(
                    SubagentProposalModel.source_signature == sig,
                    SubagentProposalModel.status.in_(("proposed", "accepted"))))
            ).first()
        return row is not None

    async def _distill_spec(self, skill: dict, recipe_body: str) -> dict | None:
        cfg = self._cfg
        cap = getattr(cfg, "max_input_chars", 600)
        system = _SPEC_SYSTEM.format(max_chars=getattr(cfg, "recipe_max_chars", 800))
        parts = [
            f"Skill: {skill['name']}",
            f"What worked: {(skill['what'] or '')[:cap]}",
            f"Outcome: {(skill['outcome'] or '')[:cap]}",
            f"Reliability: {skill['success_count']}/{skill['trial_count']}",
        ]
        if recipe_body:
            parts.append(f"Distilled guidance: {recipe_body[:cap]}")
        try:
            result = await self._mem._llm.complete_json(
                system, "\n".join(parts),
                max_tokens=self._config.llm.synthesis_max_tokens,
                temperature=0.3, timeout=30.0)
        except Exception as e:
            logger.debug("Spec LLM call failed: %s", e)
            return None
        if not isinstance(result, dict):
            return None   # noop / parse failure → clean no-op, no metering mutation
        try:
            from nmem.token_stats import record_llm_usage
            await record_llm_usage(self._mem._db, "subagent_proposal",
                                   self._config.llm.synthesis_max_tokens)
        except Exception:
            pass
        return result

    def _accept_spec(self, result: dict) -> tuple[bool, str]:
        cap = getattr(self._cfg, "recipe_max_chars", 800)
        name = result.get("name")
        prompt = result.get("system_prompt")
        trigger = result.get("trigger_conditions", "")
        # name/system_prompt required strings; trigger_conditions optional but,
        # if present, must be a string (not silently coerced later).
        if not all(isinstance(x, str) for x in (name, prompt)):
            return False, "missing_fields"
        if not isinstance(trigger, str):
            return False, "missing_fields"
        name, prompt, trigger = name.strip(), prompt.strip(), trigger.strip()
        if not (name and prompt):
            return False, "missing_fields"
        # Size every persisted text field, not just system_prompt.
        if len(prompt) > cap or len(trigger) > cap:
            return False, "oversized"
        # Tools: a bounded list of non-empty, reasonably-short strings.
        tools = result.get("suggested_tools")
        if tools is not None:
            if not isinstance(tools, list) or len(tools) > 10:
                return False, "bad_tools"
            for t in tools:
                if not isinstance(t, str) or not t.strip() or len(t) > 60:
                    return False, "bad_tools"
        tool_blob = " ".join(tools) if isinstance(tools, list) else ""
        blob = f"{name}\n{prompt}\n{trigger}\n{tool_blob}".lower()
        if any(p in blob for p in _BLOCKED_PHRASES):
            return False, "blocked_language"
        return True, ""

    async def _persist_proposal(self, skill: dict, sig: str, result: dict,
                                recipe_id: int | None, recipe_body: str) -> int | None:
        from nmem.db.models import SubagentProposalModel
        tools = result.get("suggested_tools")
        async with self._mem._db.session() as session:
            row = SubagentProposalModel(
                name=result["name"].strip()[:200],
                system_prompt=result["system_prompt"].strip(),
                trigger_conditions=(result.get("trigger_conditions") or "").strip()
                    if isinstance(result.get("trigger_conditions"), str) else "",
                context_recipe_snapshot=recipe_body or "",
                source_recipe_id=recipe_id,
                suggested_tools=tools if isinstance(tools, list) else [],
                source_skill_ids=[skill["id"]],
                source_signature=sig,
                reliability_evidence={
                    "skill": skill["name"],
                    "success_count": skill["success_count"],
                    "trial_count": skill["trial_count"],
                    "reliability": (skill["success_count"] / skill["trial_count"]
                                    if skill["trial_count"] else 0.0),
                },
                status="proposed",
                agent_id=skill["agent_id"], project_scope=skill["project_scope"],
            )
            session.add(row)
            try:
                await session.flush()
            except IntegrityError:
                # A concurrent run already inserted a live proposal for this
                # cluster (partial-unique index). Fail closed.
                await session.rollback()
                return None
            return row.id

    async def list_proposals(self, status: str = "proposed") -> list[dict]:
        if not self._enabled:
            return []
        from sqlalchemy import select
        from nmem.db.models import SubagentProposalModel
        async with self._mem._db.session() as session:
            rows = (await session.execute(
                select(SubagentProposalModel)
                .where(SubagentProposalModel.status == status)
                .order_by(SubagentProposalModel.created_at.desc()))).scalars().all()
        return [{
            "id": r.id, "name": r.name, "system_prompt": r.system_prompt,
            "trigger_conditions": r.trigger_conditions,
            "context_recipe": r.context_recipe_snapshot,
            "suggested_tools": r.suggested_tools or [],
            "source_skill_ids": r.source_skill_ids or [],
            "reliability_evidence": r.reliability_evidence, "status": r.status,
        } for r in rows]

    async def resolve_proposal(self, proposal_id: int, accepted: bool) -> bool:
        """Host feedback (accept/dismiss) — NOT execution. Returns False if unknown."""
        if not self._enabled:
            return False
        from sqlalchemy import select
        from nmem.db.models import SubagentProposalModel
        async with self._mem._db.session() as session:
            row = (await session.execute(
                select(SubagentProposalModel).where(
                    SubagentProposalModel.id == proposal_id))
            ).scalar_one_or_none()
            if row is None:
                return False
            row.status = "accepted" if accepted else "dismissed"
        return True
