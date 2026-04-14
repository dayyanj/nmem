# Healthcare Multi-Agent Memory Benchmark

<!-- i18n:start -->
**English** | [简体中文](../i18n/zh-hans/healthcare-multi-agent.md) | [日本語](../i18n/ja/healthcare-multi-agent.md) | [한국어](../i18n/ko/healthcare-multi-agent.md) | [Español](../i18n/es/healthcare-multi-agent.md) | [Português](../i18n/pt/healthcare-multi-agent.md) | [Français](../i18n/fr/healthcare-multi-agent.md) | [Deutsch](../i18n/de/healthcare-multi-agent.md) | [Русский](../i18n/ru/healthcare-multi-agent.md)
<!-- i18n:end -->


## Executive Summary

We built a 180-day simulation of a hospital ward with 4 AI agents (triage, treatment, discharge, pharmacy) processing 1,705 synthetic clinical encounters from 200 patients. Each agent writes to nmem's journal, and the system runs daily consolidation, weekly dreamstate, and biweekly nightly synthesis — compressing 6 months of clinical operations into a 4.6-hour benchmark run.

The benchmark tests whether cognitive memory improves agent answers over time compared to a baseline with the same LLM but no memory consolidation.

### Headline Findings

1. **Belief revision is nmem's killer feature.** After guideline changes, nmem scores 5.00/5 vs baseline 3.13/5 (+60%) — perfect answers on every belief revision question from day 120 onward.
2. **Overall improvement with a 14B model on consumer hardware.** nmem scores 3.84/5 vs baseline 3.60/5 (+7%) across 205 evaluations, using Qwen3-14B on a single RTX 4090.
3. **nmem wins 77 questions, loses 47, ties 81.** The wins concentrate in belief revision (+1.54 avg) and direct recall (+0.36 avg). Losses concentrate in temporal reasoning (-0.39 avg) where the 14B model's limited reasoning capacity is the likely bottleneck.
4. **The gap widens over time.** Day 1: +0.20, Day 30: +0.29, Day 120: +0.43 — as more guidelines change and knowledge consolidates, nmem's advantage grows.
5. **All features exercised at scale.** 361 LTM promotions, 56 dedup merges, 1,170 conflicts auto-resolved, 11 dreamstate patterns synthesized, 6,171 knowledge links — on a $0 inference budget (local vLLM).

---

## Methodology

### Research Questions

1. Does cognitive memory improve multi-agent clinical knowledge over 6 months?
2. Does nmem correctly revise beliefs when guidelines change?
3. Does the consolidation engine (promotion, dedup, synthesis) produce measurably better retrieval?
4. Can this work on modest infrastructure (14B model, single GPU)?

### Experimental Design

**Data source:** [Synthea](https://synthetichealth.github.io/synthea/) synthetic patient generator (Apache 2.0). 1,159 patients generated, top 200 by encounter density selected for the simulation window.

**Simulation window:** 180 days (2025-01-01 to 2025-06-29)

**Agents:**

| Agent | Role | Processes |
|-------|------|-----------|
| triage | Urgency assessment | All encounters — vitals, chief complaint, priority |
| treatment | Clinical decisions | Diagnoses, procedures, lab results |
| discharge | Care coordination | Care plans, follow-ups, readmission tracking |
| pharmacy | Medication management | Prescriptions, allergies, drug interactions |

**Injected scenarios** (hand-crafted events Synthea cannot generate):

| Day | Event | Feature Tested |
|-----|-------|----------------|
| 30 | Hypertension threshold lowered 140/90 → 130/80 | Belief revision |
| 45 | Metformin ER recall (NDMA contamination) | Cross-agent propagation |
| 50 | Diabetic patient readmitted within 30 days | Readmission pattern detection |
| 75 | Diabetes 2nd-line therapy: sulfonylureas → GLP-1 | Belief revision |
| 90 | Bactrim prescribed to patient with sulfa allergy | Conflict resolution |
| 110 | Lisinopril+potassium interaction: MODERATE → SEVERE | Belief revision |
| 120 | Opioid initial prescription: 7 days → 3 days | Policy change |
| 130 | Third Friday-discharge readmission pattern | Pattern detection |

**Daily simulation cycle:**

```
08:00  Ingest Synthea encounters → journal entries (LLM-compressed)
12:00  Inject hand-crafted scenarios
18:00  Consolidation full cycle (promote, dedup, rescore, belief revision, salience decay)
23:00  Weekly: dreamstate (convergence-gated). Biweekly: nightly synthesis + retrospective
```

Time is simulated via datetime monkeypatch — 180 days compress into 4.6 hours wall time.

**Evaluation:** 40 test questions across 5 categories, asked at days 1, 30, 60, 90, 120, 150, and 180. Each question scored 1-5 by Qwen3-14B judge against a rubric.

**Variants:**
- **nmem:** Agent searches memory via `mem.search()` + entity dossiers → answers with consolidated context
- **Baseline:** Same LLM, same question, but context is raw Synthea CSV data from the last 30 days (no consolidation, no entity dossiers, no belief revision)

### Infrastructure

| Component | Specification | Cost |
|-----------|--------------|------|
| **LLM** | Qwen3-14B-AWQ on vLLM | $0 (local) |
| **GPU** | NVIDIA RTX 4090 (24GB) | Consumer hardware |
| **Embedding** | all-MiniLM-L6-v2 (CPU) | $0 (local) |
| **Database** | PostgreSQL 16 + pgvector | Docker container |
| **Judge** | Qwen3-14B-AWQ (same GPU) | $0 (local) |
| **Total inference cost** | | **$0** |

All inference runs locally on a single consumer-grade GPU. No cloud API calls. The benchmark is fully reproducible on equivalent hardware.

---

## Results

### Overall

| Metric | nmem | Baseline | Delta |
|--------|------|----------|-------|
| **Mean score** | **3.84/5** | 3.60/5 | **+0.24 (+7%)** |
| Questions won | **77** | 47 | |
| Questions tied | 81 | 81 | |

### By Category

| Category | n | nmem | Baseline | Delta | W/L/T |
|----------|---|------|----------|-------|-------|
| **Belief revision** | 28 | **4.75** | 3.21 | **+1.54** | 23/1/4 |
| Direct recall | 74 | **4.09** | 3.73 | **+0.36** | 29/13/32 |
| Pattern detection | 21 | **3.48** | 3.33 | **+0.14** | 9/4/8 |
| Cross-agent synthesis | 46 | 3.83 | 4.02 | -0.20 | 6/13/27 |
| Temporal reasoning | 36 | 2.83 | 3.22 | -0.39 | 10/16/10 |

### Learning Curve (Score Over Time)

| Day | nmem | Baseline | Delta | Questions |
|-----|------|----------|-------|-----------|
| 1 | 4.40 | 4.20 | +0.20 | 5 |
| 30 | 4.36 | 4.07 | +0.29 | 14 |
| 60 | 3.76 | 3.66 | +0.10 | 29 |
| 90 | 3.68 | 3.57 | +0.11 | 37 |
| 120 | **3.83** | 3.40 | **+0.43** | 40 |
| 150 | 3.85 | 3.62 | +0.23 | 40 |
| 180 | 3.80 | 3.50 | +0.30 | 40 |

The dip at days 60-90 reflects the growing question set (harder questions become eligible) rather than degradation. The recovery at day 120 coincides with all four guideline changes being active — belief revision pulls the aggregate score up decisively.

### Belief Revision Deep Dive

After all guidelines have changed (day 120+), nmem answers every belief revision question correctly:

| Question | Day 120+ nmem | Day 120+ baseline | What changed |
|----------|---------------|-------------------|--------------|
| BP threshold (130/80) | 5/5 | 3.0/5 | Guideline day 30 |
| Diabetes 2nd-line (GLP-1) | 5/5 | 2.7/5 | Protocol day 75 |
| Metformin ER recall | 5/5 | 2.0/5 | Recall day 45 |
| Lisinopril+K severity | 5/5 | 4.0/5 | Upgrade day 110 |
| Opioid 3-day limit | 5/5 | 3.0/5 | Mandate day 120 |

**nmem: 5.00/5 vs baseline: 3.13/5 on belief revision after day 120.**

The baseline LLM either gives outdated answers (pre-guideline knowledge) or hedges. nmem retrieves the specific policy update from memory and answers definitively.

### Consolidation Engine Activity

| Metric | Total |
|--------|-------|
| Journal entries created | 3,446 |
| Entity memory updates | 3,446 |
| Promoted to LTM | 361 |
| Shared knowledge entries | 17 |
| Duplicates merged | 56 |
| Conflicts auto-resolved | 1,170 |
| Knowledge links created | 6,171 |
| Dreamstate patterns synthesized | 11 |
| Dreamstate cycles run | 27 |

### Final Memory State

| Tier | Count | Purpose |
|------|-------|---------|
| Journal | 1,971 | Active entries (others expired/promoted) |
| Long-term memory | 391 | Promoted important knowledge |
| Shared knowledge | 17 | Cross-agent canonical facts |
| Entity memory | 4,000 | Per-patient dossiers from all 4 agents |
| Knowledge links | 6,171 | Associative connections between entries |
| Memory conflicts | 1,401 | Detected and resolved contradictions |

---

## Analysis

### Where nmem excels

**Belief revision (+1.54):** This is the standout result. When medical guidelines change, nmem detects the contradiction at write time, resolves it during consolidation, and retrieves the updated policy at search time. The baseline LLM has no mechanism to track policy changes — it can only answer from its training data or the raw encounter log, neither of which distinguishes "current policy" from "old policy."

**Direct recall (+0.36):** Entity memory dossiers built by multiple agents give nmem richer per-patient context than raw CSV data. When asked "what medications is Patient A taking?", nmem retrieves a consolidated medication history from the pharmacy agent's entity memory rather than scanning encounter records.

**Pattern detection (+0.14):** Dreamstate synthesis detected 11 patterns from clinical data (respiratory season, readmission correlates). These patterns surfaced in search results for pattern-detection questions. The modest advantage reflects the 14B model's limited synthesis capability — larger models would likely extract richer patterns.

### Where nmem struggles

**Temporal reasoning (-0.39):** Questions like "track medication changes chronologically" or "how has ED utilization changed month-over-month" require ordered retrieval and temporal aggregation. nmem's hybrid search ranks by relevance, not chronology. The baseline's 30-day raw data window is naturally chronological, giving it an advantage on time-ordered questions. This suggests an opportunity for a temporal search mode in nmem.

**Cross-agent synthesis (-0.20):** Questions requiring synthesis across all four agents sometimes suffer from search returning too many results from one agent, diluting cross-agent signal. The modest loss may also reflect the 14B model's limited ability to synthesize across multiple context sources in a single response.

### Infrastructure implications

All results were achieved on a **single NVIDIA RTX 4090 (consumer GPU, ~$1,600)** running **Qwen3-14B-AWQ** (4-bit quantized). No cloud API calls were made. The entire benchmark — 180 days of simulation, 1,705 clinical events, 3,446 journal entries with LLM compression, 27 dreamstate cycles, and 205 dual-variant evaluations — ran for $0 in inference cost.

The 14B model is a known constraint on reasoning-heavy tasks. We expect larger models (30B+, 70B+) or cloud APIs (Claude, GPT-4) to show:
- Stronger temporal reasoning (the main weakness)
- Better cross-agent synthesis
- Richer dreamstate pattern extraction
- Even higher belief revision accuracy (already at 5.00/5 ceiling with 14B)

The fact that nmem achieves a net positive result with a 14B quantized model suggests the memory architecture is doing substantial heavy lifting — the retrieval and consolidation quality compensates for limited model reasoning.

---

## Reproducibility

### Requirements

- Python 3.11+
- PostgreSQL 16 with pgvector extension
- Any OpenAI-compatible LLM backend (vLLM, Ollama, cloud API)
- sentence-transformers for embedding
- ~24GB GPU VRAM for 14B model (or use a cloud API)

### Run the benchmark

```bash
git clone https://github.com/dayyanj/nmem-bench.git
cd nmem-bench
pip install nmem[postgres,st]
docker compose up -d

# Quick test (30 days, ~30 min)
python -m healthcare_bench.run_benchmark --fast

# Full benchmark (180 days, ~4-5 hours)
python -m healthcare_bench.run_benchmark --full
```

### Data

- **Synthea patients:** Generated with seed 42, 1,000 patients, Massachusetts population. CSV exports included in `healthcare_bench/data/synthea_csv/`.
- **Scenarios:** Hand-crafted guideline changes, drug recalls, and clinical conflicts in `healthcare_bench/data/scenarios.yaml`.
- **Questions:** 40 test questions with day-dependent rubrics in `healthcare_bench/data/questions.yaml`.

---

## Scope & Limitations

1. **Single judge model.** Scoring uses Qwen3-14B as judge — the same model that answers. A separate judge model would reduce self-preference bias. The Spwig benchmark used dual-judge (14B + 30B) for this reason.
2. **Synthetic data.** Synthea generates realistic but formulaic patient records. Real clinical data would test nmem against messier, more ambiguous inputs.
3. **14B model ceiling.** Temporal reasoning and cross-agent synthesis losses may reflect model capability rather than memory architecture limitations. Benchmarking with larger models is the obvious next step.
4. **No multi-turn evaluation.** Questions are single-turn. A multi-turn clinical dialogue (iterative diagnosis, treatment planning) would better test working memory and context management.
5. **Baseline uses raw data.** The baseline sees 30 days of raw Synthea CSV. A stronger baseline might use RAG over all historical data without consolidation — isolating nmem's consolidation value from its retrieval value.

## Next Steps

1. **Larger model benchmark** — Run the same 180 days with 30B+ models to quantify how much of the temporal reasoning gap is model vs. architecture.
2. **Dual-judge scoring** — Add 30B MoE CPU judge to reduce scoring bias.
3. **Temporal search mode** — Investigate chronological retrieval for time-ordered questions.
4. **Real clinical data** — Partner with a healthcare AI team to run the benchmark against de-identified clinical notes.
