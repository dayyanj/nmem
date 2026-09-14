"""Phase 6-A — the session-summary person-alias SEED (dossier convergence).

Unit tests over fakes for ``_maybe_seed_person_alias``: it records a name↔id alias through the
registered cognitive backend's public seam only when the flag is on and both a stable id and a
distinct name are in hand, and is fail-open (an alias hiccup never disturbs the summary).
"""
import pytest

from nmem.agent_core import session_summary as ss


class _Bridge:
    def __init__(self): self.calls = []
    async def record_person_alias(self, *, person_ref, alias_ref, source=""):
        self.calls.append((person_ref, alias_ref, source))
        return True


class _Mem:
    def __init__(self, backend): self.cognitive_backend = backend


@pytest.mark.asyncio
async def test_seed_writes_alias_when_flag_on(monkeypatch):
    monkeypatch.setenv("NMEM_CHAT_PERSON_ALIAS_ENABLED", "1")
    b = _Bridge(); result = {}
    await ss._maybe_seed_person_alias(_Mem(b), "person:7", "Dayyan", result)
    assert b.calls == [("person:7", "Dayyan", "session_summary")]
    assert result["alias"] == ["person:7", "Dayyan"]


@pytest.mark.asyncio
async def test_seed_noop_when_flag_off(monkeypatch):
    monkeypatch.setenv("NMEM_CHAT_PERSON_ALIAS_ENABLED", "0")
    b = _Bridge(); result = {}
    await ss._maybe_seed_person_alias(_Mem(b), "person:7", "Dayyan", result)
    assert b.calls == [] and "alias" not in result


@pytest.mark.asyncio
async def test_seed_noop_on_missing_or_equal(monkeypatch):
    monkeypatch.setenv("NMEM_CHAT_PERSON_ALIAS_ENABLED", "1")
    b = _Bridge(); result = {}
    await ss._maybe_seed_person_alias(_Mem(b), "person:7", None, result)   # no name
    await ss._maybe_seed_person_alias(_Mem(b), "dj", "dj", result)         # id == name → nothing linked
    await ss._maybe_seed_person_alias(_Mem(b), "", "x", result)            # no id
    assert b.calls == [] and "alias" not in result


@pytest.mark.asyncio
async def test_seed_noop_without_backend(monkeypatch):
    monkeypatch.setenv("NMEM_CHAT_PERSON_ALIAS_ENABLED", "1")
    result = {}
    await ss._maybe_seed_person_alias(_Mem(None), "person:7", "Dayyan", result)
    assert "alias" not in result


@pytest.mark.asyncio
async def test_seed_is_fail_open(monkeypatch):
    monkeypatch.setenv("NMEM_CHAT_PERSON_ALIAS_ENABLED", "1")

    class _Boom:
        async def record_person_alias(self, **k): raise RuntimeError("down")
    result = {}
    await ss._maybe_seed_person_alias(_Mem(_Boom()), "person:7", "Dayyan", result)  # never raises
    assert "alias" not in result
