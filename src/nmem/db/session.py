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

# Current schema version. Bump when adding migrations to _migrate_schema.
CURRENT_SCHEMA_VERSION = 3


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

        # Store metadata (fresh installs get the current schema version directly
        # so the migration only runs when upgrading an existing deployment)
        async with self.session() as session:
            from nmem.db.models import NmemMetadata

            # Check/set embedding dimensions and schema version
            result = await session.execute(
                text("SELECT value FROM nmem_metadata WHERE key = 'embedding_dimensions'")
            )
            row = result.scalar_one_or_none()
            is_fresh_install = row is None
            if is_fresh_install:
                session.add(NmemMetadata(key="embedding_dimensions", value=str(embedding_dimensions)))
                session.add(NmemMetadata(key="schema_version", value=str(CURRENT_SCHEMA_VERSION)))
                await session.flush()
            else:
                stored_dim = int(row)
                if stored_dim != embedding_dimensions:
                    logger.warning(
                        "Embedding dimension mismatch: stored=%d, configured=%d",
                        stored_dim,
                        embedding_dimensions,
                    )

        # Run schema migrations in a separate session to isolate DDL failures
        # from the main metadata session. Skip on fresh installs since tables
        # were already created with the latest schema.
        if not is_fresh_install:
            await self._migrate_schema()

    async def _migrate_schema(self) -> None:
        """Run incremental schema migrations based on schema_version.

        Each DDL runs in its own autocommit connection so a failure on one
        statement (e.g. duplicate column, duplicate index) does not abort
        the transaction and poison subsequent statements.
        """
        async with self.session() as session:
            result = await session.execute(
                text("SELECT value FROM nmem_metadata WHERE key = 'schema_version'")
            )
            version = int(result.scalar_one_or_none() or "1")

        if version >= CURRENT_SCHEMA_VERSION:
            return

        async def _run(sql: str, label: str) -> None:
            """Run a DDL statement in its own isolated connection.

            Failures are logged at WARNING (not DEBUG) so re-runs against an
            already-migrated schema are visible. Idempotent errors ("column
            already exists", "column does not exist") show up as warnings and
            can be safely ignored by operators — but real bugs (permission
            denied, syntax error, mismatched types) are no longer silent.
            """
            try:
                async with self._engine.begin() as conn:
                    await conn.execute(text(sql))
                logger.info("Migration: %s", label)
            except Exception as e:
                logger.warning(
                    "Migration skipped (%s): %s\n  SQL: %s",
                    label, e, sql,
                )

        if version < 2:
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
                await _run(
                    f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS project_scope VARCHAR(300)",
                    f"v2: add project_scope to {table}",
                )

            if self._is_postgres:
                await _run(
                    "DROP INDEX IF EXISTS ix_nmem_ltm_agent_key",
                    "v2: drop old LTM unique index",
                )
                await _run(
                    "CREATE UNIQUE INDEX IF NOT EXISTS ix_nmem_ltm_agent_key_scope "
                    "ON nmem_long_term_memory (agent_id, key, project_scope)",
                    "v2: create LTM (agent_id, key, project_scope) index",
                )
                await _run(
                    "DROP INDEX IF EXISTS ix_nmem_shared_key",
                    "v2: drop old shared unique index",
                )
                await _run(
                    "CREATE UNIQUE INDEX IF NOT EXISTS ix_nmem_shared_key_scope "
                    "ON nmem_shared_knowledge (key, project_scope)",
                    "v2: create shared (key, project_scope) index",
                )

        if version < 3:
            # Rename `confidence` → `salience` on LTM. Entity memory's
            # `confidence` is untouched (it means grounding certainty there).
            await _run(
                "ALTER TABLE nmem_long_term_memory "
                "RENAME COLUMN confidence TO salience",
                "v3: rename LTM.confidence to salience",
            )
            # Rename `supersedes_id` → `superseded_by_id` on LTM. The column
            # has always been written as a forward pointer in practice; the
            # rename aligns the name with the semantics.
            await _run(
                "ALTER TABLE nmem_long_term_memory "
                "RENAME COLUMN supersedes_id TO superseded_by_id",
                "v3: rename LTM.supersedes_id to superseded_by_id",
            )

        # Bump schema version in its own session
        async with self.session() as session:
            await session.execute(text(
                "UPDATE nmem_metadata SET value = :v WHERE key = 'schema_version'"
            ), {"v": str(CURRENT_SCHEMA_VERSION)})
        logger.info("Schema migrated to version %d", CURRENT_SCHEMA_VERSION)

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
