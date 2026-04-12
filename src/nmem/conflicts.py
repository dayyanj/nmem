"""
Conflict detection between memory records.

Detects contradictions across agents and tiers using text similarity
and embedding cosine distance. On write, `scan_conflicts` looks up
candidate records from the same table + scope and flags pairs where
text overlap is high but vector similarity diverges. At consolidation
time, `resolve_conflict` picks a winner using:

    grounding rank > agent trust > recency > importance

If no clear winner emerges, the conflict is left in `needs_review` for
a human (or a more confident later write) to settle.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import TYPE_CHECKING

import numpy as np
from sqlalchemy import and_, or_, select, text as sa_text

from nmem.db.models import (
    LTMModel,
    MemoryConflictModel,
    SharedKnowledgeModel,
)
from nmem.types import MemoryConflictInfo

if TYPE_CHECKING:
    from nmem.config import BeliefRevisionConfig
    from nmem.db.session import DatabaseManager

logger = logging.getLogger(__name__)

# Table name → ORM model, used by scan_conflicts to look up candidates.
_MODEL_BY_TABLE: dict[str, type] = {
    "nmem_long_term_memory": LTMModel,
    "nmem_shared_knowledge": SharedKnowledgeModel,
}


def text_similarity(a: str, b: str) -> float:
    """Compute Jaccard similarity between two texts (word-level).

    Args:
        a: First text.
        b: Second text.

    Returns:
        Jaccard similarity 0.0-1.0.
    """
    words_a = set(a.lower().split())
    words_b = set(b.lower().split())
    if not words_a or not words_b:
        return 0.0
    intersection = words_a & words_b
    union = words_a | words_b
    return len(intersection) / len(union)


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Compute cosine similarity between two embedding vectors.

    Args:
        a: First vector.
        b: Second vector.

    Returns:
        Cosine similarity -1.0 to 1.0.
    """
    a_np = np.array(a, dtype=np.float32).flatten()
    b_np = np.array(b, dtype=np.float32).flatten()
    if len(a_np) == 0 or len(b_np) == 0:
        return 0.0
    dot = float(np.dot(a_np, b_np))
    norm = float(np.linalg.norm(a_np) * np.linalg.norm(b_np))
    return dot / norm if norm > 0 else 0.0


async def check_conflict(
    db: DatabaseManager,
    content: str,
    embedding: list[float],
    agent_id: str,
    target_table: str,
    target_id: int,
    *,
    existing_content: str | None = None,
    existing_embedding: list[float] | None = None,
    existing_agent: str | None = None,
    existing_table: str | None = None,
    existing_id: int | None = None,
    project_scope: str | None = None,
    text_threshold: float = 0.7,
    vector_threshold: float = 0.85,
) -> MemoryConflictInfo | None:
    """Check if new content conflicts with existing content.

    A conflict is detected when text similarity is high (same topic) but
    embedding similarity shows divergent meaning.

    Args:
        db: Database manager.
        content: New content.
        embedding: New content embedding.
        agent_id: Agent writing the new content.
        target_table: Table name for the new record.
        target_id: Record ID for the new record.
        existing_content: Content to compare against.
        existing_embedding: Embedding to compare against.
        existing_agent: Agent that wrote the existing content.
        existing_table: Table name for the existing record.
        existing_id: Record ID for the existing record.
        project_scope: Project scope to record on the conflict row.
        text_threshold: Jaccard threshold for "same topic" detection.
        vector_threshold: Cosine threshold above which content is NOT conflicting.

    Returns:
        MemoryConflictInfo if conflict detected, None otherwise.
    """
    if not existing_content or not existing_embedding:
        return None
    if not existing_table or not existing_id or not existing_agent:
        return None

    text_sim = text_similarity(content, existing_content)
    vec_sim = cosine_similarity(embedding, existing_embedding)

    # High text overlap (same topic) + lower vector similarity (different meaning) = conflict
    if text_sim >= text_threshold and vec_sim < vector_threshold:
        description = (
            f"Potential contradiction: text_sim={text_sim:.2f}, "
            f"vec_sim={vec_sim:.2f}. "
            f"New: '{content[:100]}...' vs Existing: '{existing_content[:100]}...'"
        )

        async with db.session() as session:
            # Dedup: skip if a conflict row already exists for this pair
            # (either direction). The scan function already filters out
            # settled pairs; this guards write-time races where two writes
            # detect the same pair before consolidation runs.
            existing_row = (await session.execute(
                select(MemoryConflictModel).where(
                    or_(
                        and_(
                            MemoryConflictModel.record_a_table == target_table,
                            MemoryConflictModel.record_a_id == target_id,
                            MemoryConflictModel.record_b_table == existing_table,
                            MemoryConflictModel.record_b_id == existing_id,
                        ),
                        and_(
                            MemoryConflictModel.record_a_table == existing_table,
                            MemoryConflictModel.record_a_id == existing_id,
                            MemoryConflictModel.record_b_table == target_table,
                            MemoryConflictModel.record_b_id == target_id,
                        ),
                    )
                )
            )).scalar_one_or_none()
            if existing_row is not None:
                return None

            conflict = MemoryConflictModel(
                record_a_table=target_table,
                record_a_id=target_id,
                record_b_table=existing_table,
                record_b_id=existing_id,
                agent_a=agent_id,
                agent_b=existing_agent,
                similarity_score=vec_sim,
                description=description,
                project_scope=project_scope,
            )
            session.add(conflict)
            await session.flush()

            return MemoryConflictInfo(
                id=conflict.id,
                record_a_table=target_table,
                record_a_id=target_id,
                record_b_table=existing_table,
                record_b_id=existing_id,
                agent_a=agent_id,
                agent_b=existing_agent,
                similarity_score=vec_sim,
                description=description,
                project_scope=project_scope,
                created_at=conflict.created_at,
            )

    return None


# ── Scan: detect conflicts on write ─────────────────────────────────────────


async def scan_conflicts(
    db: DatabaseManager,
    *,
    content: str,
    embedding: list[float],
    agent_id: str,
    target_table: str,
    target_id: int,
    project_scope: str | None,
    config: BeliefRevisionConfig,
) -> list[MemoryConflictInfo]:
    """Scan a table for candidate records that conflict with the new row.

    Called from tier .save() methods after a successful write. Bounds the
    candidate set to `config.scan_candidates_limit` rows in the same scope
    + excludes the row we just wrote. Each candidate is passed through
    `check_conflict`, which dedups and inserts the conflict row if needed.

    Journal is intentionally NOT scanned — too high-volume, short-lived,
    and the nightly synthesis already catches semantic drift there.
    """
    if not config.enabled:
        return []

    model = _MODEL_BY_TABLE.get(target_table)
    if model is None:
        logger.debug("scan_conflicts: unknown table %s", target_table)
        return []

    # Look up recent rows in the same scope, excluding the target itself.
    filters = [model.id != target_id]
    if hasattr(model, "status"):
        filters.append(model.status == "validated")
    if hasattr(model, "project_scope"):
        if project_scope is None:
            filters.append(model.project_scope.is_(None))
        else:
            filters.append(model.project_scope == project_scope)

    async with db.session() as session:
        candidates = (
            await session.execute(
                select(model)
                .where(and_(*filters))
                .order_by(model.updated_at.desc() if hasattr(model, "updated_at") else model.id.desc())
                .limit(config.scan_candidates_limit)
            )
        ).scalars().all()

    conflicts: list[MemoryConflictInfo] = []
    for candidate in candidates:
        existing_embedding = getattr(candidate, "embedding", None)
        if existing_embedding is None:
            continue
        # pgvector types come back as lists already; normalize defensively.
        if hasattr(existing_embedding, "tolist"):
            existing_embedding = existing_embedding.tolist()

        # Shared knowledge rows don't carry an agent_id — they have
        # `last_updated_by`. Fall back on that for attribution.
        existing_agent = (
            getattr(candidate, "agent_id", None)
            or getattr(candidate, "last_updated_by", None)
            or "unknown"
        )

        info = await check_conflict(
            db,
            content=content,
            embedding=embedding,
            agent_id=agent_id,
            target_table=target_table,
            target_id=target_id,
            existing_content=candidate.content,
            existing_embedding=list(existing_embedding),
            existing_agent=existing_agent,
            existing_table=target_table,
            existing_id=candidate.id,
            project_scope=project_scope,
            text_threshold=config.text_similarity_threshold,
            vector_threshold=config.vector_divergence_threshold,
        )
        if info is not None:
            conflicts.append(info)

    if conflicts:
        logger.info(
            "scan_conflicts: detected %d new conflict(s) for %s#%d",
            len(conflicts), target_table, target_id,
        )
    return conflicts


# ── List: surface conflicts for MCP / CLI ─────────────────────────────────


async def list_conflicts(
    db: DatabaseManager,
    *,
    status: tuple[str, ...] = ("open",),
    agent_id: str | None = None,
    project_scope: str | None = ...,
    limit: int = 20,
    since_days: int | None = None,
) -> list[MemoryConflictInfo]:
    """List memory conflicts, filtered by status / agent / scope / recency.

    Args:
        db: Database manager.
        status: Tuple of statuses to include (default: just ``"open"``).
        agent_id: If set, only conflicts where this agent is on either side.
        project_scope: Scope filter. ``...`` (sentinel) = current scope + global,
            ``None`` = global only, ``"*"`` = all scopes.
        limit: Maximum rows.
        since_days: If set, only conflicts created in the last N days.

    Returns:
        List of MemoryConflictInfo objects, newest first.
    """
    filters = [MemoryConflictModel.status.in_(status)]
    if agent_id:
        filters.append(
            or_(
                MemoryConflictModel.agent_a == agent_id,
                MemoryConflictModel.agent_b == agent_id,
            )
        )
    # Scope filtering
    if project_scope == "*":
        pass  # All scopes — no filter
    elif project_scope is not ... and project_scope is not None:
        # Specific scope + global (NULL)
        filters.append(
            or_(
                MemoryConflictModel.project_scope == project_scope,
                MemoryConflictModel.project_scope.is_(None),
            )
        )
    elif project_scope is None:
        # Global only
        filters.append(MemoryConflictModel.project_scope.is_(None))
    # ... sentinel = no scope filter (backwards-compat default)

    if since_days is not None and since_days >= 0:
        from datetime import timedelta, timezone as tz
        cutoff = datetime.now(tz.utc) - timedelta(days=since_days)
        filters.append(MemoryConflictModel.created_at >= cutoff)

    async with db.session() as session:
        stmt = (
            select(MemoryConflictModel)
            .where(and_(*filters))
            .order_by(MemoryConflictModel.created_at.desc())
            .limit(max(limit, 1))
        )
        result = await session.execute(stmt)
        rows = result.scalars().all()

    return [
        MemoryConflictInfo(
            id=r.id,
            record_a_table=r.record_a_table,
            record_a_id=r.record_a_id,
            record_b_table=r.record_b_table,
            record_b_id=r.record_b_id,
            agent_a=r.agent_a,
            agent_b=r.agent_b,
            similarity_score=r.similarity_score,
            description=r.description,
            status=r.status,
            project_scope=r.project_scope,
            created_at=r.created_at,
        )
        for r in rows
    ]


# ── Resolve: pick a winner at consolidation time ────────────────────────────


def _grounding_rank(grounding: str, priority: list[str]) -> int:
    """Return a rank such that higher = stronger grounding.

    Values not in `priority` get rank -1 (lowest).
    """
    try:
        return len(priority) - priority.index(grounding or "")
    except ValueError:
        return -1


def _trust_for(agent: str, config: BeliefRevisionConfig) -> float:
    return config.agent_trust.get(agent, config.default_trust)


async def _load_record(db: DatabaseManager, table: str, record_id: int):
    """Fetch an ORM row by (table, id). Returns None if not found."""
    model = _MODEL_BY_TABLE.get(table)
    if model is None:
        return None
    async with db.session() as session:
        return (await session.execute(
            select(model).where(model.id == record_id)
        )).scalar_one_or_none()


async def resolve_conflict(
    db: DatabaseManager,
    conflict: MemoryConflictModel,
    config: BeliefRevisionConfig,
) -> str:
    """Resolve a single conflict row.

    Returns one of:
        "auto_resolved" — winner picked, loser marked superseded
        "needs_review"  — tied or tables unknown, status flipped for review
        "stale"         — one of the referenced records no longer exists
    """
    record_a = await _load_record(db, conflict.record_a_table, conflict.record_a_id)
    record_b = await _load_record(db, conflict.record_b_table, conflict.record_b_id)

    if record_a is None or record_b is None:
        async with db.session() as session:
            conflict_row = await session.get(MemoryConflictModel, conflict.id)
            if conflict_row is not None:
                conflict_row.status = "stale"
                conflict_row.settled_at = datetime.utcnow()
        return "stale"

    rank_a = _grounding_rank(getattr(record_a, "grounding", ""), config.grounding_priority)
    rank_b = _grounding_rank(getattr(record_b, "grounding", ""), config.grounding_priority)

    # Step 1: grounding rank gap
    gap = abs(rank_a - rank_b)
    winner = None
    loser = None
    rationale_key = ""
    if rank_a > rank_b and gap >= config.auto_resolve_grounding_gap:
        winner, loser, rationale_key = record_a, record_b, "grounding"
    elif rank_b > rank_a and gap >= config.auto_resolve_grounding_gap:
        winner, loser, rationale_key = record_b, record_a, "grounding"
    else:
        # Step 2: agent trust tiebreaker
        agent_a = getattr(record_a, "agent_id", None) or getattr(record_a, "last_updated_by", "")
        agent_b = getattr(record_b, "agent_id", None) or getattr(record_b, "last_updated_by", "")
        trust_a = _trust_for(agent_a, config)
        trust_b = _trust_for(agent_b, config)
        if abs(trust_a - trust_b) >= 0.1:
            if trust_a > trust_b:
                winner, loser, rationale_key = record_a, record_b, "agent_trust"
            else:
                winner, loser, rationale_key = record_b, record_a, "agent_trust"
        else:
            # Step 3: recency tiebreaker
            ts_a = getattr(record_a, "updated_at", None) or getattr(record_a, "created_at", None)
            ts_b = getattr(record_b, "updated_at", None) or getattr(record_b, "created_at", None)
            if ts_a and ts_b and ts_a != ts_b:
                if ts_a > ts_b:
                    winner, loser, rationale_key = record_a, record_b, "recency"
                else:
                    winner, loser, rationale_key = record_b, record_a, "recency"
            else:
                # Step 4: importance tiebreaker
                imp_a = getattr(record_a, "importance", 0) or 0
                imp_b = getattr(record_b, "importance", 0) or 0
                if imp_a != imp_b:
                    if imp_a > imp_b:
                        winner, loser, rationale_key = record_a, record_b, "importance"
                    else:
                        winner, loser, rationale_key = record_b, record_a, "importance"

    now = datetime.utcnow()
    if winner is None or loser is None:
        # No clear winner — defer to human / more confident future write.
        async with db.session() as session:
            conflict_row = await session.get(MemoryConflictModel, conflict.id)
            if conflict_row is not None:
                conflict_row.status = "needs_review"
                conflict_row.settled_at = now
        return "needs_review"

    # Auto-resolve: mark loser superseded, conflict resolved, stash JSON payload.
    resolution_payload = json.dumps({
        "rationale": rationale_key,
        "winner_table": conflict.record_a_table if winner is record_a else conflict.record_b_table,
        "winner_id": winner.id,
        "loser_table": conflict.record_a_table if loser is record_a else conflict.record_b_table,
        "loser_id": loser.id,
        "rank_a": rank_a,
        "rank_b": rank_b,
    })

    async with db.session() as session:
        loser_row = await session.get(type(loser), loser.id)
        if loser_row is not None:
            loser_row.status = "superseded"
            loser_row.superseded_by_id = winner.id

        conflict_row = await session.get(MemoryConflictModel, conflict.id)
        if conflict_row is not None:
            conflict_row.status = "auto_resolved"
            conflict_row.resolution = resolution_payload
            conflict_row.resolved_by = "consolidation"
            conflict_row.resolved_at = now
            conflict_row.settled_at = now

    return "auto_resolved"
