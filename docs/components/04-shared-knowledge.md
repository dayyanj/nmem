# Shared Knowledge (Tier 4)

Cross-agent canonical facts. Knowledge that has been validated by multiple agents and represents organizational ground truth. Any agent can write, all agents can read. Versioned with a full change-log.

## Modules

| Module | Purpose |
|--------|---------|
| `tiers/shared.py` | SharedTier: save with change-log, search, get by key, list, prompt building |

## Data Flow

```
Promotion from LTM OR direct save
    |
    v
shared.save(key, content, category, agent_id, importance, ...)
    |
    ├── 1. UPSERT by (key, project_scope)
    |       New: insert with version=1
    |       Existing: increment version, append to change_log
    |           change_log += {agent_id, timestamp, old_content}
    |
    ├── 2. CONFLICT SCAN
    |       Check for contradictions in shared tier
    |
    ├── 3. DEFAULT GROUNDING = "confirmed"
    |       Shared entries are considered authoritative
    |
    └── 4. EMBEDDING + FTS INDEX

SharedEntry stored with:
    confirmed = True
    version = N
    change_log = [{who, when, old_value}, ...]
```

## Change-Log

Every update appends to a JSON change-log:

```json
[
  {"agent_id": "researcher", "timestamp": "2024-03-15T10:30:00Z", "old_content": "..."},
  {"agent_id": "writer", "timestamp": "2024-03-16T14:22:00Z", "old_content": "..."}
]
```

This provides full audit trail of who changed what and when.

## Belief Revision

Shared entries support the same superseded_by_id mechanism as LTM:
- When a conflict is resolved, the loser is marked superseded
- The winner becomes the canonical version
- History is preserved, not deleted

## Links to Other Components

- **ltm.py** (LTM): entries promoted when multi-agent criteria met
- **consolidation.py** (Consolidation): shared promotion runs during full cycle
- **conflicts.py** (Conflicts): conflict scanning on every write
- **search.py** (Search): hybrid search across shared tier
- **prompt.py** (Prompt Builder): shared section in `PromptContext.shared`

## Database Tables

| Table | Purpose |
|-------|---------|
| `nmem_shared_knowledge` | key, content, category, created_by, last_updated_by, confirmed, importance, embedding, content_tsv, version, change_log, superseded_by_id |

Unique constraint: (key, project_scope)
Indexes: category, importance, status, updated_at

## Key Config

```
shared.max_chars_in_prompt = 1500
```
