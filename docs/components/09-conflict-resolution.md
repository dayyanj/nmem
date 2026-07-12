# Conflict Resolution

Belief revision system that detects contradictions at write time and resolves them during consolidation. Uses a grounding hierarchy where source material outranks inference, agent trust scores break ties, and recency is the final tiebreaker.

## Modules

| Module | Purpose |
|--------|---------|
| `conflicts.py` | Detection via text+vector similarity, resolution via grounding > trust > recency > importance priority, auto-resolve or flag for review |

## Detection (On Every Write)

```
Agent writes to journal, LTM, shared, or entity
    |
    v
scan_conflicts(content, embedding, agent_id, target_table, target_id, ...)
    |
    ├── 1. TEXT SIMILARITY (Jaccard)
    |       Tokenize both texts, compute overlap
    |       Threshold: 0.7 (same topic?)
    |
    ├── 2. VECTOR SIMILARITY (cosine)
    |       Compare embeddings
    |       Threshold: 0.85 (aligned meaning?)
    |
    └── 3. CONFLICT TEST
            If text_sim >= 0.7 AND vec_sim < 0.85:
                Same topic but different meaning → CONFLICT
                Store in nmem_memory_conflicts
                Status: "pending"
```

## Resolution (During Consolidation)

```
resolve_conflict(conflict_id, config)
    |
    ├── Load both records
    |
    ├── PRIORITY 1: GROUNDING RANK
    |       source_material (4) > confirmed (3) > inferred (2) > disputed (1)
    |       If winner >= 1 level above loser → AUTO-RESOLVE
    |           Winner stays active
    |           Loser marked superseded
    |           Conflict status = "auto_resolved"
    |
    ├── PRIORITY 2: AGENT TRUST (if grounding tied)
    |       Config-defined trust scores per agent
    |       Default trust: 0.5
    |       Higher trust wins
    |
    ├── PRIORITY 3: RECENCY (if trust tied)
    |       More recently updated wins
    |
    ├── PRIORITY 4: IMPORTANCE (if recency tied)
    |       Higher importance wins
    |
    └── If no clear winner after all priorities:
            Status = "needs_review"
            Requires human intervention
```

## Grounding Hierarchy

```
Rank 4: source_material  (from authoritative source)
Rank 3: confirmed        (validated by evidence/multiple agents)
Rank 2: inferred         (agent's reasoning)
Rank 1: disputed         (contradicted by evidence)
```

Auto-resolve threshold: winner must be >= 1 rank above loser.

## Agent Trust Scores

```toml
[belief_revision]
agent_trust = {
    "orchestrator" = 0.8,
    "researcher" = 0.7,
    "writer" = 0.6,
    "critic" = 0.7,
    "coder" = 0.7
}
default_trust = 0.5
```

## Links to Other Components

- **journal.py / ltm.py / shared.py / entity.py** (All Tiers): `scan_conflicts()` called on every write
- **consolidation.py** (Consolidation): `resolve_conflict()` runs during full cycle step 8
- **memory.py** (MemorySystem): conflicts exposed via `memory.conflicts` property
- **cli** (CLI): `nmem conflicts list --pending` for manual review
- **mcp** (MCP): `memory_conflict_list()` tool for Claude Code

## Database Tables

| Table | Purpose |
|-------|---------|
| `nmem_memory_conflicts` | record_a_table, record_a_id, record_b_table, record_b_id, agent_a, agent_b, similarity_score, description, status (pending/auto_resolved/needs_review/resolved) |

Indexes: status, created_at

## Key Config

```
belief_revision.enabled = true
belief_revision.text_similarity_threshold = 0.7
belief_revision.vector_divergence_threshold = 0.85
belief_revision.scan_candidates_limit = 10
belief_revision.grounding_priority = ["source_material", "confirmed", "inferred", "disputed"]
belief_revision.auto_resolve_grounding_gap = 1
belief_revision.agent_trust = {}
belief_revision.default_trust = 0.5
```
