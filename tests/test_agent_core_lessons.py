"""agent_core.recall_lessons — surface learned tool skills (+ compiled procedures) for a task,
graduated from michelle's service/tool_learning.py. Pure unit tests (mocked mem.skills), no DB.
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from nmem.agent_core import default_skill_chronic, recall_lessons


def _mem(hits):
    return SimpleNamespace(skills=SimpleNamespace(find=AsyncMock(return_value=hits)))


@pytest.mark.asyncio
async def test_formats_skills_with_do_avoid_and_no_procs_when_graph_none():
    hits = [SimpleNamespace(what="clear the address bar first", worked=True),
            SimpleNamespace(what="don't scroll blindly", worked=False)]
    text, proc_ids = await recall_lessons(_mem(hits), None, "web research",
                                          tool_tag="sandbox", agent_id="michelle")
    assert "APPLY it before" in text
    assert "- [DO] clear the address bar first" in text
    assert "- [AVOID] don't scroll blindly" in text
    assert proc_ids == []                       # graph None → no procedure recall


@pytest.mark.asyncio
async def test_empty_when_nothing_learned():
    text, proc_ids = await recall_lessons(_mem([]), None, "web research",
                                          tool_tag="sandbox", agent_id="michelle")
    assert text == "" and proc_ids == []


@pytest.mark.asyncio
async def test_tool_tag_and_agent_id_threaded_to_skills_find():
    mem = _mem([])
    await recall_lessons(mem, None, "verify a claim", tool_tag="browser", agent_id="ada", limit=7)
    mem.skills.find.assert_awaited_once()
    args, kwargs = mem.skills.find.await_args
    assert args[0] == "browser: verify a claim"   # tool_tag namespaces the query
    assert kwargs["agent_id"] == "ada" and kwargs["limit"] == 7


@pytest.mark.asyncio
async def test_surfaces_procedure_ids_and_context(monkeypatch):
    # a fake graph whose embedder + procedural lookups return two compiled procedures
    graph = SimpleNamespace(_embedder=SimpleNamespace(encode=lambda s: SimpleNamespace(tolist=lambda: [0.1])),
                            pool=object())
    procs = [{"id": 11}, {"id": 12}]
    import nmem_sym.procedural as proc
    monkeypatch.setattr(proc, "find_matching_procedures", AsyncMock(return_value=procs))
    monkeypatch.setattr(proc, "format_procedure_context", lambda p: "proc-ctx")
    text, proc_ids = await recall_lessons(_mem([]), graph, "task",
                                          tool_tag="sandbox", agent_id="m")
    assert proc_ids == [11, 12]
    assert "Compiled procedures:\nproc-ctx" in text


@pytest.mark.asyncio
async def test_fail_open_on_skills_error():
    mem = SimpleNamespace(skills=SimpleNamespace(find=AsyncMock(side_effect=RuntimeError("boom"))))
    text, proc_ids = await recall_lessons(mem, None, "x", tool_tag="t", agent_id="m")
    assert text == "" and proc_ids == []          # swallowed, never raises


# ── default_skill_chronic ───────────────────────────────────────

@pytest.mark.asyncio
async def test_skill_chronic_writes_one_strategy_lesson():
    mem = SimpleNamespace(journal=SimpleNamespace(add=AsyncMock()))
    handler = default_skill_chronic(mem, agent_id="michelle")
    await handler({"name": "avoid-redundant-actions", "trial_count": 160, "what": "kept re-clicking"})
    mem.journal.add.assert_awaited_once()
    kw = mem.journal.add.await_args.kwargs
    assert kw["agent_id"] == "michelle" and kw["entry_type"] == "chronic_skill"
    assert kw["record_type"] == "lesson" and kw["grounding"] == "confirmed" and kw["importance"] == 8
    assert "avoid-redundant-actions" in kw["title"]
    assert "approach/actuator must change" in kw["content"]


@pytest.mark.asyncio
async def test_skill_chronic_fail_open():
    mem = SimpleNamespace(journal=SimpleNamespace(add=AsyncMock(side_effect=RuntimeError("db down"))))
    handler = default_skill_chronic(mem, agent_id="m")
    await handler({"name": "x", "trial_count": 5})   # must not raise
