"""default_proposal — the graduated pursuit-proposal builder. It assembles context (all fail-open) and
emits an ActionProposal whose objective rides in BOTH ``rationale`` (a ToolCallingExecutor reads that)
and ``params`` (a params-runner like a sandbox reads that), with a configurable ``action_type``."""
import asyncio

import pytest

pytest.importorskip("nmem_act")

from nmem.agent_core.proposal import default_proposal


class _Goal:
    objective = "learn about X"
    id = 7
    source_ref = {"drive": "novelty"}


class _Mem:
    async def search(self, agent_id, query, top_k=3):
        return []


def test_default_proposal_emits_the_dual_contract():
    build = default_proposal(_Mem(), object(), None, agent_id="a",
                             action_type="pursue_knowledge", tool_tag="tool")
    p = asyncio.run(build(_Goal()))
    assert p.action_type == "pursue_knowledge"        # host-configured dispatch/gate name
    assert p.id == "pursue:7"
    assert "learn about X" in p.rationale             # objective in rationale → tool selector sees it
    assert p.params["objective"] == "learn about X"   # and in params → a sandbox runner reads it
    assert p.params["goal_id"] == 7
    assert "lessons" in p.params and "procedure_ids" in p.params


def test_default_proposal_defaults_action_type_and_is_fail_open():
    class _BadMem:
        async def search(self, *a, **k):
            raise RuntimeError("db down")

    build = default_proposal(_BadMem(), object(), None, agent_id="a")   # bad mem + missing helpers
    p = asyncio.run(build(_Goal()))                    # must NOT raise — context is best-effort
    assert p.action_type == "llm_tool_call"            # the studio/tool default
    assert p.params["objective"] == "learn about X"


def test_runtime_auto_defaults_the_builder_from_pursuit_config():
    # the thin-host seam agent-a now relies on: with no host build_proposal, the runtime builds the
    # default from pursuit.{action_type,tool_tag,...} + its own mem/graph/bridge/agent_id.
    import types

    from nmem.agent_core.runtime import AgentRuntime

    rt = AgentRuntime.__new__(AgentRuntime)
    rt.mem = _Mem()
    rt.graph = object()
    rt.bridge = None
    rt._persona = types.SimpleNamespace(agent_id="agent-a")   # rt.agent_id reads this
    build = rt._default_proposal({"action_type": "pursue_knowledge", "tool_tag": "web",
                                  "lessons_query": "seek and verify", "capability_class": "read_only"})
    p = asyncio.run(build(_Goal()))
    assert p.action_type == "pursue_knowledge"                 # config knob threaded through
    assert p.params["objective"] == "learn about X"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
