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
``release()`` (or process death → connection death → the lock **frees automatically at the DB**, no
split-brain). A pooled connection here would silently drop the lock and produce two keepers.

**Failover is LIVE (hive doc §16–§21).** nmem-sym's D1 seam (`SymbolBridge(is_keeper=…)`, commit
`1ab0ab3`) registers the graph-global hooks unconditionally and consults ``is_keeper()`` *inside* the
hook body, so the gate re-evaluates every cycle. ``AgentRuntime`` passes
``is_keeper=lambda: self.runs_graph_global`` (only in ``shared_world``; isolated ⇒ ``is_keeper=None`` ⇒
nmem-sym keeps its static-flag behavior = today), and a willing contributor runs ``_keeper_retry_loop``
to re-acquire the freed lock — on acquire it flips ``is_keeper`` and the dormant hooks resume on the
next cycle, **no restart, no split-brain**. (This supersedes the earlier boot-snapshot gate `c4ea564`,
which couldn't fail over — codex 2026-09-09.)

This module gates NOTHING by itself — it hands the runtime ``is_keeper``; the runtime wires it into the
bridge callable (above). **Remaining §12.5-step-3 work: the per-agent goal lifecycle** (abandon / reap /
impasse-tick) that rides the now-keeper-only dreamstate — it must move to a per-agent ``_lifecycle_loop``
(owner-scoped, ``owner_agent=None``=today for isolated) co-landing with nmem-sym's D2
``run_goal_lifecycle`` entrypoint. That one is a real design item, not a flag.
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

    def alive(self) -> bool:
        """Cheap + synchronous: do we still hold a live connection? asyncpg flips ``is_closed()`` on a
        closed/failed conn, so this catches a dropped lock without a round-trip — used as the per-cycle
        keeper gate. A *silent* network partition that ``is_closed()`` misses is caught by ``verify()``."""
        return self.held and self._conn is not None and not self._conn.is_closed()

    async def verify(self) -> bool:
        """Authoritative liveness: actually exercise the lock connection (catches silent drops the local
        ``is_closed()`` flag misses). On ANY failure the lock is considered lost → ``held`` goes False so
        ``alive()`` immediately de-authorizes. Used by the keeper's watch tick, not per-cycle."""
        if not self.held or self._conn is None:
            return False
        try:
            await self._conn.execute("SELECT 1")
        except Exception:  # noqa: BLE001 — any failure = lock/session gone
            self.held = False
            return False
        return not self._conn.is_closed()

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
