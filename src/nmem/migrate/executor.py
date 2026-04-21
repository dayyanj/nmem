"""Database executor abstraction — asyncpg pool or SQLAlchemy async engine.

Keeps the migration runner agnostic to which database driver is in use.
"""
from __future__ import annotations

import logging
from typing import Any, Protocol

log = logging.getLogger(__name__)


class Executor(Protocol):
    """Minimal interface the migration runner needs."""

    async def execute(self, sql: str) -> None: ...
    async def fetch(self, sql: str, *args: Any) -> list[dict]: ...
    async def fetchval(self, sql: str, *args: Any) -> Any: ...


class AsyncpgExecutor:
    """Executor backed by an asyncpg connection pool."""

    def __init__(self, pool: Any):
        self._pool = pool

    async def execute(self, sql: str) -> None:
        await self._pool.execute(sql)

    async def fetch(self, sql: str, *args: Any) -> list[dict]:
        rows = await self._pool.fetch(sql, *args)
        return [dict(r) for r in rows]

    async def fetchval(self, sql: str, *args: Any) -> Any:
        return await self._pool.fetchval(sql, *args)


class SQLAlchemyExecutor:
    """Executor backed by a SQLAlchemy async engine.

    Uses raw text() execution for DDL — no ORM involvement.
    Each call gets its own connection with autocommit for DDL safety.
    """

    def __init__(self, engine: Any):
        self._engine = engine

    async def execute(self, sql: str) -> None:
        from sqlalchemy import text
        async with self._engine.begin() as conn:
            await conn.execute(text(sql))

    async def fetch(self, sql: str, *args: Any) -> list[dict]:
        from sqlalchemy import text
        async with self._engine.connect() as conn:
            # SQLAlchemy doesn't use positional $1 params — convert if needed
            result = await conn.execute(text(sql))
            rows = result.mappings().all()
            return [dict(r) for r in rows]

    async def fetchval(self, sql: str, *args: Any) -> Any:
        rows = await self.fetch(sql, *args)
        if rows:
            return list(rows[0].values())[0]
        return None
