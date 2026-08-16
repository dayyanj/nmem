"""
Commitments — nmem as the conscious front door for external obligations.

nmem records commitments and forwards to a registered cognitive backend
(nmem-sym in production; a mock here). Tests the impose/forward, standalone
(no backend), flush-on-attach queue, confirm, and the reverse channel.
"""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import text


class MockBackend:
    """Stand-in for nmem-sym's SymbolBridge (the cognitive backend)."""

    def __init__(self):
        self.requestors: dict[str, int] = {}
        self._next_req = 1
        self._next_obl = 1
        self.calls: list = []

    async def register_requestor(self, name, authority=0.5):
        self.calls.append(("register_requestor", name))
        if name in self.requestors:                     # dedup by name
            return SimpleNamespace(id=self.requestors[name])
        rid = self._next_req
        self._next_req += 1
        self.requestors[name] = rid
        return SimpleNamespace(id=rid)

    async def impose_obligation(self, requester_id, description, deadline, importance=1.0):
        self.calls.append(("impose_obligation", requester_id, description))
        oid = self._next_obl
        self._next_obl += 1
        return SimpleNamespace(id=oid)

    async def confirm_obligation(self, oid):
        self.calls.append(("confirm_obligation", oid))

    async def abandon_obligation(self, oid):
        self.calls.append(("abandon_obligation", oid))

    async def renegotiate_obligation(self, oid, deadline):
        self.calls.append(("renegotiate_obligation", oid, deadline))


async def _count(mem, status=None) -> int:
    q = "SELECT COUNT(*) FROM nmem_commitments"
    if status:
        q += f" WHERE status = '{status}'"
    async with mem._db.session() as session:
        return (await session.execute(text(q))).scalar()


@pytest.mark.asyncio
async def test_impose_records_and_forwards_to_backend(mem):
    backend = MockBackend()
    mem.register_cognitive_backend(backend)
    dl = datetime.now(timezone.utc) + timedelta(days=2)

    c = await mem.commitments.impose("founder", "ship the benchmark", dl,
                                     authority=0.9, importance=1.0)
    assert c.status == "open"
    assert c.sym_obligation_id is not None                    # mirrored to backend
    assert await _count(mem) == 1
    kinds = [call[0] for call in backend.calls]
    assert kinds == ["register_requestor", "impose_obligation"]


@pytest.mark.asyncio
async def test_standalone_records_without_backend(mem):
    c = await mem.commitments.impose("someone", "do a thing")
    assert c.status == "open"
    assert c.sym_obligation_id is None                        # no backend → queued
    assert await _count(mem) == 1


@pytest.mark.asyncio
async def test_impose_inherits_instance_scope_and_list_is_isolated(mem):
    """A scoped instance records host-imposed commitments under its own scope
    (no explicit project_scope needed), and list() only shows that scope."""
    mem._config.project_scope = "proj-A"
    a = await mem.commitments.impose("founder", "scoped work")   # no scope arg
    assert a.project_scope == "proj-A"
    # A commitment belonging to another project isn't visible here.
    await mem.commitments.impose("founder", "other work", project_scope="proj-B")
    scoped = await mem.commitments.list("open")
    assert [c.description for c in scoped] == ["scoped work"]


@pytest.mark.asyncio
async def test_flush_pending_mirrors_on_backend_attach(mem):
    # imposed before any backend was attached
    await mem.commitments.impose("founder", "prior commitment")
    assert await _count(mem, "open") == 1

    backend = MockBackend()
    mem.register_cognitive_backend(backend)
    n = await mem.commitments.flush_pending()
    assert n == 1
    assert any(call[0] == "impose_obligation" for call in backend.calls)
    # the mirrored commitment now carries a sym id
    (c,) = await mem.commitments.list("open")
    assert c.sym_obligation_id is not None


@pytest.mark.asyncio
async def test_requester_is_registered_once(mem):
    backend = MockBackend()
    mem.register_cognitive_backend(backend)
    await mem.commitments.impose("founder", "a")
    await mem.commitments.impose("founder", "b")
    regs = [c for c in backend.calls if c[0] == "register_requestor"]
    assert len(regs) == 1                                     # cached name → id


@pytest.mark.asyncio
async def test_confirm_marks_fulfilled_and_forwards(mem):
    backend = MockBackend()
    mem.register_cognitive_backend(backend)
    c = await mem.commitments.impose("r", "x")
    await mem.commitments.confirm(c.id)
    assert await _count(mem, "fulfilled") == 1
    assert ("confirm_obligation", c.sym_obligation_id) in backend.calls


@pytest.mark.asyncio
async def test_lifecycle_ops_return_false_for_missing(mem):
    """Codex P2: confirm/abandon/renegotiate must report failure for a
    nonexistent commitment, not silently succeed."""
    assert await mem.commitments.confirm(9999) is False
    assert await mem.commitments.abandon(9999) is False
    assert await mem.commitments.renegotiate(9999, datetime.now(timezone.utc)) is False


@pytest.mark.asyncio
async def test_register_backend_schedules_flush_of_queued(mem):
    """Codex P2: registering a backend (inside a running loop) flushes the
    commitments queued while none was attached."""
    import asyncio
    await mem.commitments.impose("founder", "prior")     # queued, no backend
    backend = MockBackend()
    mem.register_cognitive_backend(backend)              # schedules flush_pending
    await asyncio.sleep(0.05)                             # let the task run
    assert any(c[0] == "impose_obligation" for c in backend.calls)


@pytest.mark.asyncio
async def test_reverse_channel_breach_updates_record(mem):
    backend = MockBackend()
    mem.register_cognitive_backend(backend)
    events = []

    async def _rec(d):
        events.append(d)

    mem.on("commitment.breached")(_rec)
    c = await mem.commitments.impose("r", "x")

    # backend reports a breach for the linked obligation
    await mem.record_obligation_event("breached", c.sym_obligation_id, {"foo": 1})
    assert await _count(mem, "breached") == 1
    assert events and events[0]["id"] == c.id
