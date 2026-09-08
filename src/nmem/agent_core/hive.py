"""nmem.agent_core.hive — shared-world config + keeper election (Path B, B-ii/B-iii).

Two small, schema-INDEPENDENT pieces of the hive (see docs/path-b-hive-scoping-design.md §12.5):

  * ``HiveConfig`` — how an agent joins a hive: isolated (default, = today, byte-identical) or
    shared_world, with independent ``memory``/``graph`` shared-vs-own knobs (§11.2: the founder's
    hive rationale — memory promotion across agents — lives in the nmem tiers, so shared_world
    shares BOTH DSNs by default).
  * ``KeeperLock`` / ``become_keeper`` — single graph-keeper election **by construction** (§4.2):
    exactly ONE process may run the heavy graph-global loops (dreamstate / clustering / edge-type
    auto-promotion), enforced at the DB, not by config discipline.

**The §11.1 correctness rule, baked in:** ``pg_try_advisory_lock`` is *session-scoped* — it releases
the instant its connection closes or returns to a pool. So ``KeeperLock`` opens a **dedicated,
standalone** connection on the graph DB and holds it for the process lifetime, releasing only on
``release()`` (or process death → connection death → automatic failover, no split-brain). A pooled
connection here would silently drop the lock and produce two keepers.

This module gates NOTHING by itself — it hands the runtime ``is_keeper``. Moving the specific
graph-global loop starts behind that flag is the nmem-sym-coordinated step (§12.5 step 3).
"""
from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass

log = logging.getLogger(__name__)


@dataclass
class HiveConfig:
    """How an agent participates in a hive. Absent/``isolated`` = today's behavior exactly."""
    mode: str = "isolated"              # isolated | shared_world
    agent_id: str | None = None         # owner identity for agency scoping (B-i) + keeper logs
    graph_role: str = "contributor"     # keeper | contributor — willingness (the lock enforces)
    memory: str = "own"                 # shared | own — which nmem-tier DSN
    graph: str = "own"                  # shared | own — which symbol-graph DSN

    @classmethod
    def from_dict(cls, d: dict | None) -> "HiveConfig":
        d = d or {}
        mode = (d.get("mode") or "isolated").lower()
        shared = mode == "shared_world"
        # shared_world preset: both DSNs shared unless explicitly overridden (§11.2 / §12.2)
        return cls(
            mode=mode,
            agent_id=d.get("agent_id"),
            graph_role=(d.get("graph_role") or "contributor").lower(),
            memory=(d.get("memory") or ("shared" if shared else "own")).lower(),
            graph=(d.get("graph") or ("shared" if shared else "own")).lower(),
        )

    @property
    def is_shared_world(self) -> bool:
        return self.mode == "shared_world"

    @property
    def wants_keeper(self) -> bool:
        """This process is *willing* to be the graph-keeper (the lock decides if it actually is)."""
        return self.is_shared_world and self.graph_role == "keeper"


def keeper_key(identifier: str) -> int:
    """A stable signed 64-bit advisory-lock key for a shared graph. MUST be deterministic across
    processes (Python's ``hash()`` is per-process-salted — do NOT use it), so we derive it from a
    SHA-256 of the graph identifier (e.g. the shared graph's DB name / domain)."""
    return int.from_bytes(hashlib.sha256(identifier.encode()).digest()[:8], "big", signed=True)


def _normalize_dsn(dsn: str) -> str:
    """asyncpg wants a bare ``postgresql://`` DSN; strip a SQLAlchemy ``+asyncpg`` driver tag."""
    return dsn.replace("postgresql+asyncpg://", "postgresql://").replace("postgres+asyncpg://", "postgres://")


class KeeperLock:
    """A held Postgres advisory lock on a **dedicated** connection (see the module note on why it
    must not be pooled). ``held`` is True only while this process owns the graph-keeper role."""

    def __init__(self, key: int):
        self._key = int(key)
        self._conn = None
        self.held = False

    async def acquire(self, graph_dsn: str) -> bool:
        """Open a standalone connection and try the session-level advisory lock. Returns True and
        HOLDS the connection if won; closes it and returns False if another process holds it."""
        import asyncpg
        conn = await asyncpg.connect(_normalize_dsn(graph_dsn))
        try:
            won = await conn.fetchval("SELECT pg_try_advisory_lock($1)", self._key)
        except Exception:
            await conn.close()
            raise
        if won:
            self._conn = conn
            self.held = True
            return True
        await conn.close()
        return False

    async def release(self) -> None:
        """Unlock + close the dedicated connection (call on shutdown). Idempotent."""
        conn, self._conn, self.held = self._conn, None, False
        if conn is None:
            return
        try:
            await conn.execute("SELECT pg_advisory_unlock($1)", self._key)
        except Exception:  # noqa: BLE001
            pass
        finally:
            try:
                await conn.close()
            except Exception:  # noqa: BLE001
                pass


async def become_keeper(graph_dsn: str, key: int) -> "KeeperLock | None":
    """Try to become the single graph-keeper for the graph at ``graph_dsn``. Returns the held
    :class:`KeeperLock` if this process won the election, else None (→ run as a contributor)."""
    lock = KeeperLock(key)
    try:
        won = await lock.acquire(graph_dsn)
    except Exception as e:  # noqa: BLE001 — a failed election must not down the agent
        log.warning("[hive] keeper election errored (running as contributor): %s", e)
        return None
    return lock if won else None
