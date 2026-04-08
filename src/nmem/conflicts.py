"""
Conflict detection between memory records.

Detects contradictions across agents and tiers using text similarity
and embedding cosine distance. Conflicts are recorded for human review.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import numpy as np

from nmem.db.models import MemoryConflictModel
from nmem.types import MemoryConflictInfo

if TYPE_CHECKING:
    from nmem.db.session import DatabaseManager

logger = logging.getLogger(__name__)


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
            conflict = MemoryConflictModel(
                record_a_table=target_table,
                record_a_id=target_id,
                record_b_table=existing_table,
                record_b_id=existing_id,
                agent_a=agent_id,
                agent_b=existing_agent,
                similarity_score=vec_sim,
                description=description,
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
                created_at=conflict.created_at,
            )

    return None
