"""
Skills — nmem as the conscious front door for "a process that worked / one
that didn't".

nmem records skills durably + vectorized and forwards to a registered cognitive
backend (nmem-sym in production; a mock here). Tests standalone operation, the
opt-in gate, record/find, dedup coalescing, scope visibility, the mirror queue
(flush-on-attach, no double-mirror under the lock), and the reverse channel's
idempotent/monotonic semantics.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy import text

from nmem import MemorySystem, NmemConfig

from tests.conftest import TEST_DB_URL, reset_db


class MockSkillBackend:
    """Stand-in for nmem-sym's SymbolBridge (the cognitive backend)."""

    def __init__(self):
        self._next = 1
        self.calls: list = []

    async def register_skill(self, name, what, outcome, *, worked=True,
                             success_count=0, trial_count=0):
        self.calls.append(("register_skill", name))
        sid = self._next
        self._next += 1
        return SimpleNamespace(id=sid)

    async def reinforce_skill(self, sym_id, success):
        self.calls.append(("reinforce_skill", sym_id, success))

    async def supersede_skill(self, sym_id):
        self.calls.append(("supersede_skill", sym_id))

    async def abandon_skill(self, sym_id):
        self.calls.append(("abandon_skill", sym_id))

    def n(self, kind: str) -> int:
        return sum(1 for c in self.calls if c[0] == kind)


async def _count(mem, status: str | None = None) -> int:
    q = "SELECT COUNT(*) FROM nmem_skills"
    if status:
        q += f" WHERE status = '{status}'"
    async with mem._db.session() as session:
        return (await session.execute(text(q))).scalar()


@pytest_asyncio.fixture
async def sk() -> MemorySystem:
    """MemorySystem with skills ENABLED (noop providers, shared Postgres)."""
    config = NmemConfig(
        database_url=TEST_DB_URL,
        embedding={"provider": "noop", "dimensions": 384},
        llm={"provider": "noop"},
        consolidation={"enabled": False},
        skills={"enabled": True},
    )
    system = MemorySystem(config)
    await system.initialize()
    await reset_db(system)
    yield system  # type: ignore[misc]
    await reset_db(system)
    await system.close()


# ── opt-in gate ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_disabled_by_default_is_inert(mem):
    """Default config has skills off — record/find are library-wide no-ops."""
    assert mem._config.skills.enabled is False
    assert await mem.skills.record("did a thing", worked=True) is None
    assert await mem.skills.find("did a thing") == []
    assert await _count(mem) == 0


# ── record / find standalone ───────────────────────────────────

@pytest.mark.asyncio
async def test_record_and_find_standalone(sk):
    info = await sk.skills.record(
        "roll out blue green deploy behind a flag",
        outcome="zero downtime", worked=True, name="blue-green")
    assert info is not None
    assert info.sym_procedure_id is None          # no backend → queued
    assert info.success_count == 1 and info.trial_count == 1

    hits = await sk.skills.find("roll out blue green deploy behind a flag")
    assert len(hits) == 1
    assert hits[0].id == info.id
    assert hits[0].similarity is not None and hits[0].similarity > 0.99


@pytest.mark.asyncio
async def test_record_coalesces_near_duplicate(sk):
    a = await sk.skills.record("cache the vector index warm on startup", worked=True)
    b = await sk.skills.record("cache the vector index warm on startup", worked=False)
    # identical trigger → coalesced into one row, reinforced (LTP then LTD)
    assert a.id == b.id
    assert await _count(sk) == 1
    got = await sk.skills.get(a.id)
    assert got.trial_count == 2


@pytest.mark.asyncio
async def test_find_scope_is_scoped_or_global(sk):
    # a global skill (no scope) and a scoped one
    await sk.skills.record("global technique alpha beta", project_scope=None)
    sk._config.project_scope = "proj-A"
    await sk.skills.record("scoped technique gamma delta", project_scope="proj-A")
    # scoped instance sees both its own scope AND global
    g = await sk.skills.find("global technique alpha beta")
    s = await sk.skills.find("scoped technique gamma delta")
    assert len(g) == 1 and len(s) == 1


# ── mirror to backend ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_record_mirrors_to_backend(sk):
    backend = MockSkillBackend()
    sk.register_cognitive_backend(backend)
    info = await sk.skills.record("provision replica then failover", worked=True)
    assert info.sym_procedure_id is not None
    assert backend.n("register_skill") == 1


@pytest.mark.asyncio
async def test_flush_pending_mirrors_on_attach(sk):
    await sk.skills.record("skill one distinct words aaa", worked=True)
    await sk.skills.record("skill two distinct words bbb", worked=True)
    assert await _count(sk, "active") == 2

    backend = MockSkillBackend()
    sk.register_cognitive_backend(backend)
    n = await sk.skills.flush_pending()
    assert n == 2
    assert backend.n("register_skill") == 2


@pytest.mark.asyncio
async def test_concurrent_flush_never_double_mirrors(sk):
    """The _mirror_lock + under-lock re-check (link AND status) guarantees each
    queued skill is mirrored exactly once even under concurrent flushers."""
    await sk.skills.record("alpha distinct one", worked=True)
    await sk.skills.record("beta distinct two", worked=True)

    backend = MockSkillBackend()
    sk.register_cognitive_backend(backend)     # schedules one auto-flush task
    # ...plus two explicit concurrent flushers
    await asyncio.gather(sk.skills.flush_pending(), sk.skills.flush_pending())
    await asyncio.sleep(0.05)                   # let the scheduled task run too

    assert backend.n("register_skill") == 2     # exactly once per skill, not 4/6


# ── reverse channel (backend → nmem) ───────────────────────────

@pytest.mark.asyncio
async def test_reverse_channel_idempotent_and_monotonic(sk):
    backend = MockSkillBackend()
    sk.register_cognitive_backend(backend)
    info = await sk.skills.record("emit myelination reverse channel test", worked=True)
    sym = info.sym_procedure_id
    assert sym is not None

    events: list = []
    sk.on("skill.reinforced")(lambda d: events.append(("reinforced", d)))

    # duplicate reinforced with the same event_id → counts increment ONCE
    await sk.record_skill_event("reinforced", sym, {"event_id": "e1", "success": True})
    await sk.record_skill_event("reinforced", sym, {"event_id": "e1", "success": True})
    got = await sk.skills.get(info.id)
    assert got.success_count == 2 and got.trial_count == 2    # 1 (record) + 1 (once)

    # retire is terminal; a later myelinated must not reactivate it
    await sk.record_skill_event("retired", sym, {"event_id": "e2"})
    await sk.record_skill_event("myelinated", sym, {"event_id": "e3"})
    got = await sk.skills.get(info.id)
    assert got.status == "retired"


# ── plasticity + lifecycle ─────────────────────────────────────

@pytest.mark.asyncio
async def test_terminal_status_is_fully_sticky(sk):
    """First terminal event wins — a later terminal can't rewrite it."""
    backend = MockSkillBackend()
    sk.register_cognitive_backend(backend)
    info = await sk.skills.record("sticky terminal skill", worked=True)
    sym = info.sym_procedure_id
    await sk.record_skill_event("retired", sym, {"event_id": "t1"})
    await sk.record_skill_event("superseded", sym, {"event_id": "t2"})
    got = await sk.skills.get(info.id)
    assert got.status == "retired"


@pytest.mark.asyncio
async def test_reinforced_absolute_counts_are_idempotent(sk):
    """When the backend sends authoritative counts, re-delivery is a no-op."""
    backend = MockSkillBackend()
    sk.register_cognitive_backend(backend)
    info = await sk.skills.record("absolute count skill", worked=True)
    sym = info.sym_procedure_id
    payload = {"trial_count": 10, "success_count": 7}
    await sk.record_skill_event("reinforced", sym, dict(payload))
    await sk.record_skill_event("reinforced", sym, dict(payload))   # duplicate delivery
    got = await sk.skills.get(info.id)
    assert got.trial_count == 10 and got.success_count == 7


@pytest.mark.asyncio
async def test_reverse_channel_noop_when_disabled(mem):
    """Reverse-channel events must not mutate rows or emit while skills off."""
    fired = []
    mem.on("skill.retired")(lambda d: fired.append(d))
    await mem.record_skill_event("retired", 123, {"event_id": "x"})
    assert fired == []


@pytest.mark.asyncio
async def test_find_cross_scope_wildcard(sk):
    # fully disjoint word sets so each query matches only its own skill
    await sk.skills.record("kubernetes rollout canary weighting", project_scope="proj-A")
    await sk.skills.record("stripe webhook idempotency replay", project_scope="proj-B")
    a = await sk.skills.find("kubernetes rollout canary weighting", project_scope="*")
    b = await sk.skills.find("stripe webhook idempotency replay", project_scope="*")
    assert len(a) == 1 and a[0].project_scope == "proj-A"
    assert len(b) == 1 and b[0].project_scope == "proj-B"


@pytest.mark.asyncio
async def test_reinforce_forwards_when_linked(sk):
    backend = MockSkillBackend()
    sk.register_cognitive_backend(backend)
    info = await sk.skills.record("reinforce forwarding path", worked=True)
    assert await sk.skills.reinforce(info.id, success=False) is True
    got = await sk.skills.get(info.id)
    assert got.trial_count == 2 and got.success_count == 1
    assert ("reinforce_skill", info.sym_procedure_id, False) in backend.calls


@pytest.mark.asyncio
async def test_supersede_excludes_from_find(sk):
    a = await sk.skills.record("older technique to be replaced", worked=True)
    b = await sk.skills.record("newer better technique replacement", worked=True)
    assert await sk.skills.supersede(a.id, b.id) is True
    got = await sk.skills.get(a.id)
    assert got.status == "superseded" and got.superseded_by_id == b.id
    hits = await sk.skills.find("older technique to be replaced")
    assert all(h.id != a.id for h in hits)
