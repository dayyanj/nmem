"""Durable continuity storage.

Thin DB helpers for the two persisted continuity artifacts — the versioned
autobiographical narrative and the per-agent immediate-continuity checkpoint.
The *assembly* (wake snapshot) lives in continuity.py and the *writing policy*
(re-grounding) in consolidation.py; this module is just read/write.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import logging

from sqlalchemy import func, or_, select, text

from nmem.db.models import (
    ContinuityCheckpointModel,
    JournalEntryModel,
    LTMModel,
    NarrativeSelfModel,
    SharedKnowledgeModel,
)

logger = logging.getLogger(__name__)


def _scope_filter(model: Any, project_scope: str | None):
    return (
        model.project_scope.is_(None)
        if project_scope is None
        else model.project_scope == project_scope
    )


def _scope_or_global(model: Any, project_scope: str | None) -> list:
    """Visibility conditions matching the tiers' ``recent()`` semantics: a None (unscoped)
    instance sees everything (no filter); a scoped instance sees its own scope OR global
    (NULL). Returned as a list so it drops cleanly into a ``where(*conds)``."""
    if project_scope is None:
        return []
    return [or_(model.project_scope == project_scope, model.project_scope.is_(None))]


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
    treat NULLs as distinct. Concurrency-safe across writers: now that several
    turn-taking seams write this row (chat ``converse``, ``PeerExchange``,
    ``CommsLoop`` — possibly interleaved in one event loop, or concurrent studio
    ``/chat`` requests), the read-then-write is serialized per agent+scope by a
    Postgres advisory transaction lock held for the whole upsert. Two writers that
    both find the row absent can no longer each INSERT a duplicate (which
    ``get_checkpoint`` would then read inconsistently). The lock releases at commit.
    Only non-None fields are applied, so partial updates preserve prior values.
    """
    fields = {
        "last_interaction_summary": last_interaction_summary,
        "last_action": last_action,
        "interrupted_work": interrupted_work,
        "expected_next_action": expected_next_action,
    }
    async with db.session() as s:
        # Serialize concurrent writers on THIS agent+scope so the SELECT→INSERT below is
        # atomic w.r.t. other upserts (no duplicate rows). hashtext→int is fine: a rare
        # hash collision only over-serializes two unrelated checkpoints, never corrupts.
        await s.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:k))"),
            {"k": f"nmem_continuity_checkpoint:{agent_id}:{project_scope}"},
        )
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


async def delta_since(
    db: Any,
    agent_id: str,
    project_scope: str | None,
    since: datetime,
    *,
    limit: int = 5,
) -> dict:
    """What changed while the agent was away — the "returning after a gap" delta.

    Returns ``{"shared_new": [{"key","by"}...], "journal_new": int, "ltm_promoted": int}``:
      * ``shared_new`` — recent cross-agent canonical writes (another agent added to the
        shared tier since ``since``); in an isolated single-agent DB this is naturally
        empty (no other writer), which is correct.  [external change]
      * ``journal_new`` — how many of THIS agent's own journal entries accrued since
        ``since`` — the volume of logged activity that piled up.  [external/activity]
      * ``ltm_promoted`` — a conservative FLOOR on long-term memories the agent's own
        consolidation promoted since ``since`` — an INTROSPECTIVE signal of what its
        dreamstate did while away. Counts only ``source='promotion'`` rows (so direct API
        saves / importer writes, which are ``source='agent'``, are NOT misattributed to
        dreamstate) created since the cutoff. It UNDERCOUNTS promotions into a pre-existing
        key (``_promote_entry`` does ON CONFLICT DO UPDATE, preserving the old ``created_at``)
        — hence a floor, surfaced as "at least N". The narrative-regrounded flag independently
        confirms consolidation ran, so "dreamstate did work" still lands even when this is 0.

    Read-only, scoped, fail-open (returns the zero delta on any error). Intended to be
    called ONLY on a long-gap wake, so it never costs anything in continuous operation."""
    out: dict = {"shared_new": [], "journal_new": 0, "ltm_promoted": 0}
    # The ``created_at`` columns are tz-naive (TIMESTAMP WITHOUT TIME ZONE); asyncpg rejects
    # a tz-aware bind against them, so normalize the cutoff to naive UTC. (wake() keeps the
    # aware timestamp for elapsed-time arithmetic; only this DB comparison needs naive.)
    cutoff = since.astimezone(timezone.utc).replace(tzinfo=None) if since.tzinfo else since
    try:
        async with db.session() as s:
            rows = (
                await s.execute(
                    select(SharedKnowledgeModel.key, SharedKnowledgeModel.created_by)
                    .where(
                        SharedKnowledgeModel.created_at > cutoff,
                        SharedKnowledgeModel.created_by != agent_id,
                        SharedKnowledgeModel.superseded_by_id.is_(None),
                        *_scope_or_global(SharedKnowledgeModel, project_scope),
                    )
                    .order_by(SharedKnowledgeModel.created_at.desc())
                    .limit(limit)
                )
            ).all()
            out["shared_new"] = [{"key": k, "by": by} for k, by in rows]
            cnt = (
                await s.execute(
                    select(func.count())
                    .select_from(JournalEntryModel)
                    .where(
                        JournalEntryModel.agent_id == agent_id,
                        JournalEntryModel.created_at > cutoff,
                        *_scope_or_global(JournalEntryModel, project_scope),
                    )
                )
            ).scalar()
            out["journal_new"] = int(cnt or 0)
            # Introspective: long-term memories the agent's own consolidation promoted while
            # away (journal→LTM promotion writes an LTM row). "What my dreamstate did."
            ltm = (
                await s.execute(
                    select(func.count())
                    .select_from(LTMModel)
                    .where(
                        LTMModel.agent_id == agent_id,
                        LTMModel.created_at > cutoff,
                        LTMModel.source == "promotion",   # consolidation, not direct saves/imports
                        *_scope_or_global(LTMModel, project_scope),
                    )
                )
            ).scalar()
            out["ltm_promoted"] = int(ltm or 0)
    except Exception as e:  # noqa: BLE001
        logger.warning("continuity delta_since failed: %s", e, exc_info=True)
    return out
