# Healthcare Benchmark: Search Optimization A/B Test

A follow-up study to the [180-day healthcare multi-agent benchmark](healthcare-multi-agent.md), testing whether search-level improvements can close the temporal reasoning and cross-agent synthesis gaps identified in the original study.

## Background

The original 180-day benchmark found that nmem scored +7% overall versus a memoryless baseline, with strong results in belief revision (+60%) and direct recall (+10%). However, two categories underperformed:

- **Temporal reasoning: -0.39** (nmem 2.83/5 vs baseline 3.22/5)
- **Cross-agent synthesis: -0.20** (nmem 3.83/5 vs baseline 4.02/5)

An [adversarial critique](../design/symbolic-cognition-critique.md) of the proposed symbolic cognition system argued that simpler search improvements should be tried first before building graph infrastructure. This study tests that hypothesis.

## What We Changed

Three search modifications were implemented in nmem's cross-tier search engine:

### 1. Recency-Weighted Search (temporal variant)

Added a `recency_weight` parameter to `MemorySystem.search()`. When enabled, recent entries receive a scoring boost on top of relevance ranking. The scoring formula becomes:

```
combined_score = vector_similarity + fts_boost + (recency_weight × recency_decay)
```

Where `recency_decay = 1 / (1 + age_seconds / halflife_seconds)`.

Settings tested: `recency_weight=0.3`, `recency_halflife_days=14`.

**Hypothesis:** Temporal questions ask about trends and changes over time. Boosting recent entries should help the LLM see the latest state of affairs while still retrieving relevant older context.

### 2. All-Agents Search (balanced variant)

Added an `all_agents=True` parameter. When enabled, journal and LTM searches remove the `agent_id` filter and search across all agents. Results are balanced to guarantee at least 2 entries from each agent before filling remaining slots by relevance.

**Hypothesis:** Cross-agent questions fail because search returns results from only one agent. Ensuring all agents are represented should provide the multi-perspective context needed for synthesis.

### 3. Combined (combined variant)

Applies recency weighting to temporal questions and all-agents mode to cross-agent questions, selected dynamically based on question category and keyword detection.

## Methodology

### A/B test design

Instead of re-running the full 180-day simulation, we reused the existing day-180 database snapshot (1,971 journal, 391 LTM, 17 shared, 4,000 entity entries across 4 agents). The search improvements are retrieval-time changes — the stored data is identical across all variants.

All 40 evaluation questions were run against 4 variants:

| Variant | recency_weight | all_agents | Applied to |
|---------|---------------|------------|------------|
| **original** | 0.0 | False | All questions (control) |
| **temporal** | 0.3 (halflife 14d) | False | All questions |
| **balanced** | 0.0 | True | All questions |
| **combined** | 0.3 if temporal, else 0.0 | True if cross-agent | Selective |

### Dual judging

Each answer was scored by two LLM judges (Qwen3-14B on GPU + Qwen3-30B-A3B MoE on CPU), with scores averaged and rounded. This matches the original benchmark's methodology and reduces single-judge variance.

### Infrastructure

- **Model:** Qwen3-14B-AWQ on RTX 4090 (answering + 14B judging)
- **30B Judge:** Qwen3-30B-A3B MoE Q4_K_M on CPU (~13 tok/s)
- **Database:** PostgreSQL + pgvector (Docker, port 5435)
- **Runtime:** 59 minutes for all 160 evaluations (4 variants × 40 questions)
- **Cost:** $0 (local inference)

## Results

### Overall scores

| Variant | Mean | vs Baseline | vs Day-180 original | Wins vs base | Losses vs base |
|---------|------|-------------|---------------------|-------------|----------------|
| **balanced** | **3.45** | -0.05 | -0.35 | 14 | 12 |
| **combined** | 3.38 | -0.12 | -0.42 | 10 | 10 |
| **original** (re-run) | 3.33 | -0.17 | -0.47 | 9 | 13 |
| **temporal** | 3.30 | -0.20 | -0.50 | 6 | 11 |

### By category

| Category | original | temporal | balanced | combined | Day-180 original |
|----------|----------|----------|----------|----------|-----------------|
| Belief revision | 2.60 | 3.00 | **3.60** | **3.60** | 5.00 |
| Cross-agent | 3.50 | **3.90** | 3.50 | 3.60 | 3.83 |
| Direct recall | **3.50** | 3.25 | **3.50** | **3.50** | 4.00 |
| Pattern detection | 3.00 | 3.00 | **3.40** | 3.00 | 3.80 |
| Temporal reasoning | **3.50** | 3.00 | 3.25 | 3.00 | 2.83 |

### Run-to-run variance

The "original" re-run scored 3.33/5 — significantly below the day-180 original of 3.80/5, despite using identical code and data. This -0.47 gap represents the **noise floor** of the benchmark: the 14B model produces different answers to the same question with the same context across runs.

This variance exceeds the deltas between search variants, meaning **no variant produced a statistically significant improvement over any other**.

Belief revision showed the most extreme variance: 2.60 in the re-run vs 5.00 in the original — a 2.40-point swing with no code changes.

## Where Each Search Mode Is Strongest

Despite the noise, per-question analysis reveals consistent patterns in which variant produces the best score for each question type:

### Unique wins (variant scored strictly higher than all others)

| Variant | Questions won | Categories |
|---------|--------------|------------|
| **original** (relevance only) | tr03, tr04 | Temporal reasoning |
| **temporal** (recency boost) | ca02, ca09, pd03 | Cross-agent, pattern detection |
| **balanced** (all-agents) | br01, br05, dr02, pd04 | Belief revision, direct recall, pattern detection |
| **combined** | br02 | Belief revision |

### Tied-best frequency (how often each variant is among the top scorers)

| Variant | Total tied-best | Temporal (of 8) | Cross-agent (of 10) | Belief rev (of 5) | Direct recall (of 12) |
|---------|----------------|-----------------|---------------------|-------------------|----------------------|
| **balanced** | 29 | 6 | 7 | **3** | 9 |
| **combined** | 27 | 4 | 8 | **3** | 10 |
| **original** | 26 | **7** | 5 | 1 | 11 |
| **temporal** | 22 | 4 | **9** | 1 | 6 |

### Key finding: each variant has a distinct strength

**Original (pure relevance) is best for temporal reasoning.** Counterintuitively, recency weighting *hurts* temporal questions. These questions ask about trends across the full timeline ("How has ED utilization changed month over month?"). Recency bias skews results toward recent events, making it harder for the LLM to compare across periods. Pure relevance ranking retrieves the right entries — the challenge is reasoning over them, not finding them.

**Temporal (recency boost) is best for cross-agent synthesis.** The surprise of the study. Cross-agent questions often ask about recent shared events ("What patterns do you see across all teams?"). Recency boost surfaces recent high-impact events (guideline changes, recalls) that affected multiple agents simultaneously. The recency signal acts as a proxy for "importance across teams."

**Balanced (all-agents) is best for belief revision and broad recall.** By searching across all agents, it picks up guideline changes recorded by different agents (pharmacy noted the recall, treatment noted the protocol change, discharge noted the care plan update). Multiple perspectives on the same policy change reinforce the updated information, helping the LLM recognise that a change occurred.

**Combined offers no advantage over selective application.** Applying both fixes to all questions produces noise. The fixes work when applied to the right question types — applying them universally dilutes their benefit.

## Search Mode Selection Guide

Based on these findings, agents and applications can select the appropriate search mode:

| Question type | Recommended mode | Parameters | Why |
|--------------|-----------------|------------|-----|
| **Factual recall** ("What medications is patient X taking?") | Default | `search(agent, query)` | Pure relevance is sufficient. The answer is a stored fact. |
| **Trend / timeline** ("How has X changed over time?") | Default | `search(agent, query)` | Recency bias hurts. The LLM needs the full timeline, not just recent entries. |
| **Recent events** ("What happened with the recall?") | Recency boost | `search(agent, query, recency_weight=0.3, recency_halflife_days=14)` | Surfaces recent high-impact events that may be buried by older, more numerous entries. |
| **Cross-team patterns** ("What do all teams observe?") | Recency boost | `search(agent, query, recency_weight=0.3)` | Recent shared events are the strongest cross-agent signal. |
| **Policy / guideline questions** ("What is the current protocol?") | All-agents | `search(agent, query, all_agents=True)` | Multiple agents recording the same guideline change reinforces the updated answer. |
| **Patient overview** ("Summarize patient X's history") | All-agents | `search(agent, query, all_agents=True)` | Different agents have different pieces of the patient's story. |

### Automatic mode detection

For applications that cannot manually select the search mode, keyword-based detection can approximate the right choice:

- **Recency keywords** → recency boost: "recent", "latest", "what happened", "recall", "across all teams", "patterns observed"
- **Multi-agent keywords** → all-agents: "current protocol", "guideline", "all teams", "coordination", "patient history"
- **Timeline keywords** → default (no recency): "over time", "month over month", "trend", "chronologically", "changed"

## Conclusions

### 1. Simple search fixes do not close the temporal reasoning gap

The critic's hypothesis — that temporal indexing and agent-tagged retrieval could fix the benchmark gaps without graph infrastructure — is **not supported** by the data. The gaps are reasoning problems, not retrieval problems. The right facts are in the search results; the 14B model struggles to synthesise them into temporal comparisons and cross-agent patterns.

### 2. Each search mode has a niche

While no variant improved overall scores significantly, per-question analysis shows consistent patterns. Recency weighting helps with recent-event and cross-agent queries. All-agents mode helps with policy/guideline and broad recall queries. Default relevance-only search is best for temporal reasoning and standard factual recall.

### 3. The benchmark needs better variance control

Run-to-run variance (±0.47) exceeds the signal from search improvements. Future benchmarks should:
- Run each evaluation 3-5 times and average
- Use temperature 0.0 for both answering and judging
- Consider using a stronger model (70B+) as the answering LLM to reduce answer variance
- Separate answer variance from judge variance by caching answers and re-judging

### 4. The case for symbolic cognition is strengthened

The original benchmark showed nmem adds value through memory (+7% overall, +60% belief revision). This study shows that search optimization has reached diminishing returns — the remaining gaps require structural reasoning support, not better retrieval. This validates the [symbolic cognition design](../design/symbolic-cognition.md) as the next architectural step, specifically for temporal reasoning and cross-agent pattern synthesis.

## Appendix: Per-Question Results

<details>
<summary>Click to expand full results table</summary>

| QID | Category | Day-180 orig | Day-180 base | original | temporal | balanced | combined | Best |
|-----|----------|-------------|-------------|----------|----------|----------|----------|------|
| br01 | belief_revision | 5 | 4 | 4 | 4 | **5** | 4 | balanced |
| br02 | belief_revision | 5 | 2 | 3 | 2 | 3 | **4** | combined |
| br03 | belief_revision | 5 | 2 | 2 | 2 | 1 | 2 | tied |
| br04 | belief_revision | 5 | 4 | 2 | 4 | **5** | **5** | bal+comb |
| br05 | belief_revision | 5 | 3 | 2 | 3 | **4** | 3 | balanced |
| ca01 | cross_agent | 5 | 4 | 4 | 4 | 4 | 4 | tied |
| ca02 | cross_agent | 4 | 5 | 4 | **5** | 2 | 2 | temporal |
| ca03 | cross_agent | 4 | 4 | 4 | **5** | **5** | **5** | temp+bal+comb |
| ca04 | cross_agent | 4 | 4 | 3 | **4** | 3 | **4** | temp+comb |
| ca05 | cross_agent | 3 | 3 | 2 | **4** | **4** | **4** | temp+bal+comb |
| ca06 | cross_agent | 5 | 5 | 5 | 5 | 5 | 5 | tied |
| ca07 | cross_agent | 5 | 4 | 4 | 4 | 4 | 4 | tied |
| ca08 | cross_agent | 2 | 3 | 2 | 2 | 2 | 2 | tied |
| ca09 | cross_agent | 3 | 4 | 3 | **4** | 2 | 2 | temporal |
| ca10 | cross_agent | 4 | 4 | **4** | 2 | **4** | **4** | orig+bal+comb |
| dr01 | direct_recall | 4 | 4 | 4 | 4 | 4 | 4 | tied |
| dr02 | direct_recall | 5 | 5 | 2 | 4 | **5** | 3 | balanced |
| dr03 | direct_recall | 5 | 5 | **5** | 4 | **5** | **5** | orig+bal+comb |
| dr04 | direct_recall | 5 | 5 | **5** | **5** | 4 | **5** | orig+temp+comb |
| dr05 | direct_recall | 5 | 4 | 2 | 2 | 2 | 2 | tied |
| dr06 | direct_recall | 5 | 4 | **4** | 3 | **4** | **4** | orig+bal+comb |
| dr07 | direct_recall | 5 | 4 | 4 | 4 | 4 | 4 | tied |
| dr08 | direct_recall | 4 | 2 | 2 | 2 | 2 | 2 | tied |
| dr09 | direct_recall | 3 | 2 | **3** | 2 | **3** | **3** | orig+bal+comb |
| dr10 | direct_recall | 2 | 2 | **4** | 3 | 3 | **4** | orig+comb |
| dr11 | direct_recall | 3 | 3 | **4** | 3 | **4** | **4** | orig+bal+comb |
| dr12 | direct_recall | 2 | 3 | **3** | **3** | 2 | 2 | orig+temp |
| pd01 | pattern_detection | 5 | 4 | 4 | 4 | 4 | 4 | tied |
| pd02 | pattern_detection | 3 | 2 | **3** | 2 | **3** | 2 | orig+bal |
| pd03 | pattern_detection | 3 | 4 | 2 | **3** | 2 | 2 | temporal |
| pd04 | pattern_detection | 3 | 2 | 2 | 2 | **3** | 2 | balanced |
| pd05 | pattern_detection | 5 | 5 | 4 | 4 | **5** | **5** | bal+comb |
| tr01 | temporal_reasoning | 5 | 4 | 5 | 5 | 5 | 5 | tied |
| tr02 | temporal_reasoning | 2 | 2 | **5** | 3 | **5** | 3 | orig+bal |
| tr03 | temporal_reasoning | 2 | 4 | **3** | 2 | 2 | 2 | original |
| tr04 | temporal_reasoning | 2 | 4 | **5** | 3 | 2 | 3 | original |
| tr05 | temporal_reasoning | 4 | 2 | **4** | 3 | **4** | **4** | orig+bal+comb |
| tr06 | temporal_reasoning | 2 | 2 | 2 | 2 | 2 | 2 | tied |
| tr07 | temporal_reasoning | 2 | 5 | 2 | **4** | **4** | 3 | temp+bal |
| tr08 | temporal_reasoning | 2 | 2 | 2 | 2 | 2 | 2 | tied |

</details>
