"""SymbolGoalStore — adapt nmem-sym's ``symbol_goals`` to nmem-act's ``GoalStore``.

The reusable seam between the goal queue (nmem-sym) and the goal-pursuit lifecycle
(nmem-act's :class:`~nmem_act.GoalPursuit`): it exposes actionable / claim / resolve
/ release / recover over ``symbol_goals`` so nmem-act can own the lifecycle without
importing nmem-sym. Pure adapter — no app logic — so any agent that produces
``drive_intent`` goals in nmem-sym and actuates them through nmem-act reuses it.

Graduated from michelle-ai (``service/pursuit_store.py``) after it ran live (upstream
plan Phase 3).
"""
from __future__ import annotations

from typing import Any

from nmem_act import PursuitGoal
from nmem_sym.goals import get_actionable_goals, mark_goal_pursuing, resolve_goal


def _goal_id(g: Any):
    return getattr(g, "id", None) if not isinstance(g, dict) else g.get("id")


def _goal_objective(g: Any) -> str:
    return (getattr(g, "objective", None) if not isinstance(g, dict) else g.get("objective")) or ""


def _goal_source_type(g: Any):
    return getattr(g, "source_type", None) if not isinstance(g, dict) else g.get("source_type")


class SymbolGoalStore:
    """nmem-act ``GoalStore`` over ``symbol_goals``, filtered to one ``source_type`` and —
    in a hive (Path B / B-i) — to one ``owner_agent``.

    ``owner_agent=None`` = unscoped = today's behavior (additive). When set, EVERY operation
    is strictly scoped to that agent: an agent only sees / claims / releases / recovers its
    OWN goals — it can never touch another agent's on a shared graph. Requires the
    ``owner_agent`` column (nmem-sym migration 017)."""

    # G2-integration (§30.4): `dispatch` selects which plan_states this store pursues.
    #   'dispatchable' = the legacy drive pursuit (plan_state none/executing, source-filtered) —
    #                    default, behavior-identical.
    #   'planned'      = the PLANNED pursuit (plan_state planned/executing, any source_type) that
    #                    drains validated-but-unexecuted plans (e.g. michelle's "Establish: X" goals).
    # `_inflight` = the plan_state a CLAIMED goal of this store sits in (so recover is selector-aware
    # and can't reset the OTHER store's live claim, §30.6 P1-7).
    _DISPATCH = {
        "dispatchable": {"plan_states": ("none", "executing"), "inflight": ("none",)},
        "planned":      {"plan_states": ("planned", "executing"), "inflight": ("executing",)},
    }

    def __init__(self, pool, *, source_type: str | None = "drive_intent",
                 owner_agent: str | None = None, dispatch: str = "dispatchable") -> None:
        self._pool = pool
        self._source_type = source_type
        self._owner = owner_agent
        self._dispatch = dispatch if dispatch in self._DISPATCH else "dispatchable"

    async def actionable(self, limit: int) -> list[PursuitGoal]:
        rows = await get_actionable_goals(
            self._pool, source_type=self._source_type, limit=limit, owner_agent=self._owner,
            plan_states=self._DISPATCH[self._dispatch]["plan_states"])
        out: list[PursuitGoal] = []
        for r in rows:
            gid, obj = _goal_id(r), _goal_objective(r)
            if gid is not None and obj:
                out.append(PursuitGoal(id=gid, objective=obj,
                                       source_type=_goal_source_type(r)))
        return out

    async def claim(self, goal_id: Any) -> bool:
        # Atomic pending/active/decomposed -> pursuing (False if a concurrent cycle already
        # claimed it, OR — when owner-scoped — if the goal isn't ours). Admits this store's
        # plan_states and CONDITIONALLY advances planned->executing (§30.1).
        return await mark_goal_pursuing(self._pool, goal_id, self._owner, dispatch=self._dispatch)

    async def resolve(self, goal_id: Any, *, achieved: bool) -> None:
        if achieved:
            await resolve_goal(self._pool, goal_id, "achieved", owner_agent=self._owner)
            return
        # §30.5: for the PLANNED store, a COMPLETED-but-unverified pursuit is a retryable attempt, not
        # an immediate terminal failure — increment `attempts`, release back to 'planned' below the
        # cap, terminalize to 'failed' at the cap (bounds sandbox churn on an unachievable goal). The
        # legacy drive store keeps today's behavior (terminal 'failed').
        if self._dispatch == "planned":
            from nmem_sym import config as _sym_config
            cap = int(getattr(_sym_config.settings, "goal_plan_max_attempts", 3))
            row = await self._pool.fetchrow(
                "UPDATE symbol_goals SET attempts = attempts + 1, updated_at = now() "
                "WHERE id=$1 AND ($2::text IS NULL OR owner_agent = $2) RETURNING attempts",
                goal_id, self._owner)
            if row is not None and (row["attempts"] or 0) < cap:
                await self.release(goal_id)   # executing->planned; retry on a later cycle
                return
        await resolve_goal(self._pool, goal_id, "failed", owner_agent=self._owner)

    async def release(self, goal_id: Any) -> None:
        # Un-claim: back to pending, and (for a planned goal) executing->planned so the planned
        # store can re-pursue it. CONDITIONAL → a legacy drive goal (plan_state 'none') is untouched.
        await self._pool.execute(
            "UPDATE symbol_goals SET status='pending', "
            "plan_state = CASE WHEN plan_state='executing' THEN 'planned' ELSE plan_state END, "
            "updated_at=now() "
            "WHERE id=$1 AND status='pursuing' AND ($2::text IS NULL OR owner_agent = $2)",
            goal_id, self._owner)

    async def recover_orphaned(self) -> int:
        # OWNER-SCOPED (§10.2) AND SELECTOR-AWARE (§30.6 P1-7): recover only goals THIS store left
        # in-flight, keyed on the store's IN-FLIGHT plan_state (not source) — that alone makes the two
        # stores DISJOINT (drive claims sit at plan_state 'none'; planned claims at 'executing'), so
        # the planned store's recovery never resets a goal the drive store is live-executing, and
        # vice-versa. A planned goal resets executing->planned; a drive goal ('none') keeps its state.
        # With planning OFF every pursuing goal is 'none' → the drive store recovers them all, exactly
        # as the table-wide recover did (byte-identical).
        inflight = list(self._DISPATCH[self._dispatch]["inflight"])
        rows = await self._pool.fetch(
            "UPDATE symbol_goals SET status='pending', "
            "plan_state = CASE WHEN plan_state='executing' THEN 'planned' ELSE plan_state END, "
            "updated_at=now() "
            "WHERE status='pursuing' AND plan_state = ANY($2::text[]) "
            "  AND ($1::text IS NULL OR owner_agent = $1) RETURNING id",
            self._owner, inflight)
        return len(rows)
