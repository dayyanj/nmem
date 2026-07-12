# Journal (Tier 2)

Agent observations with a 30-day default lifespan. The system's short-term memory -- everything an agent notices, decides, or learns enters here first. High-importance entries get promoted to LTM; the rest expire and are archived.

## Modules

| Module | Purpose |
|--------|---------|
| `tiers/journal.py` | JournalTier: add with compression/dedup, search, recent, prompt building, batch add |

## Data Flow

```
Agent observation / decision / note
    |
    v
journal.add(agent_id, entry_type, title, content, importance, tags, ...)
    |
    ├── 1. COMPRESSION (if compress=True)
    |       LLM distills content into factual statements
    |       Preserves names, dates, numbers, decisions
    |       Max 200 chars (configurable)
    |
    ├── 2. DEDUP CHECK (24h window)
    |       Embedding similarity >= 0.92 → skip duplicate
    |       Same agent only
    |
    ├── 3. EMBEDDING
    |       Encode content → 384-dim vector
    |       Store in embedding column (pgvector HNSW)
    |
    ├── 4. IMPORTANCE SCORING
    |       If importance not provided → classify_importance()
    |       Heuristic: entry_type rules + keyword rules
    |       auto_importance = True (may be rescored later)
    |
    ├── 5. CONTEXT THREAD
    |       Assign to semantic cluster (0.8 similarity threshold)
    |       Groups related entries across time
    |
    ├── 6. FTS INDEX
    |       Populate content_tsv (PostgreSQL tsvector)
    |       Fire-and-forget async
    |
    └── 7. CONSOLIDATION SIGNAL
            If importance >= 7 → signal micro-cycle
            Consolidator may promote immediately

JournalEntry stored with:
    expires_at = now + 30 days (configurable)
    promoted_to_ltm = False
    access_count = 0
```

## Entry Types

| Type | Default Importance | Purpose |
|------|-------------------|---------|
| observation | 3 | What the agent noticed |
| decision | 5 | Choices made |
| task | 4 | Work performed |
| insight | 6 | Patterns recognized |
| lesson_learned | 6 | Experience-derived rules |
| incident | 8 | Problems encountered |
| deployment | 7 | System changes |

## Record Types & Grounding

| Record Type | Purpose |
|-------------|---------|
| evidence | Observed facts |
| fact | Established knowledge |
| judgment | Agent opinion |
| task | Work record |
| rule | Derived principle |
| summary | Compressed synthesis |

| Grounding | Meaning |
|-----------|---------|
| source_material | From authoritative source |
| inferred | Agent's reasoning |
| confirmed | Validated by evidence/other agents |
| disputed | Contradicted by evidence |

## Promotion Criteria

Journal entries promote to LTM when:
- `importance >= auto_promote_importance` (default 7), OR
- `access_count >= auto_promote_access_count` (default 5)

Checked during consolidation full cycles.

## Links to Other Components

- **working.py** (Working): `flush_to_journal()` creates journal entries
- **ltm.py** (LTM): entries promoted via consolidation
- **consolidation.py** (Consolidation): expiry, promotion, dedup, importance rescoring
- **compression.py** (Compression): `compress_content()` called at write time
- **importance.py** (Importance): `classify_importance()` for auto-scoring
- **search.py** (Search): hybrid vector+FTS search
- **conflicts.py** (Conflicts): `scan_conflicts()` checks for contradictions
- **links.py** (Links): temporal and causal links created between entries

## Database Tables

| Table | Purpose |
|-------|---------|
| `nmem_journal_entries` | Full entry with embedding, content_tsv, tags, record_type, grounding, status, project_scope |

Indexes: agent_id, entry_type, created_at, expires_at, importance, context_thread_id, record_type, status, project_scope

## Key Config

```
journal.default_expiry_days = 30
journal.auto_promote_importance = 7
journal.auto_promote_access_count = 5
journal.dedup_similarity_threshold = 0.92
```
