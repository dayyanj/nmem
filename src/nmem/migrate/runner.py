"""Forward-only SQL migration runner for the nmem ecosystem.

Each project (nmem, nmem-sym, nmem-immunity) keeps a migrations/ directory
with numbered SQL files. The runner tracks applied migrations in a shared
schema_migrations table and applies pending ones in order.

Design:
  - Plain SQL files (not Python) — auditable, can be run manually with psql
  - Forward-only — no rollback support (add a compensating migration instead)
  - Idempotent — safe to call on every startup
  - Per-project tracking in one table — multiple projects can share a database
  - Checksum tracking — warns if an applied migration file has been modified
"""
from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from nmem.migrate.executor import AsyncpgExecutor, SQLAlchemyExecutor

log = logging.getLogger(__name__)


MIGRATIONS_TABLE_SQL = """\
CREATE TABLE IF NOT EXISTS schema_migrations (
    id         SERIAL PRIMARY KEY,
    project    VARCHAR(100) NOT NULL,
    version    INTEGER NOT NULL,
    name       VARCHAR(300) NOT NULL,
    checksum   VARCHAR(64) NOT NULL,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(project, version)
);
"""

# Pattern: 001_description.sql
_MIGRATION_RE = re.compile(r"^(\d{3,4})_.*\.sql$")


@dataclass
class AppliedMigration:
    """Record of a migration that has been applied."""
    version: int
    name: str
    checksum: str
    applied_at: datetime


class MigrationRunner:
    """Forward-only migration runner for the nmem ecosystem.

    Works with either asyncpg pools or SQLAlchemy async engines.

    Usage::

        runner = MigrationRunner(
            project="nmem-sym",
            migrations_dir=Path(__file__).parent / "migrations",
            pool=asyncpg_pool,       # OR engine=sqlalchemy_engine
        )
        applied = await runner.run()
        print(f"Applied {len(applied)} migrations")
    """

    def __init__(
        self,
        project: str,
        migrations_dir: Path,
        pool: Any | None = None,
        engine: Any | None = None,
    ):
        if pool is None and engine is None:
            raise ValueError("Provide either pool (asyncpg) or engine (SQLAlchemy)")

        self.project = project
        self.migrations_dir = Path(migrations_dir)

        if pool is not None:
            self._exec = AsyncpgExecutor(pool)
        else:
            self._exec = SQLAlchemyExecutor(engine)

    # ── Public API ─────────────────────────────────────────

    async def ensure_table(self) -> None:
        """Create schema_migrations table if it doesn't exist."""
        await self._exec.execute(MIGRATIONS_TABLE_SQL)

    async def applied(self) -> list[AppliedMigration]:
        """Return list of already-applied migrations for this project."""
        await self.ensure_table()
        # Use string interpolation — project is a controlled constant, not user input.
        # This keeps the executor agnostic to parameter styles ($1 vs :param).
        rows = await self._exec.fetch(
            "SELECT version, name, checksum, applied_at "
            "FROM schema_migrations "
            f"WHERE project = '{self.project}' "
            "ORDER BY version ASC",
        )
        return [
            AppliedMigration(
                version=r["version"],
                name=r["name"],
                checksum=r["checksum"],
                applied_at=r["applied_at"],
            )
            for r in rows
        ]

    def discover(self) -> list[tuple[int, Path]]:
        """Find all migration files in the migrations directory.

        Returns list of (version, path) sorted by version.
        """
        if not self.migrations_dir.is_dir():
            return []

        results: list[tuple[int, Path]] = []
        for path in sorted(self.migrations_dir.iterdir()):
            match = _MIGRATION_RE.match(path.name)
            if match:
                version = int(match.group(1))
                results.append((version, path))

        return sorted(results, key=lambda x: x[0])

    async def pending(self) -> list[tuple[int, Path]]:
        """Return migration files not yet applied, sorted by version."""
        applied_versions = {m.version for m in await self.applied()}
        return [
            (version, path)
            for version, path in self.discover()
            if version not in applied_versions
        ]

    async def run(self, dry_run: bool = False) -> list[str]:
        """Apply all pending migrations. Returns list of applied names.

        Each migration runs in its own execution. Skips already-applied.
        Records checksum for drift detection.
        """
        await self.ensure_table()

        # Check for checksum drift on applied migrations
        await self._check_drift()

        to_apply = await self.pending()
        if not to_apply:
            log.debug("migrate[%s]: no pending migrations", self.project)
            return []

        applied_names: list[str] = []

        for version, path in to_apply:
            if dry_run:
                log.info("migrate[%s]: would apply %s", self.project, path.name)
                applied_names.append(path.name)
                continue

            sql = path.read_text(encoding="utf-8")
            checksum = _sha256(sql)

            log.info("migrate[%s]: applying %s ...", self.project, path.name)
            try:
                await self._exec.execute(sql)
            except Exception as e:
                log.error(
                    "migrate[%s]: FAILED on %s: %s",
                    self.project, path.name, e,
                )
                raise MigrationError(
                    f"Migration {path.name} failed for project {self.project}: {e}"
                ) from e

            # Record as applied
            await self._exec.execute(
                "INSERT INTO schema_migrations (project, version, name, checksum) "
                f"VALUES ('{self.project}', {version}, '{path.name}', '{checksum}')"
            )
            applied_names.append(path.name)
            log.info("migrate[%s]: applied %s", self.project, path.name)

        if applied_names:
            log.info(
                "migrate[%s]: %d migration(s) applied: %s",
                self.project, len(applied_names), ", ".join(applied_names),
            )

        return applied_names

    async def mark_applied(self, version: int, name: str, sql_content: str) -> None:
        """Mark a migration as applied without running it.

        Used by bootstrap logic for existing installations where the
        schema already exists but was applied before the migration system.
        """
        await self.ensure_table()
        checksum = _sha256(sql_content)
        await self._exec.execute(
            "INSERT INTO schema_migrations (project, version, name, checksum) "
            f"VALUES ('{self.project}', {version}, '{name}', '{checksum}') "
            "ON CONFLICT (project, version) DO NOTHING"
        )
        log.info("migrate[%s]: marked %s as already applied", self.project, name)

    async def status(self) -> dict:
        """Return migration status for display."""
        applied_list = await self.applied()
        all_files = self.discover()
        pending_list = await self.pending()

        return {
            "project": self.project,
            "applied": len(applied_list),
            "pending": len(pending_list),
            "total": len(all_files),
            "applied_list": [m.name for m in applied_list],
            "pending_list": [p.name for _, p in pending_list],
        }

    # ── Internal ──────────────────────────────────────────

    async def _check_drift(self) -> None:
        """Warn if any applied migration file has been modified since application."""
        applied_list = await self.applied()
        all_files = {v: p for v, p in self.discover()}

        for migration in applied_list:
            path = all_files.get(migration.version)
            if path is None:
                continue
            current_checksum = _sha256(path.read_text(encoding="utf-8"))
            if current_checksum != migration.checksum:
                log.warning(
                    "migrate[%s]: DRIFT detected on %s — file has been modified "
                    "since it was applied (applied checksum: %s, current: %s)",
                    self.project, migration.name,
                    migration.checksum[:12], current_checksum[:12],
                )


class MigrationError(Exception):
    """Raised when a migration fails to apply."""


def _sha256(content: str) -> str:
    """Compute SHA-256 hex digest of a string."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()
