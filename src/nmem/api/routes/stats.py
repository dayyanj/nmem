"""
Stats, health, and version endpoints.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import text

from nmem import MemorySystem
from nmem.api.deps import get_mem
from nmem.api.schemas import HealthResponse, StatsResponse, TierStats, VersionResponse

router = APIRouter(prefix="/v1", tags=["admin"])

_TIER_TABLES = {
    "working": "nmem_working_memory",
    "journal": "nmem_journal_entries",
    "ltm": "nmem_long_term_memory",
    "shared": "nmem_shared_knowledge",
    "entity": "nmem_entity_memory",
    "policy": "nmem_policy_memory",
    "delegations": "nmem_delegations",
    "curiosity": "nmem_curiosity_signals",
    "knowledge_links": "nmem_knowledge_links",
}


@router.get("/stats", response_model=StatsResponse)
async def get_stats(mem: MemorySystem = Depends(get_mem)) -> StatsResponse:
    """Get per-tier counts and system info."""
    tiers: list[TierStats] = []
    total = 0

    async with mem._db.session() as session:
        for label, table in _TIER_TABLES.items():
            try:
                result = await session.execute(text(f"SELECT COUNT(*) FROM {table}"))
                count = result.scalar() or 0
            except Exception:
                count = 0
            tiers.append(TierStats(tier=label, count=count))
            total += count

    config = mem._config
    database = "PostgreSQL" if mem._db.is_postgres else "SQLite"

    return StatsResponse(
        tiers=tiers,
        total_entries=total,
        database=database,
        embedding_provider=config.embedding.provider,
        embedding_model=config.embedding.model,
        llm_provider=config.llm.provider,
        project_scope=config.project_scope,
    )


@router.get("/health", response_model=HealthResponse)
async def health_check(mem: MemorySystem = Depends(get_mem)) -> HealthResponse:
    """Check API and database health."""
    database = "PostgreSQL" if mem._db.is_postgres else "SQLite"
    schema_version = None
    try:
        async with mem._db.session() as session:
            result = await session.execute(
                text("SELECT value FROM nmem_metadata WHERE key = 'schema_version'")
            )
            row = result.scalar_one_or_none()
            if row is not None:
                schema_version = int(row)
    except Exception:
        pass

    return HealthResponse(
        status="ok",
        database=database,
        schema_version=schema_version,
    )


@router.get("/version", response_model=VersionResponse)
async def version() -> VersionResponse:
    """Return nmem version info."""
    try:
        from importlib.metadata import version as pkg_version
        ver = pkg_version("nmem")
    except Exception:
        ver = "0.0.0-dev"
    return VersionResponse(version=ver, schema_version=2)
