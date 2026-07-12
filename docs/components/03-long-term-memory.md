# Long-Term Memory (Tier 3)

Permanent per-agent knowledge. Facts, lessons, and rules that have proven important enough to keep. Entries arrive via promotion from journal or direct save. Salience decays over time -- unused knowledge fades but never disappears below a floor.

## Modules

| Module | Purpose |
|--------|---------|
| `tiers/ltm.py` | LTMTier: save with versioning/conflict scanning, search, batch save, prompt building |

## Data Flow

```
Promotion from journal OR direct save
    |
    v
ltm.save(agent_id, category, key, content, importance, ...)
    |
    ├── 1. UPSERT by (agent_id, key, project_scope)
    |       New entry: insert with salience=1.0
    |       Existing entry: create new version
    |           old.superseded_by_id = new.id
    |           old.status = "superseded"
    |
    ├── 2. COMPRESSION (if compress=True)
    |       LLM distillation, same as journal
    |
    ├── 3. CONFLICT SCAN (inline)
    |       Check for contradictions in same tier
    |       Uses text similarity + vector divergence
    |
    ├── 4. EMBEDDING + FTS INDEX
    |
    └─�� 5. SALIENCE RESET
            Updated entries get salience = 1.0
            (re-validation restores relevance)

LTMEntry stored with:
    salience = 1.0 (fresh)
    access_count = 0
    accessed_by_agents = []
    version = 1 (or incremented)
```

## Salience Decay

Replaces the old "confidence" concept. Salience represents how relevant/current a piece of knowledge is:

```
During consolidation full cycle:
    For each LTM entry:
        days_stale = (now - updated_at).days
        if days_stale > staleness_days (90):
            if entry was accessed but not validated:
                salience -= salience_decay_rate_accessed (0.05)
            else:
                salience -= salience_decay_rate (0.02)
            salience = max(salience, min_salience (0.3))
```

Salience affects search ranking -- low-salience entries still appear but rank lower.

## Versioning

LTM entries are versioned via forward pointers:

```
Version 1: "Patient protocol requires X"
    |
    superseded_by_id → Version 2: "Updated: protocol now requires Y"
                           |
                           superseded_by_id → Version 3: ...
```

Search excludes superseded entries by default. Pass `include_superseded=True` to see history.

## Shared Promotion

LTM entries promote to Shared when multiple agents find them valuable:

```
Criteria (checked during consolidation):
    accessed_by_agents count >= 2
    access_count >= 3
    importance >= 8
```

This is "social learning" -- agents vote with their queries.

## Links to Other Components

- **journal.py** (Journal): entries promoted from journal during consolidation
- **shared.py** (Shared): entries promoted when multi-agent criteria met
- **consolidation.py** (Consolidation): salience decay, dedup, shared promotion
- **conflicts.py** (Conflicts): inline conflict scanning on every write
- **search.py** (Search): hybrid search with salience weighting
- **compression.py** (Compression): LLM distillation on save
- **nmem-sym** (Symbol Graph): LTM entries are the source for triple extraction

## Database Tables

| Table | Purpose |
|-------|---------|
| `nmem_long_term_memory` | Full entry with embedding, content_tsv, salience, access_count, accessed_by_agents, version, superseded_by_id, source_journal_id |

Unique constraint: (agent_id, key, project_scope)
Indexes: agent_id+category, agent_id+importance, updated_at, context_thread_id, record_type, status

## Key Config

```
ltm.staleness_days = 90
ltm.salience_decay_rate = 0.02
ltm.salience_decay_rate_accessed = 0.05
ltm.min_salience = 0.3
ltm.shared_promote_importance = 8
ltm.shared_promote_min_agents = 2
ltm.shared_promote_min_access = 3
```
