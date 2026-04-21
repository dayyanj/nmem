"""Lightweight database migration runner for the nmem ecosystem.

Plain SQL files in numbered directories, tracked in a schema_migrations table.
Works with asyncpg pools (nmem-sym) and SQLAlchemy async engines (nmem).

Usage::

    from nmem.migrate import MigrationRunner

    runner = MigrationRunner(
        project="nmem-sym",
        migrations_dir=Path(__file__).parent / "migrations",
        pool=asyncpg_pool,
    )
    await runner.run()
"""
from nmem.migrate.runner import MigrationRunner, AppliedMigration

__all__ = ["MigrationRunner", "AppliedMigration"]
