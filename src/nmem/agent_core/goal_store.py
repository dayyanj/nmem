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


class SymbolGoalStore:
    """nmem-act ``GoalStore`` over ``symbol_goals``, filtered to one ``source_type``."""

    def __init__(self, pool, *, source_type: str = "drive_intent") -> None:
        self._pool = pool
        self._source_type = source_type

    async def actionable(self, limit: int) -> list[PursuitGoal]:
        rows = await get_actionable_goals(
            self._pool, source_type=self._source_type, limit=limit)
        out: list[PursuitGoal] = []
        for r in rows:
            gid, obj = _goal_id(r), _goal_objective(r)
            if gid is not None and obj:
                out.append(PursuitGoal(id=gid, objective=obj))
        return out

    async def claim(self, goal_id: Any) -> bool:
        # Atomic pending/active/decomposed -> pursuing (False if a concurrent cycle
        # already claimed it).
        return await mark_goal_pursuing(self._pool, goal_id)

    async def resolve(self, goal_id: Any, *, achieved: bool) -> None:
        await resolve_goal(self._pool, goal_id, "achieved" if achieved else "failed")

    async def release(self, goal_id: Any) -> None:
        await self._pool.execute(
            "UPDATE symbol_goals SET status='pending', updated_at=now() "
            "WHERE id=$1 AND status='pursuing'", goal_id)

    async def recover_orphaned(self) -> int:
        rows = await self._pool.fetch(
            "UPDATE symbol_goals SET status='pending', updated_at=now() "
            "WHERE status='pursuing' RETURNING id")
        return len(rows)
