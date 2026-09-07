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
- `SymbolGoalStore` (goal_store) — maps nmem-sym `symbol_goals` to nmem-act's
  `GoalStore`, so nmem-act's `GoalPursuit` owns the pursuit lifecycle.
- recall consumer (recall) — subscribes to nmem autonomy's `memory.surfaced` and
  injects the surfaced memory into the next task's context.

**Roadmap (extract validate-then-thin from michelle, michelle stays live)**
- bootstrap: construct MemorySystem + SymbolGraph, register adapters, apply
  `capabilities.env`.
- adapters: LLM client (nmem's own), embedder, DB engine, actuator executor.
- comms: the channel-agnostic `CommsLoop` / `ChannelSink` (michelle service/communication).
- peer glue: the standard nmem-exchange handler.
- data-schema: persona / objectives / baseline-KB loader.
"""
from __future__ import annotations

# Lazy re-exports (PEP 562): keep `import nmem.agent_core` free of nmem_sym/nmem_act
# unless a specific adapter is actually accessed.
_LAZY = {
    "SymbolGoalStore": ("nmem.agent_core.goal_store", "SymbolGoalStore"),
}

__all__ = list(_LAZY) + ["recall"]


def __getattr__(name):
    target = _LAZY.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    import importlib
    mod = importlib.import_module(target[0])
    return getattr(mod, target[1])
