"""
Database engine and session management.

Handles both PostgreSQL (asyncpg) and SQLite (aiosqlite) backends.
Creates tables, indexes, and manages connection pooling.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from nmem.db.models import Base, HAS_PGVECTOR

logger = logging.getLogger(__name__)


class DatabaseManager:
    """Manages the async SQLAlchemy engine and session factory."""

    def __init__(self, database_url: str, echo: bool = False):
        self._url = database_url
        self._is_postgres = "postgresql" in database_url or "postgres" in database_url
        self._is_sqlite = "sqlite" in database_url

        engine_kwargs: dict = {"echo": echo}
        if self._is_postgres:
            engine_kwargs.update(pool_size=5, max_overflow=10)

        self._engine: AsyncEngine = create_async_engine(database_url, **engine_kwargs)
        self._session_factory = async_sessionmaker(
            self._engine, class_=AsyncSession, expire_on_commit=False
        )

    @property
    def engine(self) -> AsyncEngine:
        return self._engine

    @property
    def is_postgres(self) -> bool:
        return self._is_postgres

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        """Yield an async session with automatic commit/rollback."""
        async with self._session_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    async def initialize(self, embedding_dimensions: int = 384) -> None:
        """Create all tables and indexes. Idempotent."""
        # Ensure pgvector extension exists BEFORE creating tables (vector type needed)
        if self._is_postgres:
            async with self._engine.begin() as conn:
                try:
                    await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
                except Exception as e:
                    logger.warning("Could not create pgvector extension: %s", e)

        async with self._engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        if self._is_postgres:
            await self._create_postgres_indexes(embedding_dimensions)

        # Store metadata
        async with self.session() as session:
            from nmem.db.models import NmemMetadata

            # Check/set embedding dimensions
            result = await session.execute(
                text("SELECT value FROM nmem_metadata WHERE key = 'embedding_dimensions'")
            )
            row = result.scalar_one_or_none()
            if row is None:
                session.add(NmemMetadata(key="embedding_dimensions", value=str(embedding_dimensions)))
                session.add(NmemMetadata(key="schema_version", value="1"))
            else:
                stored_dim = int(row)
                if stored_dim != embedding_dimensions:
                    logger.warning(
                        "Embedding dimension mismatch: stored=%d, configured=%d",
                        stored_dim,
                        embedding_dimensions,
                    )

            # Run schema migrations
            await self._migrate_schema(session)

    async def _migrate_schema(self, session: AsyncSession) -> None:
        """Run incremental schema migrations based on schema_version."""
        result = await session.execute(
            text("SELECT value FROM nmem_metadata WHERE key = 'schema_version'")
        )
        version = int(result.scalar_one_or_none() or "1")

        if version < 2:
            # v2: Add project_scope column to all tier tables
            tables_needing_scope = [
                "nmem_working_memory",
                "nmem_journal_entries",
                "nmem_long_term_memory",
                "nmem_shared_knowledge",
                "nmem_entity_memory",
                "nmem_curiosity_signals",
                "nmem_delegations",
            ]
            for table in tables_needing_scope:
                try:
                    await session.execute(text(
                        f"ALTER TABLE {table} ADD COLUMN project_scope VARCHAR(300)"
                    ))
                    logger.info("Migration v2: added project_scope to %s", table)
                except Exception:
                    pass  # Column already exists (fresh install)

            # Update LTM unique index: (agent_id, key) → (agent_id, key, project_scope)
            if self._is_postgres:
                try:
                    await session.execute(text(
                        "DROP INDEX IF EXISTS ix_nmem_ltm_agent_key"
                    ))
                    await session.execute(text(
                        "CREATE UNIQUE INDEX IF NOT EXISTS ix_nmem_ltm_agent_key_scope "
                        "ON nmem_long_term_memory (agent_id, key, project_scope)"
                    ))
                except Exception as e:
                    logger.debug("LTM index migration: %s", e)

            # Update shared key unique index
            if self._is_postgres:
                try:
                    await session.execute(text(
                        "DROP INDEX IF EXISTS ix_nmem_shared_key_scope"
                    ))
                    await session.execute(text(
                        "CREATE UNIQUE INDEX IF NOT EXISTS ix_nmem_shared_key_scope "
                        "ON nmem_shared_knowledge (key, project_scope)"
                    ))
                except Exception as e:
                    logger.debug("Shared index migration: %s", e)

            await session.execute(text(
                "UPDATE nmem_metadata SET value = '2' WHERE key = 'schema_version'"
            ))
            logger.info("Schema migrated to version 2 (project_scope)")

    async def _create_postgres_indexes(self, dimensions: int) -> None:
        """Create PostgreSQL-specific HNSW and GIN indexes."""
        if not HAS_PGVECTOR:
            logger.warning("pgvector not installed — skipping vector indexes")
            return

        async with self._engine.begin() as conn:
            # HNSW indexes for vector similarity search
            vector_tables = [
                "nmem_journal_entries",
                "nmem_long_term_memory",
                "nmem_shared_knowledge",
                "nmem_entity_memory",
                "nmem_delegations",
                "nmem_scheduled_followups",
            ]
            for table in vector_tables:
                try:
                    await conn.execute(text(f"""
                        CREATE INDEX IF NOT EXISTS ix_{table}_embedding
                        ON {table} USING hnsw(embedding vector_cosine_ops)
                        WITH (m = 16, ef_construction = 64)
                    """))
                except Exception as e:
                    logger.debug("HNSW index for %s: %s", table, e)

            # GIN indexes for full-text search
            tsv_tables = [
                "nmem_journal_entries",
                "nmem_long_term_memory",
                "nmem_shared_knowledge",
                "nmem_delegations",
            ]
            for table in tsv_tables:
                try:
                    await conn.execute(text(f"""
                        CREATE INDEX IF NOT EXISTS ix_{table}_tsv
                        ON {table} USING gin(content_tsv)
                    """))
                except Exception as e:
                    logger.debug("GIN index for %s: %s", table, e)

    async def close(self) -> None:
        """Dispose of the engine connection pool."""
        await self._engine.dispose()
