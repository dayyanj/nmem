# Benchmarks

<!-- i18n:start -->
**English** | [简体中文](i18n/zh-hans/benchmarks.md) | [日本語](i18n/ja/benchmarks.md) | [한국어](i18n/ko/benchmarks.md) | [Español](i18n/es/benchmarks.md) | [Português](i18n/pt/benchmarks.md) | [Français](i18n/fr/benchmarks.md) | [Deutsch](i18n/de/benchmarks.md) | [Русский](i18n/ru/benchmarks.md)
<!-- i18n:end -->


Empirical evaluations of nmem's impact on agent performance. All benchmarks use reproducible methodology with dual-judge scoring on local hardware ($0 judging cost).

## Results Summary

| Benchmark | Finding | Key Metric |
|-----------|---------|------------|
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

## Current Scope

All benchmarks to date use **Claude Code (Sonnet 4.6, 200K context)** — the MCP integration is the validated use case. Agentic use cases with smaller models (8B-30B, 8K-32K context) are next on the roadmap. See [Scope & Limitations](spwig-institutional-knowledge.md#scope--limitations) for details.

## Methodology

- **Agent:** Claude Sonnet 4.6, headless (`claude -p`), single invocation per task
- **Judges:** Qwen3-14B (GPU) + Qwen3-30B-A3B MoE (CPU), independent scoring, 1-5 scale
- **Isolation:** Dedicated PostgreSQL on port 5435, separate from development
- **Corpus:** 6,076 entries — LLM-distilled conversations + semantically chunked auto-memory + docs + git
- **Controls:** Clean HOME for new_developer, no session persistence, same CLAUDE.md base
