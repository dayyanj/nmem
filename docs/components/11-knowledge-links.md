# Knowledge Links

Associative graph between memory entries. Links connect related entries across tiers via shared entities, shared tags, temporal proximity, causal relationships, and synthesized patterns. Links expand search results to surface context the query alone wouldn't find.

## Modules

| Module | Purpose |
|--------|---------|
| `links.py` | KnowledgeLinkEngine: build links by type, retrieve linked entries, expand search results |

## Link Types

| Type | How Created | Strength |
|------|-------------|----------|
| `shared_entity` | Both entries reference the same entity (via tags matching entity records) | 0.8 |
| `shared_tag` | Share non-meta tags (excluding common tags like dates) | 0.6 |
| `temporal` | Journal entries within N minutes in same session/thread | 0.7 |
| `causal` | Decision entry followed by outcome entry (entry_type patterns) | 0.9 |
| `pattern` | Nightly synthesis detected cross-entry pattern | 0.7 |

## Link Building

```
consolidation full cycle → build_links()
    |
    ├── 1. SHARED ENTITY LINKS
    |       For each entity record, find LTM/journal entries
    |       with matching entity tags
    |       Create bidirectional links
    |
    ├── 2. SHARED TAG LINKS
    |       Entries with 2+ shared tags (non-meta)
    |       Strength proportional to tag overlap
    |
    ├── 3. TEMPORAL LINKS
    |       Journal entries within temporal_window_minutes
    |       Same agent, same session or thread
    |
    ├── 4. CAUSAL LINKS
    |       Decision → outcome entry type pairs
    |       Based on entry_type sequencing
    |
    └── 5. PATTERN LINKS
            Created by nightly synthesis
            LLM identifies cross-entry patterns
```

## Search Expansion

```
Original search returns 5 results
    |
    v
expand_search_results(results, max_expansion=3, min_strength=0.5)
    |
    ├── For each result: get_linked(entry_id, tier)
    ├── Score linked entries: original_score × link_strength × 0.5
    ├── Add up to 3 expansion results
    ├── Mark with metadata["expanded_via_link"] = True
    └── Return merged + ranked list

Use case: Query "patient medication schedule"
    → Direct hit: medication protocol document
    → Expansion via shared_entity: recent nurse observation about patient
    → Expansion via causal: decision to change dosage + outcome
```

## Links to Other Components

- **consolidation.py** (Consolidation): `build_links()` runs during full cycle step 9
- **search.py** (Search): `expand_search_results()` adds linked entries to search results
- **memory.py** (MemorySystem): link engine accessible via `memory.links`

## Database Tables

| Table | Purpose |
|-------|---------|
| `nmem_knowledge_links` | source_id, source_tier, target_id, target_tier, link_type, strength, evidence |

Indexes: (source_id, source_tier), (target_id, target_tier), link_type

## Key Config

```
knowledge_links.enabled = true
knowledge_links.temporal_window_minutes = 30
knowledge_links.min_shared_tags = 2
knowledge_links.search_expansion_enabled = true
knowledge_links.search_expansion_max = 3
knowledge_links.search_expansion_min_strength = 0.5
```
