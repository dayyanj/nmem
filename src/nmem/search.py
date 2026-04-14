"""
Cross-tier unified search engine.

Searches across all memory tiers in parallel, merges and ranks results.
Implements the hybrid search algorithm (60/40 vector/FTS weighting).
"""

from __future__ import annotations

import asyncio
import logging
import re
import uuid
from typing import TYPE_CHECKING, Any

import numpy as np
from sqlalchemy import text as sa_text

from nmem.types import SearchResult

# Passage extraction: only extract for entries longer than this
PASSAGE_MIN_CONTENT_LENGTH = 800
# Target passage size in chars
PASSAGE_TARGET_CHARS = 500

if TYPE_CHECKING:
    from nmem.config import NmemConfig, RecognitionConfig
    from nmem.db.session import DatabaseManager
    from nmem.links import KnowledgeLinkEngine
    from nmem.tiers.journal import JournalTier
    from nmem.tiers.ltm import LTMTier
    from nmem.tiers.shared import SharedTier
    from nmem.tiers.entity import EntityTier
    from nmem.tiers.policy import PolicyTier

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
    min_vector_score: float = 0.0,
    recency_weight: float = 0.0,
    recency_halflife_days: int = 30,
) -> list[tuple[int, float]]:
    """Hybrid search (vector + FTS) returning ranked (id, score) pairs.

    Uses a CTE pattern: vector similarity as the primary ranking signal,
    FTS (BM25) as an additive boost when keywords match.

    The FTS boost is capped (default 0.1) to prevent keyword matches from
    overwhelming semantic relevance.  This avoids the pool-max normalization
    bug where FTS scores are divided by the candidate-pool maximum, which
    crushes entries with high vector similarity but low FTS match.

    When recency_weight > 0, a temporal recency factor is added to
    the scoring formula.  The recency signal decays exponentially with
    a configurable half-life (default 30 days).

    Falls back gracefully when content_tsv is NULL (FTS boost = 0).

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
        min_vector_score: Minimum vector similarity to include in candidate
            pool (0.0 = no filter, 0.3 = recommended for large corpora).
        recency_weight: Weight for recency boost (0.0 = disabled).
        recency_halflife_days: Half-life for recency decay.

    Returns:
        List of (id, combined_score) tuples, ranked by score.
    """
    # SQLite fallback: no pgvector operators, compute similarity in Python
    if not db.is_postgres:
        return await _sqlite_fallback_search(
            db, table, query_embedding, where_clause, params, top_k,
            min_vector_score=min_vector_score,
        )

    embedding_str = f"[{','.join(str(x) for x in query_embedding)}]"
    params.update({
        "embedding_vec": embedding_str,
        "query_text": query_text,
        "candidate_limit": top_k * 3,
        "top_k": top_k,
    })

    # Build optional minimum-score filter for the vector candidate stage.
    min_vec_filter = ""
    if min_vector_score > 0:
        min_vec_filter = f"AND 1 - (embedding <=> CAST(:embedding_vec AS vector)) > :min_vec_score"
        params["min_vec_score"] = min_vector_score

    # When recency is enabled, set up decay parameters.
    if recency_weight > 0:
        halflife_seconds = recency_halflife_days * 86400
        params["recency_halflife_secs"] = halflife_seconds

    # Build recency CTE and scoring fragment
    recency_cte = ""
    recency_join = ""
    recency_term = ""
    if recency_weight > 0:
        recency_cte = f""",
        recency_scores AS (
            SELECT vs.id,
                   1.0 / (1.0 + EXTRACT(EPOCH FROM (NOW() - t.created_at)) / :recency_halflife_secs) AS recency
            FROM vector_scores vs
            JOIN {table} t ON vs.id = t.id
        )"""
        recency_join = "LEFT JOIN recency_scores rs ON vs.id = rs.id"
        recency_term = f"+ {recency_weight} * COALESCE(rs.recency, 0)"

    # FTS boost cap: keyword matches add at most this much to the vector score.
    # Keeps FTS from overwhelming semantic relevance while still rewarding
    # exact keyword hits.  ts_rank_cd typically returns 0.0-0.3.
    # Scale cap by fts_weight so callers can disable FTS with fts_weight=0.
    fts_boost_cap = 0.1 * (fts_weight / 0.4) if fts_weight > 0 else 0

    sql = sa_text(f"""
        WITH vector_scores AS (
            SELECT id,
                   1 - (embedding <=> CAST(:embedding_vec AS vector)) AS vec_score
            FROM {table}
            WHERE {where_clause}
              AND embedding IS NOT NULL
              {min_vec_filter}
            ORDER BY embedding <=> CAST(:embedding_vec AS vector)
            LIMIT :candidate_limit
        ),
        fts_boost AS (
            SELECT vs.id,
                   CASE WHEN t.content_tsv @@ plainto_tsquery('english', :query_text)
                        THEN LEAST(
                            ts_rank_cd(t.content_tsv, plainto_tsquery('english', :query_text)),
                            {fts_boost_cap}
                        )
                        ELSE 0 END AS boost
            FROM vector_scores vs
            JOIN {table} t ON vs.id = t.id
        ){recency_cte}
        SELECT vs.id,
               (vs.vec_score + COALESCE(fb.boost, 0)
                {recency_term}) AS combined_score
        FROM vector_scores vs
        LEFT JOIN fts_boost fb ON vs.id = fb.id
        {recency_join}
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
    min_vector_score: float = 0.0,
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

    # Filter by minimum score and sort by similarity descending
    if min_vector_score > 0:
        scored = [(id, sim) for id, sim in scored if sim >= min_vector_score]
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


def compute_recognition(
    metadata: dict[str, Any],
    config: RecognitionConfig,
) -> tuple[str, float, list[str]]:
    """Compute recognition level from available entry metadata.

    Returns (level, score, reasons) where level is KNOWN/FAMILIAR/UNCERTAIN.
    Missing metadata keys are silently ignored (graceful degradation).
    """
    score = 0.0
    reasons: list[str] = []

    # Grounding (all tiers)
    grounding = metadata.get("grounding")
    if grounding:
        weight = config.grounding_weights.get(grounding, 0.0)
        if weight != 0:
            score += weight
            reasons.append(f"{grounding} ({weight:+.2f})")

    # Access count (journal, ltm)
    access_count = metadata.get("access_count", 0)
    if access_count >= config.access_count_high:
        score += 0.2
        reasons.append(f"accessed {access_count}x (+0.20)")
    elif access_count >= config.access_count_medium:
        score += 0.1
        reasons.append(f"accessed {access_count}x (+0.10)")

    # Recency via last_accessed_at (ltm)
    last_accessed = metadata.get("last_accessed_at")
    if last_accessed:
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        if hasattr(last_accessed, 'tzinfo') and last_accessed.tzinfo is None:
            # Naive datetime — assume UTC
            from datetime import timezone as tz
            last_accessed = last_accessed.replace(tzinfo=tz.utc)
        days_old = (now - last_accessed).days
        if days_old <= config.recency_high_days:
            score += 0.15
            reasons.append(f"{days_old}d ago (+0.15)")
        elif days_old <= config.recency_medium_days:
            score += 0.05
            reasons.append(f"{days_old}d ago (+0.05)")

    # Multi-agent confirmation (ltm)
    agents = metadata.get("accessed_by_agents") or []
    if len(agents) >= 2:
        score += config.multi_agent_bonus
        reasons.append(f"{len(agents)} agents (+{config.multi_agent_bonus:.2f})")

    # Salience (ltm)
    salience = metadata.get("salience")
    if salience is not None:
        bonus = salience * config.salience_weight
        score += bonus
        if bonus >= 0.05:
            reasons.append(f"salience={salience:.1f} (+{bonus:.2f})")

    # Confidence (entity)
    confidence = metadata.get("confidence")
    if confidence is not None:
        bonus = confidence * config.confidence_weight
        score += bonus
        reasons.append(f"confidence={confidence:.1f} (+{bonus:.2f})")

    # Confirmed flag (shared)
    if metadata.get("confirmed"):
        score += 0.3
        reasons.append("confirmed (+0.30)")

    # Clamp
    score = max(0.0, min(1.0, score))

    # Classify
    if score >= config.known_threshold:
        level = "KNOWN"
    elif score >= config.familiar_threshold:
        level = "FAMILIAR"
    else:
        level = "UNCERTAIN"

    return level, score, reasons


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


# ── Passage Extraction ────────────────────────────────────────────────────────


def _split_into_passages(content: str) -> list[str]:
    """Split content into semantic passages for extraction.

    Uses a hierarchy of boundaries:
    1. Markdown headers (## / ###)
    2. Bold sub-headers (**Label:**)
    3. Double newlines (paragraph breaks)

    Adjacent short passages are merged to reach target size.
    """
    # First split on headers and bold sub-headers
    parts = re.split(
        r"(?=^#{1,3}\s+|\n\*\*[^*]+\*\*[:\s—–-])",
        content,
        flags=re.MULTILINE,
    )

    # If no structural splits found, fall back to paragraph breaks
    if len(parts) <= 1:
        parts = re.split(r"\n\s*\n", content)

    # Merge very small passages with neighbors
    merged: list[str] = []
    current = ""
    for part in parts:
        part = part.strip()
        if not part:
            continue
        if len(current) + len(part) < PASSAGE_TARGET_CHARS:
            current = f"{current}\n\n{part}".strip() if current else part
        else:
            if current:
                merged.append(current)
            current = part
    if current:
        merged.append(current)

    return merged if merged else [content]


def extract_passage(
    content: str,
    query_embedding: np.ndarray,
    embedder: Any,
) -> str | None:
    """Extract the most relevant passage from long content.

    Uses the embedding model to find which passage within the content
    best matches the query. Like Google's featured snippet extraction.

    Args:
        content: Full entry content.
        query_embedding: Pre-computed query embedding vector.
        embedder: Embedding provider with encode() method.

    Returns:
        Best matching passage, or None if content is short enough to use as-is.
    """
    if len(content) < PASSAGE_MIN_CONTENT_LENGTH:
        return None

    passages = _split_into_passages(content)
    if len(passages) <= 1:
        return None

    # Batch-embed all passages
    try:
        passage_embeddings = embedder.embed_batch(passages)
    except AttributeError:
        # Single embed fallback
        passage_embeddings = [embedder.embed(p) for p in passages]

    if not passage_embeddings:
        return None

    # Cosine similarity of each passage against query
    passage_embs = np.array(passage_embeddings)
    # Normalize
    norms = np.linalg.norm(passage_embs, axis=1, keepdims=True)
    norms[norms == 0] = 1
    passage_embs = passage_embs / norms

    q_norm = np.linalg.norm(query_embedding)
    if q_norm > 0:
        q_normalized = query_embedding / q_norm
    else:
        return None

    similarities = passage_embs @ q_normalized
    best_idx = int(np.argmax(similarities))

    return passages[best_idx]


async def extract_passages_for_results(
    results: list[SearchResult],
    query: str,
    embedder: Any,
) -> list[SearchResult]:
    """Add passage extraction to search results with long content.

    Runs synchronously since embedding is CPU-bound and typically fast
    for small batches (5-10 passages per result).
    """
    if not embedder or not results:
        return results

    try:
        query_embedding = np.array(embedder.embed(query))
    except Exception:
        return results

    for result in results:
        if len(result.content) >= PASSAGE_MIN_CONTENT_LENGTH:
            try:
                passage = extract_passage(result.content, query_embedding, embedder)
                if passage:
                    result.passage = passage
            except Exception as e:
                logger.debug("Passage extraction failed for %s: %s", result.key or result.id, e)

    return results


# ── Cross-Tier Search ─────────────────────────────────────────────────────────


async def cross_tier_search(
    agent_id: str,
    query: str,
    *,
    journal: JournalTier,
    ltm: LTMTier,
    shared: SharedTier,
    entity: EntityTier,
    policy: PolicyTier | None = None,
    link_engine: KnowledgeLinkEngine | None = None,
    config: NmemConfig | None = None,
    tiers: tuple[str, ...] | None = None,
    top_k: int = 10,
    project_scope: str | None = ...,
    bump_access: bool = True,
    embedder: Any = None,
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
        policy: Policy tier instance (optional — pass to enable ``"policy"`` tier search).
        link_engine: Knowledge link engine for search expansion.
        config: nmem config (for search expansion settings).
        tiers: Which tiers to search (default: journal, ltm, shared, entity).
        top_k: Maximum total results.
        project_scope: Scope filter. Sentinel (...) = use tier config defaults.
        bump_access: Whether to increment access counters on matched entries.

    Returns:
        List of SearchResult objects, ranked by score.
    """
    tiers = tiers or DEFAULT_TIERS
    recog_config = config.recognition if config else None
    # Pass scope through as sentinel — each tier resolves its own config default
    scope_kwarg = {"project_scope": project_scope}
    tasks = []

    if "journal" in tiers:
        tasks.append(_search_journal(agent_id, query, journal, recognition_config=recog_config, bump_access=bump_access, **scope_kwarg))
    if "ltm" in tiers:
        tasks.append(_search_ltm(agent_id, query, ltm, recognition_config=recog_config, bump_access=bump_access, **scope_kwarg))
    if "shared" in tiers:
        tasks.append(_search_shared(query, shared, recognition_config=recog_config, **scope_kwarg))
    if "entity" in tiers:
        tasks.append(_search_entity(query, entity, agent_id=agent_id, recognition_config=recog_config, **scope_kwarg))
    if "policy" in tiers and policy is not None:
        tasks.append(_search_policy(query, policy))

    all_results: list[SearchResult] = []
    for coro_result in await asyncio.gather(*tasks, return_exceptions=True):
        if isinstance(coro_result, Exception):
            logger.warning("Tier search failed: %s", coro_result)
            continue
        all_results.extend(coro_result)

    # Sort by score descending, take top_k
    all_results.sort(key=lambda r: r.score, reverse=True)
    top_results = all_results[:top_k]

    # Expand via knowledge links (surfaces related entries not in the direct results)
    if link_engine and config and config.knowledge_links.search_expansion_enabled:
        try:
            top_results = await link_engine.expand_search_results(
                top_results,
                max_expansion=config.knowledge_links.search_expansion_max,
                min_strength=config.knowledge_links.search_expansion_min_strength,
            )
            top_results.sort(key=lambda r: r.score, reverse=True)
            top_results = top_results[:top_k]
        except Exception as e:
            logger.warning("Link expansion failed (non-fatal): %s", e)

    # Extract best-matching passages from long entries
    if embedder:
        top_results = await extract_passages_for_results(top_results, query, embedder)

    return top_results


async def _search_journal(
    agent_id: str, query: str, tier: JournalTier,
    *, project_scope: str | None = ...,
    recognition_config: RecognitionConfig | None = None,
    bump_access: bool = True,
) -> list[SearchResult]:
    entries = await tier.search(agent_id, query, top_k=5, project_scope=project_scope, bump_access=bump_access)
    results: list[SearchResult] = []
    for e in entries:
        meta: dict[str, Any] = {
            "entry_type": e.entry_type,
            "record_type": e.record_type,
            "grounding": e.grounding,
            "access_count": e.access_count,
            "importance": e.importance,
        }
        if recognition_config:
            level, r_score, reasons = compute_recognition(meta, recognition_config)
        else:
            level, r_score, reasons = "UNCERTAIN", 0.0, []
        results.append(SearchResult(
            tier="journal",
            id=e.id,
            score=e.relevance_score,
            content=e.content,
            title=e.title,
            agent_id=e.agent_id,
            metadata=meta,
            recognition=level,
            recognition_score=r_score,
            recognition_reasons=tuple(reasons),
        ))
    return results


async def _search_ltm(
    agent_id: str, query: str, tier: LTMTier,
    *, project_scope: str | None = ...,
    recognition_config: RecognitionConfig | None = None,
    bump_access: bool = True,
) -> list[SearchResult]:
    ranked_entries = await tier.search(agent_id, query, top_k=5, project_scope=project_scope, bump_access=bump_access)
    results: list[SearchResult] = []
    for e, relevance in ranked_entries:
        meta: dict[str, Any] = {
            "category": e.category,
            "salience": e.salience,
            "importance": e.importance,
            "grounding": e.grounding,
            "access_count": e.access_count,
            "record_type": e.record_type,
        }
        if recognition_config:
            level, r_score, reasons = compute_recognition(meta, recognition_config)
        else:
            level, r_score, reasons = "UNCERTAIN", 0.0, []
        results.append(SearchResult(
            tier="ltm",
            id=e.id,
            # Use relevance from hybrid search directly.
            # Salience and importance are lifecycle signals (consolidation/expiry),
            # NOT retrieval signals — they belong in priorities(), not search().
            score=relevance,
            content=e.content,
            key=e.key,
            agent_id=e.agent_id,
            metadata=meta,
            recognition=level,
            recognition_score=r_score,
            recognition_reasons=tuple(reasons),
        ))
    return results


async def _search_shared(
    query: str, tier: SharedTier,
    *, project_scope: str | None = ...,
    recognition_config: RecognitionConfig | None = None,
) -> list[SearchResult]:
    ranked_entries = await tier.search(query, top_k=5, project_scope=project_scope)
    results: list[SearchResult] = []
    for e, relevance in ranked_entries:
        meta: dict[str, Any] = {
            "category": e.category,
            "confirmed": e.confirmed,
            "created_by": e.created_by,
            "importance": e.importance,
            "grounding": e.grounding,
            "record_type": e.record_type,
        }
        if recognition_config:
            level, r_score, reasons = compute_recognition(meta, recognition_config)
        else:
            level, r_score, reasons = "UNCERTAIN", 0.0, []
        results.append(SearchResult(
            tier="shared",
            id=e.id,
            score=relevance,
            content=e.content,
            key=e.key,
            agent_id=e.last_updated_by,
            metadata=meta,
            recognition=level,
            recognition_score=r_score,
            recognition_reasons=tuple(reasons),
        ))
    return results


async def _search_entity(
    query: str, tier: EntityTier,
    *, project_scope: str | None = ...,
    agent_id: str | None = None,
    recognition_config: RecognitionConfig | None = None,
) -> list[SearchResult]:
    ranked_records = await tier.search(query, top_k=5, project_scope=project_scope, agent_id=agent_id)
    results: list[SearchResult] = []
    for r, relevance in ranked_records:
        meta: dict[str, Any] = {
            "entity_type": r.entity_type,
            "entity_id": r.entity_id,
            "entity_name": r.entity_name,
            "record_type": r.record_type,
            "confidence": r.confidence,
            "grounding": r.grounding,
        }
        if recognition_config:
            level, r_score, reasons = compute_recognition(meta, recognition_config)
        else:
            level, r_score, reasons = "UNCERTAIN", 0.0, []
        results.append(SearchResult(
            tier="entity",
            id=r.id,
            # Use relevance from hybrid search directly.
            # Confidence is exposed in metadata for recognition signals.
            score=relevance,
            content=r.content,
            agent_id=r.agent_id,
            metadata=meta,
            recognition=level,
            recognition_score=r_score,
            recognition_reasons=tuple(reasons),
        ))
    return results


async def _search_policy(
    query: str, tier: PolicyTier,
) -> list[SearchResult]:
    raw_results = await tier.search(query, top_k=5)
    return [
        SearchResult(
            tier="policy",
            id=entry.id,
            score=score,
            content=entry.content,
            key=entry.key,
            agent_id=entry.created_by,
            metadata={
                "scope": entry.scope,
                "category": entry.category,
                "version": entry.version,
                "status": entry.status,
            },
            recognition="KNOWN",
            recognition_score=1.0,
            recognition_reasons=("active policy",),
        )
        for entry, score in raw_results
    ]
