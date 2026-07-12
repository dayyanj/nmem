# Entity Memory (Tier 5)

Collaborative workspace organized by business objects. Multiple agents contribute observations, judgments, and evidence about the same entity (customer, patient, product, etc.). Grounding levels track how well-established each record is.

## Modules

| Module | Purpose |
|--------|---------|
| `tiers/entity.py` | EntityTier: save with permission checking, grounding lifecycle, search, get by entity, prompt building |

## Data Flow

```
Agent observes something about an entity
    |
    v
entity.save(entity_type, entity_id, entity_name, agent_id, content, ...)
    |
    ├── 1. PERMISSION CHECK
    |       entity.write_permissions[entity_type] = [allowed_agent_ids]
    |       If configured and agent not in list → PermissionError
    |       If not configured → all agents allowed
    |
    ├── 2. CONFIDENCE ENFORCEMENT
    |       Judgments: confidence forced < 1.0
    |       Evidence: status = "validated"
    |       Others: status = "draft"
    |
    ├── 3. EMBEDDING + FTS INDEX
    |
    └── 4. AUTO-JOURNAL (optional)
            If entity.auto_journal_on_search:
                When agent searches entities and finds results,
                create a journal entry summarizing what was found

EntityRecord stored with:
    confidence = 0.8 (default, tunable)
    evidence_refs = [] (audit trail)
    tags = [] (searchable labels)
```

## Grounding Lifecycle

Entity records move through grounding levels:

```
source_material ←→ inferred ←→ confirmed ←→ disputed

entity.update_grounding(record_id, grounding, evidence_ref, agent_id)
    |
    ├── Transitions are explicit (not automatic)
    ├── Each transition appends to evidence_refs:
    |       {agent_id, grounding, timestamp, evidence_ref}
    └── Full audit trail of who changed grounding and why
```

## Permission Model

```toml
[entity]
write_permissions = {
    "patient" = ["doctor", "nurse"],
    "product" = ["product_manager", "engineer"]
}
```

Empty dict or missing key = all agents can write.

## Auto-Journal

When configured, entity searches automatically create journal entries:

```
Agent searches for "Patient Smith"
    → 3 entity records found (score > min_score)
    → Journal entry created: "Reviewed Patient Smith records: ..."
    → Provides audit trail of who looked at what
```

## Links to Other Components

- **search.py** (Search): hybrid search within entity type
- **prompt.py** (Prompt Builder): entity section in `PromptContext.entity`
- **journal.py** (Journal): auto-journal on search creates journal entries
- **conflicts.py** (Conflicts): conflict scanning on writes

## Database Tables

| Table | Purpose |
|-------|---------|
| `nmem_entity_memory` | entity_type, entity_id, entity_name, agent_id, record_type, content, confidence, embedding, content_tsv, evidence_refs, tags, version, superseded_by |

Indexes: (entity_type, entity_id), agent_id, (entity_type, status), record_type

## Key Config

```
entity.max_chars_in_prompt = 2000
entity.write_permissions = {}
entity.auto_journal_on_search = false
entity.auto_journal_min_results = 1
entity.auto_journal_min_score = 0.5
entity.auto_journal_importance = 3
```
