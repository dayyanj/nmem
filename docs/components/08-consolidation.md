# Consolidation

Background lifecycle management for memory entries. Three trigger modes: reactive micro-cycles for urgent entries, scheduled full cycles for maintenance, and nightly synthesis for pattern extraction and retrospective validation.

## Modules

| Module | Purpose |
|--------|---------|
| `consolidation.py` | Consolidator: background task with micro-cycle/full-cycle/nightly modes, custom hook registration, convergence detection |

## Three Trigger Modes

```
1. MICRO-CYCLE (reactive)
   Triggered by: journal entry with importance >= 7
   Cooldown: 5 minutes between triggers
   Scope: quick promotion of high-importance entries

2. FULL CYCLE (scheduled)
   Triggered by: interval timer (default every 6 hours)
   Scope: complete maintenance pass

3. NIGHTLY SYNTHESIS (daily)
   Triggered by: UTC hour (default 23:00)
   Scope: full cycle + pattern synthesis + retrospective
   Requires: minimum 10 journal entries since last nightly
```

## Full Cycle Steps

```
run_full_cycle() → ConsolidationStats
    |
    ├── 1. EXPIRE & PROMOTE
    |       Journal entries past expires_at → archive or promote to LTM
    |       Entries with importance >= auto_promote_importance → LTM
    |
    ├── 2. IMPORTANCE PROMOTION
    |       Journal entries with high access_count → LTM
    |       access_count >= auto_promote_access_count (5)
    |
    ├── 3. SHARED PROMOTION
    |       LTM entries accessed by 2+ agents, 3+ times, importance >= 8 → Shared
    |       "Social learning" -- agents vote with their queries
    |
    ├── 4. DEDUPLICATION
    |       Union-find clustering of similar LTM entries (0.85 similarity)
    |       LLM-based merging of duplicate clusters
    |       Winner keeps highest importance + merged content
    |
    ├── 5. AUTO-IMPORTANCE RESCORING
    |       Entries with auto_importance=True get rescored
    |       Base score from record_type (6-7 for lessons, 4-5 for facts)
    |       +2 for source_material/confirmed grounding
    |       +2 if access velocity >= 1/day
    |       -1 if > 7 days old with zero accesses
    |       Clamped to [1, 10]
    |
    ├── 6. SALIENCE DECAY
    |       LTM entries stale > 90 days → salience -= 0.02 per cycle
    |       Accessed but unvalidated → faster decay (0.05)
    |       Floor: 0.3
    |
    ├── 7. CUSTOM HOOKS
    |       Application-registered steps (e.g., nmem-sym clustering)
    |       register_full_cycle_step(name, fn)
    |
    ├── 8. CONFLICT RESOLUTION
    |       Process pending conflicts
    |       Grounding > Trust > Recency > Importance
    |
    ├── 9. KNOWLEDGE LINKING
    |       Build associative links between entries
    |       Temporal, causal, entity-shared, tag-shared
    |
    └── 10. CURIOSITY DECAY
            Reduce scores on old curiosity signals
```

## Nightly Synthesis (After Full Cycle)

```
nightly_synthesis()
    |
    ├── PATTERN SYNTHESIS
    |       Extract cross-cutting themes from recent journal
    |       LLM identifies patterns across multiple agents
    |       Store as LTM entries with record_type="summary"
    |
    └── RETROSPECTIVE VALIDATION
            Retrieve past lessons from LTM
            Test against recent evidence
            Validated lessons → LTP (confirmed grounding)
            Disputed lessons → flag for review
            Budget: max 5 LLM calls per night
```

## Custom Hook Registration

```python
# nmem-sym registers clustering as a full-cycle step
consolidator.register_full_cycle_step("symbol_clustering", cluster_fn)

# nmem-sym registers dreamstate as a nightly step
consolidator.register_nightly_step("symbol_dreamstate", dreamstate_fn)
```

## ConsolidationStats

| Counter | Purpose |
|---------|---------|
| `expired_deleted` | Journal entries archived |
| `expired_promoted` | Expired entries promoted to LTM |
| `promoted_to_ltm` | Journal → LTM promotions |
| `promoted_to_shared` | LTM → Shared promotions |
| `duplicates_merged` | Dedup merge operations |
| `auto_importance_rescored` | Entries with updated scores |
| `conflicts_auto_resolved` | Conflicts resolved automatically |
| `conflicts_needs_review` | Conflicts requiring human review |
| `lessons_validated` | Retrospective validations |
| `lessons_disputed` | Retrospective disputes |
| `salience_decayed` | LTM entries with reduced salience |
| `curiosity_decayed` | Curiosity signals reduced |
| `patterns_synthesized` | New patterns found |
| `links_created` | Knowledge links built |
| `duration_seconds` | Total cycle time |

## Links to Other Components

- **journal.py** (Journal): expiry, promotion, dedup
- **ltm.py** (LTM): salience decay, shared promotion, dedup
- **shared.py** (Shared): entries created from LTM promotion
- **conflicts.py** (Conflicts): resolution runs during full cycle
- **links.py** (Links): knowledge links built during full cycle
- **compression.py** (Compression): LLM merging of duplicates
- **cognitive.py** (Cognitive): curiosity decay
- **bridge.py** (nmem-sym): clustering and dreamstate registered as hooks

## Key Config

```
consolidation.enabled = true
consolidation.interval_hours = 6
consolidation.similarity_merge_threshold = 0.85
consolidation.micro_cycle_cooldown_minutes = 5
consolidation.nightly_synthesis_hour_utc = 23
consolidation.nightly_synthesis_min_entries = 10
```
