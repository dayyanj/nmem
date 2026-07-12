# Compression & Importance

Write-time processing that shapes how entries enter memory. Compression uses an LLM to distill verbose content into factual statements. Importance scoring classifies how valuable an entry is, determining its promotion trajectory.

## Modules

| Module | Purpose |
|--------|---------|
| `compression.py` | LLM-based content distillation at write time, duplicate merging at consolidation time |
| `importance.py` | Heuristic importance scoring (1-10) based on entry type, keywords, and tool usage patterns |

## Compression

```
Agent writes: "I looked at the patient charts for rooms 201-205 and noticed
that three of the five patients had medication timing issues where their
evening doses were being administered 2 hours late on Tuesday and Wednesday..."
    |
    v
compress_content(llm, title, content, max_chars=200, max_tokens=128)
    |
    ├── LLM prompt: "Distill into factual statements.
    |       Preserve names, dates, numbers, decisions."
    |
    └── Result: "3/5 patients rooms 201-205 had evening medication
                 delays (2h late) on Tue-Wed."

Fallback: If LLM fails → truncate to max_chars
```

Compression is opt-in per write (`compress=True`, default for journal/LTM).

## Duplicate Merging (Consolidation)

```
Two similar LTM entries detected (similarity >= 0.85)
    |
    v
merge_duplicates(llm, entries, max_chars=300)
    |
    ├── LLM prompt: "Merge these entries into one,
    |       preserving all unique facts."
    |
    └── Result: Combined entry with unified content
        Winner keeps highest importance
        Loser marked superseded
```

## Importance Scoring

```
classify_importance(content, entry_type=None, rules=None) → int [1-10]
    |
    ├── PRIORITY 1: Entry-type rules (if entry_type matches)
    |       deployment    → 7
    |       incident      → 8
    |       lesson_learned → 6
    |       observation   → 3
    |
    ├── PRIORITY 2: Keyword rules (scan content)
    |       "security", "production", "breaking" → 7
    |       "fixed", "implemented", "deployed"  → 5
    |       "read", "checked", "looked at"      → 2
    |
    └── DEFAULT: 3

Auto-importance rescoring (at consolidation):
    Base from record_type priors:
        lessons/rules: 6-7
        facts: 4-5
    Grounding bonus:
        source_material/confirmed: +2
        inferred: +0
        disputed: -1
    Access velocity:
        >= 1 access/day: +2
        >= 0.3 access/day: +1
    Staleness penalty:
        > 7 days, zero accesses: -1
    Clamped to [1, 10]
```

## Tool Importance (Claude Code)

```
classify_tool_importance(tool_name, tool_input=None) → int | None
    |
    ├── Skip (return None): Read, Glob, Grep (too noisy)
    ├── Edit, Write: 5
    ├── Bash with deploy/push: 7
    ├── Bash general: 4
    └── Default: 3
```

## Links to Other Components

- **journal.py** (Journal): compression on `add()`, importance on auto-scoring
- **ltm.py** (LTM): compression on `save()`
- **consolidation.py** (Consolidation): auto-importance rescoring, duplicate merging
- **providers/llm/** (LLM Providers): compression uses the configured LLM provider

## Key Config

```
llm.compression_max_chars = 200
llm.compression_max_tokens = 128
llm.synthesis_max_tokens = 1024

importance.enabled = true
importance.llm_rescore_enabled = false
importance.rescore_batch_size = 50
```
