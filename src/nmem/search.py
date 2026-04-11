"""
Cross-tier unified search engine.

Searches across all memory tiers in parallel, merges and ranks results.
Implements the hybrid search algorithm (60/40 vector/FTS weighting).
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import TYPE_CHECKING

import numpy as np
from sqlalchemy import text as sa_text

from nmem.types import SearchResult

if TYPE_CHECKING:
    from nmem.db.session import DatabaseManager
    from nmem.tiers.journal import JournalTier
    from nmem.tiers.ltm import LTMTier
    from nmem.tiers.shared import SharedTier
    from nmem.tiers.entity import EntityTier

logger = logging.getLogger(__name__)

# Default tiers to search
DEFAULT_TIERS = ("journal", "ltm", "shared", "entity")


# ── Hybrid Search CTE ────────────────────────────────────────────────────────


async def hybrid_memory_search(
    db: DatabaseManager,
    table: str,
    query_embedding: list[float],
    query_text: str,
    where_clause: str,
    params: dict,
    top_k: int,
    vector_weight: float = 0.6,
    fts_weight: float = 0.4,
) -> list[tuple[int, float]]:
    """Hybrid search (vector + FTS) returning ranked (id, score) pairs.

    Uses a CTE pattern: vector similarity as the primary signal,
    FTS as a secondary re-ranking factor. Falls back gracefully
    when content_tsv is NULL (FTS score = 0).

    Args:
        db: Database manager.
        table: Table name to search.
        query_embedding: Query embedding vector.
        query_text: Query text for FTS.
        where_clause: SQL WHERE clause fragment (e.g., "agent_id = :agent_id").
        params: Parameters for the WHERE clause.
        top_k: Maximum results.
        vector_weight: Weight for vector similarity (default 0.6).
        fts_weight: Weight for FTS score (default 0.4).

    Returns:
        List of (id, combined_score) tuples, ranked by score.
    """
    # SQLite fallback: no pgvector operators, compute similarity in Python
    if not db.is_postgres:
        return await _sqlite_fallback_search(
            db, table, query_embedding, where_clause, params, top_k,
        )

    embedding_str = f"[{','.join(str(x) for x in query_embedding)}]"
    params.update({
        "embedding_vec": embedding_str,
        "query_text": query_text,
        "candidate_limit": top_k * 3,
        "top_k": top_k,
    })

    sql = sa_text(f"""
        WITH vector_scores AS (
            SELECT id,
                   1 - (embedding <=> CAST(:embedding_vec AS vector)) AS vec_score
            FROM {table}
            WHERE {where_clause}
              AND embedding IS NOT NULL
            ORDER BY embedding <=> CAST(:embedding_vec AS vector)
            LIMIT :candidate_limit
        ),
        fts_scores AS (
            SELECT vs.id,
                   CASE WHEN t.content_tsv IS NOT NULL
                        THEN ts_rank_cd(t.content_tsv, plainto_tsquery('english', :query_text))
                        ELSE 0 END AS fts_score
            FROM vector_scores vs
            JOIN {table} t ON vs.id = t.id
        )
        SELECT vs.id,
               ({vector_weight} * vs.vec_score
                + {fts_weight} * COALESCE(fs.fts_score, 0)) AS combined_score
        FROM vector_scores vs
        LEFT JOIN fts_scores fs ON vs.id = fs.id
        ORDER BY combined_score DESC
        LIMIT :top_k
    """)

    async with db.session() as session:
        result = await session.execute(sql, params)
        return [(row[0], float(row[1])) for row in result.all()]


async def _sqlite_fallback_search(
    db: DatabaseManager,
    table: str,
    query_embedding: list[float],
    where_clause: str,
    params: dict,
    top_k: int,
) -> list[tuple[int, float]]:
    """SQLite fallback: fetch candidates by importance, rank by cosine similarity in Python.

    This is only suitable for small datasets (demo, dev). Production should use PostgreSQL.
    """
    import json as json_mod

    # Fetch top candidates ordered by importance (best proxy without vector search)
    candidate_limit = top_k * 5
    # Use importance for ordering if the table has it, otherwise created_at
    sql = sa_text(
        f"SELECT id, embedding FROM {table} WHERE {where_clause} "
        f"ORDER BY id DESC LIMIT :candidate_limit"
    )
    params["candidate_limit"] = candidate_limit

    async with db.session() as session:
        result = await session.execute(sql, params)
        rows = result.all()

    if not rows:
        return []

    # Compute cosine similarity in Python
    scored = []
    for row_id, emb_data in rows:
        if emb_data is None:
            continue
        # Embedding stored as LargeBinary — could be JSON string or bytes
        if isinstance(emb_data, (bytes, memoryview)):
            try:
                emb = json_mod.loads(emb_data)
            except Exception:
                continue
        elif isinstance(emb_data, str):
            try:
                emb = json_mod.loads(emb_data)
            except Exception:
                continue
        elif isinstance(emb_data, list):
            emb = emb_data
        else:
            continue

        sim = cosine_similarity(query_embedding, emb)
        scored.append((row_id, sim))

    # Sort by similarity descending
    scored.sort(key=lambda x: x[1], reverse=True)
    return scored[:top_k]


# ── TSVECTOR Population ──────────────────────────────────────────────────────


async def populate_tsvector(
    db: DatabaseManager, table: str, record_id: int, text_content: str
) -> None:
    """Populate the content_tsv column for a record using PostgreSQL to_tsvector.

    Args:
        db: Database manager.
        table: Table name.
        record_id: Record ID.
        text_content: Text to vectorize.
    """
    if not db.is_postgres:
        return
    try:
        async with db.session() as session:
            await session.execute(
                sa_text(
                    f"UPDATE {table} SET content_tsv = to_tsvector('english', :text) WHERE id = :id"
                ),
                {"text": text_content[:4000], "id": record_id},
            )
    except Exception as e:
        logger.debug("TSVECTOR population for %s#%d failed (non-fatal): %s", table, record_id, e)


# ── Context Thread Assignment ────────────────────────────────────────────────

# In-memory centroid cache: {agent_id: {thread_id: centroid_embedding}}
_thread_centroids: dict[str, dict[str, list[float]]] = {}


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Compute cosine similarity between two embedding vectors."""
    a_np = np.array(a, dtype=np.float32).flatten()
    b_np = np.array(b, dtype=np.float32).flatten()
    if len(a_np) == 0 or len(b_np) == 0:
        return 0.0
    dot = float(np.dot(a_np, b_np))
    norm = float(np.linalg.norm(a_np) * np.linalg.norm(b_np))
    return dot / norm if norm > 0 else 0.0


def assign_context_thread(
    embedding: list[float], agent_id: str, threshold: float = 0.65
) -> str:
    """Assign an entry to a semantic context thread.

    If the embedding is >threshold similar to an existing thread's centroid,
    join it. Otherwise create a new thread.

    Args:
        embedding: Entry embedding vector.
        agent_id: Agent identifier.
        threshold: Cosine similarity threshold for joining.

    Returns:
        Thread ID string.
    """
    agent_threads = _thread_centroids.get(agent_id, {})

    best_thread = None
    best_sim = 0.0

    for thread_id, centroid in agent_threads.items():
        sim = cosine_similarity(embedding, centroid)
        if sim > best_sim:
            best_sim = sim
            best_thread = thread_id

    if best_sim >= threshold and best_thread:
        # Join existing thread — update centroid (running average)
        old = np.array(agent_threads[best_thread])
        new = np.array(embedding)
        updated = ((old + new) / 2).tolist()
        agent_threads[best_thread] = updated
        return best_thread
    else:
        # Create new thread
        thread_id = str(uuid.uuid4())[:8]
        if agent_id not in _thread_centroids:
            _thread_centroids[agent_id] = {}
        _thread_centroids[agent_id][thread_id] = embedding
        return thread_id


# ── Cross-Tier Search ─────────────────────────────────────────────────────────


async def cross_tier_search(
    agent_id: str,
    query: str,
    *,
    journal: JournalTier,
    ltm: LTMTier,
    shared: SharedTier,
    entity: EntityTier,
    tiers: tuple[str, ...] | None = None,
    top_k: int = 10,
    project_scope: str | None = ...,
) -> list[SearchResult]:
    """Search across multiple memory tiers in parallel.

    When project_scope is set, scoped entries get a 1.2x score boost.

    Args:
        agent_id: Agent identifier.
        query: Search query text.
        journal: Journal tier instance.
        ltm: LTM tier instance.
        shared: Shared tier instance.
        entity: Entity tier instance.
        tiers: Which tiers to search (default: all).
        top_k: Maximum total results.
        project_scope: Scope filter. Sentinel (...) = use tier config defaults.

    Returns:
        List of SearchResult objects, ranked by score.
    """
    tiers = tiers or DEFAULT_TIERS
    # Pass scope through as sentinel — each tier resolves its own config default
    scope_kwarg = {"project_scope": project_scope}
    tasks = []

    if "journal" in tiers:
        tasks.append(_search_journal(agent_id, query, journal, **scope_kwarg))
    if "ltm" in tiers:
        tasks.append(_search_ltm(agent_id, query, ltm, **scope_kwarg))
    if "shared" in tiers:
        tasks.append(_search_shared(query, shared, **scope_kwarg))
    if "entity" in tiers:
        tasks.append(_search_entity(query, entity, agent_id=agent_id, **scope_kwarg))

    all_results: list[SearchResult] = []
    for coro_result in await asyncio.gather(*tasks, return_exceptions=True):
        if isinstance(coro_result, Exception):
            logger.warning("Tier search failed: %s", coro_result)
            continue
        all_results.extend(coro_result)

    # Sort by score descending, take top_k
    all_results.sort(key=lambda r: r.score, reverse=True)
    return all_results[:top_k]


async def _search_journal(
    agent_id: str, query: str, tier: JournalTier,
    *, project_scope: str | None = ...,
) -> list[SearchResult]:
    entries = await tier.search(agent_id, query, top_k=5, project_scope=project_scope)
    return [
        SearchResult(
            tier="journal",
            id=e.id,
            score=e.relevance_score,
            content=e.content,
            title=e.title,
            agent_id=e.agent_id,
            metadata={"entry_type": e.entry_type, "record_type": e.record_type},
        )
        for e in entries
    ]


async def _search_ltm(
    agent_id: str, query: str, tier: LTMTier,
    *, project_scope: str | None = ...,
) -> list[SearchResult]:
    entries = await tier.search(agent_id, query, top_k=5, project_scope=project_scope)
    return [
        SearchResult(
            tier="ltm",
            id=e.id,
            score=(e.importance / 10.0) * e.salience,
            content=e.content,
            key=e.key,
            agent_id=e.agent_id,
            metadata={"category": e.category, "salience": e.salience},
        )
        for e in entries
    ]


async def _search_shared(
    query: str, tier: SharedTier,
    *, project_scope: str | None = ...,
) -> list[SearchResult]:
    entries = await tier.search(query, top_k=5, project_scope=project_scope)
    return [
        SearchResult(
            tier="shared",
            id=e.id,
            score=e.importance / 10.0,
            content=e.content,
            key=e.key,
            agent_id=e.last_updated_by,
            metadata={
                "category": e.category,
                "confirmed": e.confirmed,
                "created_by": e.created_by,
            },
        )
        for e in entries
    ]


async def _search_entity(
    query: str, tier: EntityTier,
    *, project_scope: str | None = ...,
    agent_id: str | None = None,
) -> list[SearchResult]:
    records = await tier.search(query, top_k=5, project_scope=project_scope, agent_id=agent_id)
    return [
        SearchResult(
            tier="entity",
            id=r.id,
            score=r.confidence,
            content=r.content,
            agent_id=r.agent_id,
            metadata={
                "entity_type": r.entity_type,
                "entity_id": r.entity_id,
                "entity_name": r.entity_name,
                "record_type": r.record_type,
            },
        )
        for r in records
    ]
