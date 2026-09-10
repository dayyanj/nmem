"""nmem.agent_core — reusable scaffolding for building an autonomous agent.

The thesis (see nmem/docs/nmem-agent-core-plan.md):

    An agent = data + adapters + config. All cognition — drives, goals, pursuit,
    learning, recall, enrichment — lives in and hardens in the nmem libraries
    (nmem / nmem-sym / nmem-act), toggled by flags. A new agent is then a config,
    a persona, and (optionally) a custom executor adapter.

`agent_core` is where the *reusable glue* between those libraries lives — the
adapters and bootstrap that every agent would otherwise hand-roll. It is proven by
extraction: each piece here graduated from michelle-ai (the live proving ground)
only after it ran in production, so this package carries no speculative API.

**Import discipline / no circular import.** `agent_core` imports `nmem_sym` and
`nmem_act`; `nmem_sym` imports `nmem`. So `nmem/__init__` must NOT import
`agent_core` (it doesn't), and this package is opt-in: `from nmem.agent_core import
SymbolGoalStore` pulls its deps lazily, only when you touch an adapter that needs
them. Agents without `nmem_sym`/`nmem_act` installed can still `import nmem`.

**Graduated so far**
- `AgentRuntime` (runtime) — the headless boot->run->shutdown lifecycle + cognition
  wiring; a new agent is `AgentRuntime(config, persona, executor=...)` + host I/O.
- `Persona` / `seed_persona` (persona) — the per-agent identity DATA + its loader.
- `build_memory` / `build_symbol_graph` (memory) — the order-sensitive bootstrap.
- `build_backend` + backends (backend) — provider-agnostic LLM (OpenAI-compat/Anthropic).
- `CommsLoop` / `ChannelSink` / `Utterance` (comms) — channel-agnostic communication.
- `SymbolGoalStore` (goal_store) — maps nmem-sym `symbol_goals` to nmem-act's `GoalStore`.
- recall consumer (recall) — subscribes to autonomy's `memory.surfaced` and injects it.

- `PeerExchange` / `PeerExchangeSink` (peer) — agent-to-agent messaging over
  nmem-exchange; inbound challenges run the agent's injected `on_challenge` cognition.
- `build_experiential_sink` (actuation) — the act->learn outcome sink (episode +
  procedure reward + honest discharge + merit finding memory) for any acting agent.
- `make_ops_router` (ops) — OPTIONAL FastAPI router (lazy-imported): /health +
  /admin/{consolidate,nightly,dreamstate,probe_recipes,seed_recall}. An HTTP agent
  mounts it for instant observability; a voice/CLI agent ignores it.
- `make_studio_router` (studio) — OPTIONAL FastAPI router (lazy-imported): /studio/
  {catalog,test-llm,list-models,create}. The nmem-studio setup-wizard backend — stands
  a new agent up from a web UI; the LLM key never leaves the server.

**Roadmap (extract validate-then-thin from michelle, michelle stays live)**
- a DB-engine helper (Group 1E) + prompt-assembly helpers (Group 3G) — opportunistic.
"""
from __future__ import annotations

# Lazy re-exports (PEP 562): keep `import nmem.agent_core` free of nmem_sym/nmem_act
# unless a specific adapter is actually accessed.
_LAZY = {
    "AgentRuntime": ("nmem.agent_core.runtime", "AgentRuntime"),
    "Persona": ("nmem.agent_core.persona", "Persona"),
    "seed_persona": ("nmem.agent_core.persona", "seed_persona"),
    "build_memory": ("nmem.agent_core.memory", "build_memory"),
    "build_symbol_graph": ("nmem.agent_core.memory", "build_symbol_graph"),
    "build_backend": ("nmem.agent_core.backend", "build_backend"),
    "CommsLoop": ("nmem.agent_core.comms", "CommsLoop"),
    "ChannelSink": ("nmem.agent_core.comms", "ChannelSink"),
    "Utterance": ("nmem.agent_core.comms", "Utterance"),
    "PeerExchange": ("nmem.agent_core.peer", "PeerExchange"),
    "PeerExchangeSink": ("nmem.agent_core.peer", "PeerExchangeSink"),
    "build_experiential_sink": ("nmem.agent_core.actuation", "build_experiential_sink"),
    "make_ops_router": ("nmem.agent_core.ops", "make_ops_router"),
    "make_studio_router": ("nmem.agent_core.studio", "make_studio_router"),
    "create_studio_app": ("nmem.agent_core.studio", "create_studio_app"),
    "SymbolGoalStore": ("nmem.agent_core.goal_store", "SymbolGoalStore"),
    "proposal_source": ("nmem.agent_core.goal_store", "proposal_source"),
    "recall_lessons": ("nmem.agent_core.lessons", "recall_lessons"),
    "CAPABILITIES": ("nmem.agent_core.capabilities", "CAPABILITIES"),
    "Capability": ("nmem.agent_core.capabilities", "Capability"),
    "validate_capabilities": ("nmem.agent_core.capabilities", "validate"),
    "check_capability_env": ("nmem.agent_core.capabilities", "check_env"),
    "capability_catalog": ("nmem.agent_core.capabilities", "catalog"),
    "PRESETS": ("nmem.agent_core.capabilities", "PRESETS"),
    "preset_flags": ("nmem.agent_core.capabilities", "preset_flags"),
    "build_agent_files": ("nmem.agent_core.config_writer", "build_agent_files"),
    "write_agent": ("nmem.agent_core.config_writer", "write_agent"),
    "render_capabilities_env": ("nmem.agent_core.config_writer", "render_capabilities_env"),
    "converse": ("nmem.agent_core.chat", "converse"),
    "build_context": ("nmem.agent_core.chat", "build_context"),
    "system_prompt": ("nmem.agent_core.chat", "system_prompt"),
    "init_viz": ("nmem.agent_core.viz", "init_viz"),
    "VizBridge": ("nmem.agent_core.viz", "VizBridge"),
    "HiveConfig": ("nmem.agent_core.hive", "HiveConfig"),
    "KeeperLock": ("nmem.agent_core.hive", "KeeperLock"),
    "become_keeper": ("nmem.agent_core.hive", "become_keeper"),
    "keeper_key": ("nmem.agent_core.hive", "keeper_key"),
}

__all__ = list(_LAZY) + ["recall", "chat"]


def __getattr__(name):
    target = _LAZY.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    import importlib
    mod = importlib.import_module(target[0])
    return getattr(mod, target[1])
