# nmem-sym Integration

How nmem connects to its symbolic cognition companion. The SymbolBridge in nmem-sym registers hooks into nmem's lifecycle to extract knowledge graphs, run dreamstate exploration, and inject symbol context into search results. All coupling is opt-in and crash-safe.

## Modules

| Module | Purpose |
|--------|---------|
| `consolidation.py` (nmem) | Provides `register_full_cycle_step()` and `register_nightly_step()` for external hooks |
| `memory.py` (nmem) | Provides `.on()` event registration and `.consolidation` property |
| `cognitive.py` (nmem) | Provides `emit_curiosity()` for hypothesis feedback |
| `bridge.py` (nmem-sym) | SymbolBridge: the ONLY coupling point between the two systems |

## How It Works

```
nmem MemorySystem
    |
    |  bridge = SymbolBridge(graph)
    |  bridge.connect(mem)
    |
    v
SymbolBridge registers into nmem's lifecycle:
    |
    ├── EVENT: "ltm.saved"
    |       → extract triples from the new LTM entry
    |       → build knowledge graph incrementally
    |
    ├── FULL CYCLE HOOK: "symbol_clustering"
    |       → run entity resolution on the graph
    |       → merge near-duplicate nodes
    |
    ├── NIGHTLY HOOK: "symbol_dreamstate"
    |       → explore the graph (structural holes, hypotheses)
    |       → run prediction grounding
    |       → compile procedures
    |       → detect coverage gaps
    |
    └── CURIOSITY FEEDBACK:
            → read recent speculative hypotheses
            → emit curiosity signals back to nmem
            → agents can see what the graph is speculating about
```

## Search Augmentation

```
Agent searches nmem:
    results = memory.search("treatment protocol", agent_id)

In parallel, SymbolBridge:
    context = bridge.augment_search("treatment protocol")
    |
    ├── Activate symbol graph for query
    ├── Surface relevant:
    |       C1: hypotheses (speculative inferences)
    |       C2: schemas (recurring patterns)
    |       C3: analogies (cross-domain parallels)
    |       C4: self-model (system capabilities/limitations)
    |       C5: procedures (compiled reasoning patterns)
    |       C6: goals (active objectives)
    |
    └── Return as formatted markdown section

Agent sees: nmem results + symbol context
    → Connections the LLM wouldn't find from unstructured text alone
```

## Drive System Integration

```
When drives are enabled, bridge.tick_drives(dt) runs in the main loop:
    |
    ├── Temporal awareness checks (silence, deadlines, staleness)
    ├── Drive accumulation from events
    ├── Winner-take-all arbiter selects action
    └── Intents executed: dreamstate, explore, verify, extract, ground

Events flowing through nmem feed drive pressure:
    ltm.saved → competence -0.03
    consolidation complete → coherence -0.4
    curiosity emitted → novelty -0.05
```

## Safety Properties

- **No nmem code changes required**: bridge lives entirely in nmem-sym
- **No import-time coupling**: nmem is never imported at module level in nmem-sym
- **Duck-typing**: bridge accepts any object with `.on()`, `.consolidation`, `.cognitive`
- **Crash isolation**: `_safe()` wraps every operation; failures are logged, never propagated
- **Fail-open**: `augment_search()` returns empty string on any failure

## What nmem Provides to nmem-sym

| nmem Feature | Used By nmem-sym |
|--------------|-----------------|
| LTM entries | Source for triple extraction |
| `.on("ltm.saved")` | Incremental extraction trigger |
| `register_full_cycle_step()` | Entity resolution clustering |
| `register_nightly_step()` | Dreamstate exploration |
| `emit_curiosity()` | Hypothesis feedback loop |
| Search results | Augmented with symbol context |
| Consolidation lifecycle | Timing for graph maintenance |

## What nmem-sym Provides to nmem

| nmem-sym Feature | Benefit to nmem |
|-----------------|----------------|
| Symbol context | Richer search results (connections between facts) |
| Hypotheses | Speculative inferences surfaced alongside facts |
| Predictions | "What if..." reasoning before agent acts |
| Procedures | Compiled reasoning patterns for similar problems |
| Goals | Active objectives visible in agent context |
| Self-model | System capabilities/limitations as context |

## Key Config

```
# nmem side (no nmem-sym specific config needed)
# nmem-sym registers itself via bridge.connect(mem)

# nmem-sym side
NMEM_SYM_DB_DSN = (same PostgreSQL database as nmem)
NMEM_SYM_DRIVES_ENABLED = 0|1
```
