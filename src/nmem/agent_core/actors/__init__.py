"""nmem.agent_core.actors — give an agent hands, over any protocol.

The design rule (see docs/nmem-studio-product-design.md §5): **a protocol is just an adapter
that emits nmem-act ``Action``s; we never fork the executor, the gate, or the learning loop
per protocol.** Every actuator — a webhook, an MCP tool, another agent over A2A, a mounted
Python plugin — becomes an ``Action(name, handler, capability_class, description, parameters)``
in one ``ActionRegistry``, and they all run through one ``ToolCallingExecutor`` that gives you,
uniformly:

  * the **autonomy gate** (read-only / tiered / allowlist) + **approval hook**,
  * the **experiential loop** (episode + procedure reward + honest discharge → the agent learns
    which tools actually work), via agent_core.build_experiential_sink,
  * prediction + outcome recording.

This package is the seam + the adapters:
  * ``build_executor(registry, backend=…)``  — wrap a registry as a gated, learning executor
    driven by an OpenAI-tools LLM (any agent_core.backend). This is what ``AgentRuntime``'s
    ``build_executor=`` hook returns.
  * ``webhook`` / ``mcp`` / ``a2a`` / ``plugins`` — adapters that produce ``Action``s.
  * ``assemble_registry(spec, …)`` — build a registry from a declarative ``actors:`` config
    (what the studio wizard writes), returning ``(registry, aclose)`` so live sessions (MCP/A2A)
    are torn down cleanly.
"""
from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)


def _chat_with_tools(backend):
    """Adapt an agent_core.backend to nmem-act's OpenAIToolSelector callable contract:
    ``async (messages, tools) -> (content, [{id,name,arguments}])``."""
    async def cwt(messages: list[dict], tools: list[dict]):
        # No permitted tools this step (gate filtered them all out) → a plain chat. Passing an
        # empty `tools` array is rejected by strict servers ("tools must not be an empty array"),
        # and with nothing to call the model should just answer / conclude.
        if not tools:
            return await backend.chat(messages, max_tokens=1024), []
        res = await backend.chat_with_tools(messages, tools, tool_choice="auto", max_tokens=1024)
        calls = [{"id": tc.id, "name": tc.name, "arguments": tc.arguments} for tc in res.tool_calls]
        return res.content, calls
    return cwt


def build_selector(backend, *, preamble: str | None = None):
    """An LLM tool-selector driven by `backend` (the agent's own reasoning brain)."""
    from nmem_act import OpenAIToolSelector
    from nmem_act.openai_selector import DEFAULT_PREAMBLE
    return OpenAIToolSelector(_chat_with_tools(backend), system_preamble=preamble or DEFAULT_PREAMBLE)


def build_executor(registry, *, backend, mem=None, agent_id: str = "agent", bridge=None,
                   gate=None, approval=None, max_steps: int = 8,
                   reflect=None, record_skill=None, preamble: str | None = None):
    """Wrap an ``ActionRegistry`` as a gated, outcome-recording ``ToolCallingExecutor`` driven by
    `backend`. When `bridge` + `mem` are provided, the run is recorded through the graduated
    experiential sink (episode + procedure reward + honest discharge + finding memory), optionally
    wrapped by nmem-act's reflective sink to capture tool-use skills (`reflect`/`record_skill`
    injected). This is the object ``AgentRuntime(build_executor=…)`` returns."""
    from nmem_act import ToolCallingExecutor

    selector = build_selector(backend, preamble=preamble)
    sink = None
    if bridge is not None and mem is not None:
        from nmem.agent_core import build_experiential_sink
        sink = build_experiential_sink(bridge, mem, agent_id)
        if reflect is not None:
            from nmem_act import make_reflective_sink
            sink = make_reflective_sink(sink, reflect=reflect, record_skill=record_skill,
                                        agent_id=agent_id, enabled=True)
    return ToolCallingExecutor(registry, selector, gate=gate, approval=approval,
                               outcome_sink=sink, max_steps=max_steps)


async def run(executor, goal: str, *, capability_class=None):
    """Convenience: drive one goal through a ToolCallingExecutor. Builds the composite proposal
    (the goal rides in ``rationale``, which the selector sees) and returns the composite Outcome."""
    from nmem_act import ActionProposal, CapabilityClass
    proposal = ActionProposal(action_type="llm_tool_call", rationale=goal,
                              capability_class=capability_class or CapabilityClass.READ_ONLY)
    return await executor.execute(proposal)


async def assemble_registry(spec: dict, *, backend=None) -> tuple[Any, Any]:
    """Build an ``ActionRegistry`` from a declarative ``actors:`` config and return
    ``(registry, aclose)`` where ``aclose`` is an async callable that tears down any live
    sessions (MCP/A2A). Config shape (all keys optional)::

        actors:
          webhooks:  [{name,url,method,description,parameters,capability_class}, ...]
          openapi:   [{spec_url|spec, base_url?, include?}, ...]
          utcp:      [{manual_url|manual, name?, headers?, only?}, ...]
          mcp:       [{name,transport:http|stdio,url?|command?,args?,headers?,only?}, ...]
          a2a:       [{name,card_url,description?}, ...]
          plugins_dir: "/data/agent/plugins/executors"

    Adapters are imported lazily so an agent that uses none of them pulls in no deps."""
    from nmem_act import ActionRegistry
    reg = ActionRegistry()
    closers: list = []
    spec = spec or {}

    for wh in spec.get("webhooks", []) or []:
        from nmem.agent_core.actors.webhook import webhook_action
        reg.register(webhook_action(wh))

    for oa in spec.get("openapi", []) or []:
        from nmem.agent_core.actors.webhook import openapi_actions
        for action in await openapi_actions(oa):
            reg.register(action)

    for man in spec.get("utcp", []) or []:
        from nmem.agent_core.actors.utcp import utcp_actions
        for action in await utcp_actions(man):
            reg.register(action)

    for server in spec.get("mcp", []) or []:
        from nmem.agent_core.actors.mcp import connect_mcp
        actions, aclose = await connect_mcp(server)
        for action in actions:
            reg.register(action)
        if aclose is not None:
            closers.append(aclose)

    for remote in spec.get("a2a", []) or []:
        from nmem.agent_core.actors.a2a import a2a_action
        action, aclose = await a2a_action(remote)
        if action is not None:
            reg.register(action)
        if aclose is not None:
            closers.append(aclose)

    plugins_dir = spec.get("plugins_dir")
    if plugins_dir:
        from nmem.agent_core.actors.plugins import load_plugins
        load_plugins(plugins_dir, reg)

    async def aclose():
        # REVERSE order (LIFO): each MCP/A2A session opened nested AnyIO cancel scopes on the
        # lifespan task; unwinding them out of order raises "Attempted to exit a cancel scope that
        # isn't the current task's current cancel scope" and aborts the rest of the teardown.
        for c in reversed(closers):
            try:
                await c()
            except Exception as e:  # noqa: BLE001
                log.warning("[actors] session close failed: %s", e)

    log.info("[actors] registry assembled: %d action(s) [%s]", len(reg), ", ".join(reg.names()))
    return reg, aclose
