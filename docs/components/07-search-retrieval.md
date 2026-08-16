# Search & Retrieval

Hybrid search combining vector similarity, full-text search, and optional recency weighting. Results carry recognition signals (KNOWN/FAMILIAR/UNCERTAIN) so the LLM knows how much to trust each piece of retrieved context.

## Modules

| Module | Purpose |
|--------|---------|
| `search.py` | Hybrid search algorithm, recognition signal computation, context thread assignment, passage extraction |
| `prompt.py` | PromptBuilder: token-budgeted context assembly from all tiers |

## Search Algorithm

```
query text
    |
    ├── 1. EMBED query → 384-dim vector
    |
    ├── 2. VECTOR SEARCH (primary signal)
    |       pgvector <=> cosine distance
    |       Weight: 0.6 (configurable)
    |
    ├── 3. FTS BOOST (additive)
    |       PostgreSQL: ts_rank on content_tsv
    |       Capped at 0.1 × fts_weight
    |       Weight: 0.4 (configurable)
    |
    ├── 4. RECENCY FACTOR (optional)
    |       Exponential decay: exp(-days / halflife × ln(2))
    |       Half-life: 30 days (configurable)
    |       Weight: 0.0 (disabled by default)
    |
    └── 5. COMBINED SCORE
            score = vector_weight × vec_score
                  + fts_weight × fts_boost
                  + recency_weight × recency_factor

Cross-tier search:
    Run in parallel across journal, ltm, shared, entity, policy
    Merge and rank by score
    Deduplicate by content similarity
```

## Recognition Signals

Every search result carries a recognition signal computed from multiple factors:

| Signal | Score Range | Meaning |
|--------|------------|---------|
| KNOWN | >= 0.6 | High confidence -- well-grounded, frequently accessed |
| FAMILIAR | >= 0.3 | Moderate confidence -- some evidence or access history |
| UNCERTAIN | < 0.3 | Low confidence -- inferred, rarely accessed, or disputed |

**Score Components:**

| Factor | Weight | Bonus |
|--------|--------|-------|
| Grounding: confirmed | 0.4 | +0.40 |
| Grounding: source_material | 0.35 | +0.35 |
| Grounding: inferred | 0.1 | +0.10 |
| Grounding: disputed | -0.2 | -0.20 |
| Access count >= 5 | | +0.20 |
| Access count >= 2 | | +0.10 |
| Updated <= 7 days ago | | +0.15 |
| Updated <= 30 days ago | | +0.05 |
| Multi-agent (2+ agents) | | +0.15 |
| Salience (LTM only) | 0.1 | +0.10 × salience |

## Prompt Building

`PromptBuilder.build()` assembles all tiers into a token-budgeted context:

```
Token budget allocation (proportional):
    Policy:  10%
    Shared:  15%
    LTM:     30%
    Journal: 20%
    Working: 10%
    Entity:  15%

Output: PromptContext
    .full_injection → "## Agent Memory\n### Policy\n...\n### Shared\n..."
    .token_estimate → rough chars/4
    .section_tokens → per-section breakdown
```

Sections are fetched in parallel. If total exceeds budget, sections are proportionally truncated.

## Briefing

`memory.briefing()` generates a session-start summary:
- Recent high-importance entries
- Recognition breakdown (how many KNOWN vs FAMILIAR vs UNCERTAIN)
- Entity context if entity_type/entity_id provided
- Configurable max tokens

## Links to Other Components

- **memory.py** (MemorySystem): `search()` and `briefing()` are the main entry points
- **tiers/** (All Tiers): each tier's `search()` and `build_prompt()` feeds into cross-tier results
- **links.py** (Knowledge Links): `expand_search_results()` adds linked entries
- **bridge.py** (nmem-sym): `augment_search()` injects symbol context alongside memory results

## Key Config

```
search.vector_weight = 0.6
search.fts_weight = 0.4
search.recency_weight = 0.0
search.recency_halflife_days = 30
search.min_vector_score = 0.0

recognition.known_threshold = 0.6
recognition.familiar_threshold = 0.3
```
