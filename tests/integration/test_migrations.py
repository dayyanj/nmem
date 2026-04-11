"""
Schema migration tests.

These tests seed a database at an older schema version and verify that
`_migrate_schema()` upgrades it correctly. The tests are Postgres-only:
SQLite's `ALTER TABLE` support is too limited to exercise the migration
paths we care about in production.

Pattern: each test creates a MemorySystem, downgrades the schema state
(drops columns or resets the schema_version metadata row), then calls
`initialize()` again to trigger the migration. Post-migration assertions
run against `information_schema.columns`.
"""

import asyncio
import os

import pytest
from sqlalchemy import text

from nmem import MemorySystem, NmemConfig
from nmem.db.session import CURRENT_SCHEMA_VERSION

NMEM_TEST_DSN = os.environ.get(
    "NMEM_TEST_DSN", "postgresql+asyncpg://nmem:nmem@localhost:5433/nmem"
)


def _can_connect() -> bool:
    try:
        import asyncpg
    except ImportError:
        return False

    async def check():
        try:
            conn = await asyncpg.connect("postgresql://nmem:nmem@localhost:5433/nmem")
            await conn.close()
            return True
        except Exception:
            return False

    try:
        return asyncio.run(check())
    except Exception:
        return False


_pg_available = _can_connect()

pytestmark = pytest.mark.skipif(not _pg_available, reason="PostgreSQL not available")


async def _drop_all_nmem_tables(system: MemorySystem) -> None:
    """Fully wipe the nmem schema so migration tests start clean."""
    async with system._db._engine.begin() as conn:
        result = await conn.execute(
            text(
                "SELECT tablename FROM pg_tables "
                "WHERE schemaname = 'public' AND tablename LIKE 'nmem_%'"
            )
        )
        tables = [row[0] for row in result.fetchall()]
        for table in tables:
            await conn.execute(text(f'DROP TABLE IF EXISTS "{table}" CASCADE'))


async def _column_exists(system: MemorySystem, table: str, column: str) -> bool:
    async with system._db._engine.begin() as conn:
        result = await conn.execute(
            text(
                "SELECT 1 FROM information_schema.columns "
                "WHERE table_name = :t AND column_name = :c"
            ),
            {"t": table, "c": column},
        )
        return result.scalar() is not None


async def _schema_version(system: MemorySystem) -> int:
    async with system._db.session() as session:
        result = await session.execute(
            text("SELECT value FROM nmem_metadata WHERE key = 'schema_version'")
        )
        return int(result.scalar_one_or_none() or "1")


@pytest.fixture
async def fresh_system():
    """Provide a MemorySystem whose schema has been fully dropped first."""
    config = NmemConfig(
        database_url=NMEM_TEST_DSN,
        embedding={"provider": "noop", "dimensions": 384},
        llm={"provider": "noop"},
        consolidation={"enabled": False},
    )
    system = MemorySystem(config)
    # Initialize once so the engine + tables exist, then drop everything
    # and re-initialize to exercise the fresh-install path.
    await system.initialize()
    await _drop_all_nmem_tables(system)
    yield system
    await _drop_all_nmem_tables(system)
    await system.close()


class TestFreshInstall:
    """Fresh-install path: schema should land at CURRENT_SCHEMA_VERSION."""

    async def test_fresh_install_is_at_current_version(self, fresh_system: MemorySystem):
        await fresh_system.initialize()
        assert await _schema_version(fresh_system) == CURRENT_SCHEMA_VERSION

    async def test_reinitialize_is_idempotent(self, fresh_system: MemorySystem):
        await fresh_system.initialize()
        await fresh_system.initialize()
        assert await _schema_version(fresh_system) == CURRENT_SCHEMA_VERSION


class TestV1toV2Migration:
    """Exercise the v1 → v2 migration (project_scope column additions)."""

    async def test_v1_to_v2_adds_project_scope(self, fresh_system: MemorySystem):
        await fresh_system.initialize()

        # Simulate v1 state: drop project_scope columns, reset metadata
        async with fresh_system._db._engine.begin() as conn:
            for table in (
                "nmem_working_memory",
                "nmem_journal_entries",
                "nmem_long_term_memory",
                "nmem_shared_knowledge",
                "nmem_entity_memory",
                "nmem_curiosity_signals",
                "nmem_delegations",
            ):
                await conn.execute(
                    text(f"ALTER TABLE {table} DROP COLUMN IF EXISTS project_scope")
                )
        async with fresh_system._db.session() as session:
            await session.execute(
                text("UPDATE nmem_metadata SET value = '1' WHERE key = 'schema_version'")
            )

        # Run migration
        await fresh_system._db._migrate_schema()

        # Verify columns are back
        for table in (
            "nmem_journal_entries",
            "nmem_long_term_memory",
            "nmem_shared_knowledge",
            "nmem_entity_memory",
        ):
            assert await _column_exists(fresh_system, table, "project_scope"), (
                f"project_scope not restored on {table}"
            )

        assert await _schema_version(fresh_system) == CURRENT_SCHEMA_VERSION


class TestV2toV3Migration:
    """Exercise the v2 → v3 migration (confidence → salience, supersedes_id → superseded_by_id)."""

    async def test_v2_to_v3_renames_ltm_columns(self, fresh_system: MemorySystem):
        await fresh_system.initialize()

        # Simulate v2 state: rename the v3 columns back to their v2 names and
        # mark schema_version as 2 so the v3 migration will actually fire.
        async with fresh_system._db._engine.begin() as conn:
            await conn.execute(
                text(
                    "ALTER TABLE nmem_long_term_memory "
                    "RENAME COLUMN salience TO confidence"
                )
            )
            await conn.execute(
                text(
                    "ALTER TABLE nmem_long_term_memory "
                    "RENAME COLUMN superseded_by_id TO supersedes_id"
                )
            )
        async with fresh_system._db.session() as session:
            await session.execute(
                text("UPDATE nmem_metadata SET value = '2' WHERE key = 'schema_version'")
            )

        # Pre-conditions: v2 column names present
        assert await _column_exists(fresh_system, "nmem_long_term_memory", "confidence")
        assert await _column_exists(fresh_system, "nmem_long_term_memory", "supersedes_id")
        assert not await _column_exists(
            fresh_system, "nmem_long_term_memory", "salience"
        )

        # Run the migration
        await fresh_system._db._migrate_schema()

        # Post-conditions: v3 column names present, v2 names gone
        assert await _column_exists(fresh_system, "nmem_long_term_memory", "salience")
        assert await _column_exists(
            fresh_system, "nmem_long_term_memory", "superseded_by_id"
        )
        assert not await _column_exists(
            fresh_system, "nmem_long_term_memory", "confidence"
        )
        assert not await _column_exists(
            fresh_system, "nmem_long_term_memory", "supersedes_id"
        )

        # Entity memory confidence is untouched
        assert await _column_exists(fresh_system, "nmem_entity_memory", "confidence")

        assert await _schema_version(fresh_system) == CURRENT_SCHEMA_VERSION


class TestEntityConfidencePreserved:
    """Phase 1 guard — entity memory's `confidence` column must never get
    dragged into the LTM salience rename."""

    async def test_entity_confidence_column_exists(self, fresh_system: MemorySystem):
        await fresh_system.initialize()
        assert await _column_exists(fresh_system, "nmem_entity_memory", "confidence")

    async def test_entity_confidence_field_on_dataclass(self, fresh_system: MemorySystem):
        from nmem.types import EntityRecord

        fields = {f for f in EntityRecord.__dataclass_fields__}
        assert "confidence" in fields, (
            "EntityRecord.confidence must survive the Phase 1 rename — "
            "it represents grounding certainty, not staleness."
        )
