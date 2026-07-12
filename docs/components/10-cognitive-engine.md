# Cognitive Engine

Higher-order reasoning capabilities: deja vu matching for task delegation, curiosity signals for exploration, counterfactual reasoning for learning from failures, and delegation tracking for audit.

## Modules

| Module | Purpose |
|--------|---------|
| `cognitive.py` | CognitiveEngine: deja vu, curiosity signals, counterfactual generation, delegation lifecycle |

## Deja Vu (Similar Experience Matching)

```
Agent receives a new task
    |
    v
cognitive.find_similar_experience(instruction, agent_id, threshold=0.8)
    |
    ├── Embed the instruction
    ├── Search past delegations via vector similarity
    ├── Threshold: 0.8 cosine similarity
    └── Returns: list[DelegationRecord] with past outcomes

Use case: "We've done something like this before"
    → Past success? Reuse the approach
    → Past failure? Avoid the same mistake
```

## Curiosity Signals

```
Any subsystem detects something interesting
    |
    v
cognitive.emit_curiosity(
    source_agent,
    trigger_type,      # "graph_hypothesis", "anomaly", "pattern"
    summary,
    novelty_score,     # How new is this?
    uncertainty_score,  # How unsure are we?
    conflict_score,     # Does it contradict existing knowledge?
    recurrence_score,   # How often does this come up?
    business_impact,    # How important is this?
)
    |
    ├── composite_score = weighted combination
    ├── Status: "active"
    └── Stored in nmem_curiosity_signals

Curiosity signals:
    → Feed nmem-sym hypothesis generation
    → Decay over time during consolidation
    → Can be queried by agents for exploration targets
```

## Counterfactual Reasoning

```
Agent action fails
    |
    v
cognitive.generate_counterfactual(action, failure, agent_id)
    |
    ├── LLM generates alternative approaches
    ├── "What could we have done differently?"
    └── Returns: natural language alternative reasoning

Use case: Learning from failures
    → Store as journal entry with record_type="lesson_learned"
    → Future similar tasks benefit from deja vu matching
```

## Delegation Tracking

```
Orchestrator assigns task to researcher
    |
    v
cognitive.record_delegation(
    delegating_agent="orchestrator",
    target_agent="researcher",
    task_type="research",
    instruction="Find studies on X",
    context_data={...}
)
    |
    └── Returns delegation_id

Later:
    cognitive.complete_delegation(
        delegation_id,
        status="completed",  # or "failed"
        result_summary="Found 3 relevant studies..."
    )
```

## Links to Other Components

- **memory.py** (MemorySystem): `cognitive` property exposes the engine
- **consolidation.py** (Consolidation): curiosity decay runs during full cycle
- **bridge.py** (nmem-sym): hypotheses emitted as curiosity signals via `emit_curiosity()`
- **journal.py** (Journal): counterfactual lessons stored as journal entries

## Database Tables

| Table | Purpose |
|-------|---------|
| `nmem_delegations` | delegating_agent, target_agent, task_type, instruction, embedding, status, result_summary, result_data |
| `nmem_curiosity_signals` | source_agent, trigger_type, summary, composite_score, novelty/uncertainty/conflict/recurrence/business_impact scores, status, entity_type, entity_id |

## Key Config

No dedicated config section. Deja vu threshold (0.8) is a method parameter. Curiosity decay runs as part of consolidation.
