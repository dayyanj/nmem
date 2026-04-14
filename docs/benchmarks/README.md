# Benchmarks

<!-- i18n:start -->
**English** | [简体中文](../i18n/zh-hans/benchmarks.md) | [日本語](../i18n/ja/benchmarks.md) | [한국어](../i18n/ko/benchmarks.md) | [Español](../i18n/es/benchmarks.md) | [Português](../i18n/pt/benchmarks.md) | [Français](../i18n/fr/benchmarks.md) | [Deutsch](../i18n/de/benchmarks.md) | [Русский](../i18n/ru/benchmarks.md)
<!-- i18n:end -->


Empirical evaluations of nmem's impact on agent performance. All benchmarks use reproducible methodology with local hardware ($0 inference cost).

## Results Summary

| Benchmark | Finding | Key Metric |
|-----------|---------|------------|
| [Healthcare Multi-Agent](healthcare-multi-agent.md) | Belief revision scores **5.00/5 vs 3.13/5 baseline** after guideline changes — on a 14B model, single consumer GPU | +7% overall, +60% belief revision |
| [Spwig Institutional Knowledge](spwig-institutional-knowledge.md) | nmem MCP search matches a new developer's accuracy at **half the cost** | 4.27/5 judge score, $0.097/task |
| [Recognition Signals](recognition-signals.md) | Trust tags in prompts don't change 8B-30B model behaviour | Recognition computed but not injected |

## Spwig Benchmark: Quick Numbers

**Setup:** 17-repo eCommerce platform, 45 test tasks, 5 variants, 225 dual-judge evaluations.

| Variant | What it is | Judge Score | Cost |
|---------|-----------|-------------|------|
| **v8_mcp** | Agent searches nmem via MCP tools | 4.27/5 | **$4.35** |
| new_developer | No memory, explores from scratch | 4.36/5 | $8.18 |
| control | Claude Code auto-memory (82% fact coverage) | 3.98/5 | $7.06 |
| v8_injected | Memory pre-injected into prompt | 4.02/5 | $15.27 |
| v8_briefing | Briefing API with recognition signals | 3.96/5 | $18.36 |

**Key insight:** MCP search is both cheapest AND most accurate because the agent decides what to search based on each question. Pre-injection guesses what might be useful before seeing the question.

## Healthcare Benchmark: Quick Numbers

**Setup:** 180-day simulation, 4 healthcare agents, 200 synthetic patients, 1,705 encounters, 40 test questions, Qwen3-14B on RTX 4090 ($0 inference cost).

| Category | nmem | Baseline | Delta |
|----------|------|----------|-------|
| **Belief revision** | **4.75/5** | 3.21/5 | **+48%** |
| Direct recall | 4.09/5 | 3.73/5 | +10% |
| Pattern detection | 3.48/5 | 3.33/5 | +4% |
| Overall | 3.84/5 | 3.60/5 | +7% |

**Key insight:** Belief revision is nmem's strongest differentiator. When guidelines change, nmem detects the contradiction, resolves it at consolidation, and retrieves the updated policy. The baseline LLM has no mechanism to distinguish current from outdated knowledge. After day 120 (all guidelines changed), nmem scores a perfect 5.00/5 on every belief revision question.

## Tested Configurations

| Benchmark | Model | Integration | Context |
|-----------|-------|-------------|---------|
| Spwig | Claude Sonnet 4.6 (200K ctx) | MCP tools | Institutional knowledge retrieval |
| Healthcare | Qwen3-14B-AWQ (consumer GPU) | Python API + search | Multi-agent consolidation over time |

## Methodology

### Spwig Benchmark
- **Agent:** Claude Sonnet 4.6, headless (`claude -p`), single invocation per task
- **Judges:** Qwen3-14B (GPU) + Qwen3-30B-A3B MoE (CPU), independent scoring, 1-5 scale
- **Isolation:** Dedicated PostgreSQL on port 5435, separate from development
- **Corpus:** 6,076 entries — LLM-distilled conversations + semantically chunked auto-memory + docs + git
- **Controls:** Clean HOME for new_developer, no session persistence, same CLAUDE.md base

### Healthcare Benchmark
- **Model:** Qwen3-14B-AWQ on vLLM (single RTX 4090, $0 inference)
- **Judge:** Qwen3-14B, 1-5 scale against rubric
- **Data:** Synthea synthetic patients (Apache 2.0) + hand-crafted clinical scenarios
- **Simulation:** 180 days compressed into 4.6 hours via time simulation
- **Evaluation:** 40 questions at 7 intervals (days 1, 30, 60, 90, 120, 150, 180)
