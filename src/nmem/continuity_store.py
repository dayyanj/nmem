"""Durable continuity storage.

Thin DB helpers for the two persisted continuity artifacts — the versioned
autobiographical narrative and the per-agent immediate-continuity checkpoint.
The *assembly* (wake snapshot) lives in continuity.py and the *writing policy*
(re-grounding) in consolidation.py; this module is just read/write.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select

from nmem.db.models import ContinuityCheckpointModel, NarrativeSelfModel


def _scope_filter(model: Any, project_scope: str | None):
    return (
        model.project_scope.is_(None)
        if project_scope is None
        else model.project_scope == project_scope
    )


async def latest_narrative(db: Any, agent_id: str, project_scope: str | None = None) -> dict | None:
    """The newest narrative reconstruction for an agent (highest version)."""
    async with db.session() as s:
        row = (
            await s.execute(
                select(NarrativeSelfModel)
                .where(
                    NarrativeSelfModel.agent_id == agent_id,
                    _scope_filter(NarrativeSelfModel, project_scope),
                )
                .order_by(NarrativeSelfModel.version.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if row is None:
            return None
        return {
            "current_period": row.current_period,
            "longer_trajectory": row.longer_trajectory,
            "version": row.version,
            "token_len": row.token_len,
            "grounded_at": row.grounded_at,
            "provenance": row.provenance or [],
        }


async def write_narrative(
    db: Any,
    agent_id: str,
    project_scope: str | None,
    *,
    current_period: str,
    longer_trajectory: str | None,
    provenance: list,
    token_len: int,
    full_reconstruction: bool,
) -> int:
    """Append a new narrative version (never overwrites — drift stays auditable).

    Returns the new version number. ``full_reconstruction`` stamps
    ``last_full_reconstruction_at`` so a consistency check can tell a re-grounded
    narrative from an incremental one.
    """
    now = datetime.now(timezone.utc)
    async with db.session() as s:
        prev = (
            await s.execute(
                select(NarrativeSelfModel.version)
                .where(
                    NarrativeSelfModel.agent_id == agent_id,
                    _scope_filter(NarrativeSelfModel, project_scope),
                )
                .order_by(NarrativeSelfModel.version.desc())
                .limit(1)
            )
        ).scalar()
        version = (prev or 0) + 1
        s.add(
            NarrativeSelfModel(
                agent_id=agent_id,
                current_period=current_period,
                longer_trajectory=longer_trajectory,
                provenance=list(provenance),
                token_len=token_len,
                version=version,
                grounded_at=now,
                last_full_reconstruction_at=now if full_reconstruction else None,
                project_scope=project_scope,
            )
        )
    return version


async def get_checkpoint(db: Any, agent_id: str, project_scope: str | None = None) -> dict | None:
    """The immediate-continuity checkpoint for an agent, if any."""
    async with db.session() as s:
        row = (
            await s.execute(
                select(ContinuityCheckpointModel).where(
                    ContinuityCheckpointModel.agent_id == agent_id,
                    _scope_filter(ContinuityCheckpointModel, project_scope),
                )
            )
        ).scalars().first()
        if row is None:
            return None
        return {
            "last_interaction_summary": row.last_interaction_summary,
            "last_action": row.last_action,
            "interrupted_work": row.interrupted_work,
            "expected_next_action": row.expected_next_action,
            "updated_at": row.updated_at,
        }


async def save_checkpoint(
    db: Any,
    agent_id: str,
    project_scope: str | None = None,
    *,
    last_interaction_summary: str | None = None,
    last_action: str | None = None,
    interrupted_work: str | None = None,
    expected_next_action: str | None = None,
) -> None:
    """Upsert the per-agent checkpoint (one row per agent+scope).

    Read-then-write upsert — correct for a NULL scope, where a unique index would
    treat NULLs as distinct. Single-writer-safe (the michelle pilot); concurrent
    writers for one agent_id are a Phase-4 concern (add a CAS/version then).
    Only non-None fields are applied, so partial updates preserve prior values.
    """
    fields = {
        "last_interaction_summary": last_interaction_summary,
        "last_action": last_action,
        "interrupted_work": interrupted_work,
        "expected_next_action": expected_next_action,
    }
    async with db.session() as s:
        row = (
            await s.execute(
                select(ContinuityCheckpointModel).where(
                    ContinuityCheckpointModel.agent_id == agent_id,
                    _scope_filter(ContinuityCheckpointModel, project_scope),
                )
            )
        ).scalars().first()
        if row is None:
            s.add(ContinuityCheckpointModel(
                agent_id=agent_id, project_scope=project_scope,
                **{k: v for k, v in fields.items() if v is not None},
            ))
        else:
            for k, v in fields.items():
                if v is not None:
                    setattr(row, k, v)
