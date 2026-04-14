# nmem

<!-- i18n:start -->
**English** | [简体中文](docs/i18n/zh-hans/README.md) | [日本語](docs/i18n/ja/README.md) | [한국어](docs/i18n/ko/README.md) | [Español](docs/i18n/es/README.md) | [Português](docs/i18n/pt/README.md) | [Français](docs/i18n/fr/README.md) | [Deutsch](docs/i18n/de/README.md) | [Русский](docs/i18n/ru/README.md)
<!-- i18n:end -->


**Cognitive memory for AI agents**: hierarchical, self-refining, and framework-agnostic.

nmem gives your agents a brain that learns. Not just storage and retrieval, but active cognition — automatic promotion, belief revision, nightly retrospectives, social learning across agents, and token-tracked prompt injection.

> **We're actively looking for contributors.** nmem is intentionally built as a community-driven project. Code, docs, ideas, bug reports, independent benchmarks, and adversarial testing are all welcome. See [Contributing](#contributing) below.

## How it works

### Memory flows upward: entries earn their way

```mermaid
graph LR
    subgraph session [" "]
        W["Working Memory\n<i>session slots</i>"]
    end

    subgraph shortterm [" "]
        J["Journal\n<i>30-day log</i>"]
    end

    subgraph longterm [" "]
        L["Long-Term Memory\n<i>per-agent, permanent</i>"]
    end

    subgraph shared [" "]
        S["Shared Knowledge\n<i>cross-agent, canonical</i>"]
    end

    W -- "session end" --> J
    J -- "importance ≥ 7\nor accessed 5x" --> L
    L -- "≥ 2 agents\naccessed it" --> S

    style W fill:#e8f5e9,stroke:#4caf50,color:#1b5e20
    style J fill:#fff3e0,stroke:#ff9800,color:#e65100
    style L fill:#e3f2fd,stroke:#2196f3,color:#0d47a1
    style S fill:#f3e5f5,stroke:#9c27b0,color:#4a148c
    style session fill:none,stroke:none
    style shortterm fill:none,stroke:none
    style longterm fill:none,stroke:none
    style shared fill:none,stroke:none
```

Plus two specialized tiers: **Entity Memory** (per-object collaborative workspace) and **Policy Memory** (governance rules with write permissions).

### The consolidation engine refines memory overnight

```mermaid
graph LR
    subgraph cycle ["Every 6 hours (10 steps)"]
        direction LR
        D["Decay\nexpired"] --> P["Promote\nto LTM"] --> SP["Promote\nto Shared"] --> DD["Dedup\nmerge"] --> AI["Auto-\nimportance"] --> BR["Belief\nrevision"] --> SD["Salience\ndecay"]
    end

    subgraph nightly ["Daily (dreamstate)"]
        SY["Synthesize\npatterns"] --> RB["Retroactive\nboost"] --> RT["Retrospective\nvalidate lessons"]
    end

    style D fill:#ffebee,stroke:#ef5350
    style P fill:#e8f5e9,stroke:#4caf50
    style SP fill:#f3e5f5,stroke:#9c27b0
    style DD fill:#fff3e0,stroke:#ff9800
    style AI fill:#e8eaf6,stroke:#3f51b5
    style BR fill:#fce4ec,stroke:#e91e63
    style SD fill:#e3f2fd,stroke:#2196f3
    style SY fill:#fce4ec,stroke:#e91e63
    style RB fill:#fff8e1,stroke:#ffc107
    style RT fill:#e0f7fa,stroke:#00bcd4
    style cycle fill:#fafafa,stroke:#e0e0e0
    style nightly fill:#fafafa,stroke:#e0e0e0
```

### The full picture

```
Your Agent (LangChain / CrewAI / Plain Python)
    │
    ▼
┌─────────────────────────────────────────────────┐
│  MemorySystem                                   │
│                                                 │
│  ┌──────────┐ ┌──────────┐ ┌──────────────────┐│
│  │ Prompt   │ │ Hybrid   │ │  Cognitive       ││
│  │ Builder  │ │ Search   │ │  Engine          ││
│  │          │ │ 60% vec  │ │  (deja vu,       ││
│  │ tiered   │ │ 40% FTS  │ │  belief revision,││
│  │ verbosity│ │          │ │  retrospective)  ││
│  └──────────┘ └──────────┘ └──────────────────┘│
│                                                 │
│  6 Memory Tiers + 10-Step Consolidation Engine  │
└─────────────────────┬───────────────────────────┘
                      │
        ┌─────────────┼─────────────┐
        ▼             ▼             ▼
   ┌─────────┐  ┌──────────┐  ┌─────────┐
   │ Database│  │ Embedding│  │  LLM    │
   │ pg+vec  │  │ MiniLM   │  │ vLLM    │
   │ SQLite  │  │ OpenAI   │  │ Ollama  │
   └─────────┘  └──────────┘  └─────────┘
```

**Write**: agents store observations, decisions, and outcomes in their journal. Write-time compression distills verbose content into dense facts. Dedup prevents redundant entries. Conflict detection flags contradictions at write time.

**Search**: hybrid search combines pgvector cosine similarity (60%) with PostgreSQL full-text search (40%) across all tiers simultaneously. Access stats are updated on every retrieval. Knowledge links expand results with associated entries.

**Consolidate**: a 10-step background engine promotes important entries to LTM, deduplicates via union-find + LLM, rescores importance heuristically, resolves belief conflicts (grounding rank → agent trust → recency), decays salience on stale entries, builds knowledge links, and synthesizes cross-agent patterns nightly.

**Reflect**: the nightly "dreamstate" retrospective validates past lessons against new evidence — reinforcing what held up and marking contradicted lessons as disputed. Token usage is tracked automatically so you can measure memory efficiency over time.

**Promote**: no LLM decides what's "universal." Entries promote to shared knowledge when multiple agents actually search for them. The agents vote with their queries — this is **social learning** across your agent team.

## Features

- **6-tier memory hierarchy**: working memory → journal → long-term memory → shared knowledge, plus entity memory and policy memory
- **Social learning**: agents learn from each other — when multiple agents access the same knowledge, it auto-promotes to shared. One agent's lesson benefits the entire team
- **Belief revision**: contradictions detected at write time, resolved at consolidation using grounding rank → agent trust → recency → importance. Configurable per-agent trust scores
- **Nightly retrospective**: "dreamstate" step validates past lessons against new evidence — reinforcing what held up, disputing what didn't. Bounded LLM budget (5 calls/night default)
- **Auto-importance scoring**: heuristic rescoring at consolidation for entries without explicit importance. Manual scores are never overwritten
- **Salience decay**: unused knowledge fades from current reasoning (but isn't deleted). Reinforced lessons get their salience refreshed
- **Write-time compression**: LLM distills verbose content into dense facts
- **Hybrid search**: 60% vector + 40% full-text search across all tiers, with knowledge link expansion
- **10-step consolidation engine**: decay, promote, dedup, rescore, resolve conflicts, salience decay, custom hooks, knowledge links, curiosity decay — plus nightly synthesis and retrospective
- **Token trends**: automatic tracking of prompt injection sizes and LLM costs. CLI (`nmem token-trends`) and API (`GET /v1/token-trends`) for monitoring efficiency over time
- **Configuration profiles**: `NmemConfig.from_profile("refinery")` for pre-tuned multi-agent defaults, or `"neutral"` for generic. Custom profiles via `register_profile()`
- **Governance**: policy memory with writer/proposer permissions, entity memory with grounding levels (`source_material` / `confirmed` / `inferred` / `disputed`)
- **Framework adapters**: LangChain (`BaseMemory` compatible), CrewAI, or plain Python — `pip install nmem[langchain]`
- **Pluggable providers**: bring your own LLM (OpenAI-compatible, Anthropic), embedding model (sentence-transformers, OpenAI), and database (PostgreSQL + pgvector, SQLite)

## Benchmarked

nmem has been tested with **Claude Code** (Claude Sonnet 4.6) against a real-world 17-repository eCommerce platform (45 tasks, 225 dual-judge evaluations). Full methodology and results: [docs/benchmarks/](docs/benchmarks/)

| | Without nmem | With nmem (MCP) | Improvement |
|---|---|---|---|
| **Factual accuracy** | 3.60/5 | **4.00/5** | +11% |
| **Cost per task** | $0.182 | **$0.097** | **47% cheaper** |
| **Wall clock** | 69s/task | **43s/task** | **38% faster** |
| **Documentation quality** | 4.00/5 | **4.20/5** | +5% |

- Tested with Claude Code (Sonnet 4.6) via MCP integration — the primary validated use case
- Scores from independent dual-judge evaluation (Qwen3-14B + Qwen3-30B, $0 judging cost)
- "Without nmem" is a new developer with no prior context exploring all repositories from scratch
- "With nmem" uses MCP tool search against a corpus of LLM-distilled conversations, docs, and git history
- Both variants have identical codebase access — the difference is memory
- Agentic use cases (support agents, autonomous systems with smaller context windows) are next on the benchmark roadmap

## Quick Start

```bash
pip install nmem[postgres,st]
docker compose up -d  # PostgreSQL + pgvector
```

```python
from nmem import MemorySystem, NmemConfig

# Use a profile for pre-tuned defaults, or NmemConfig() for neutral
mem = MemorySystem(NmemConfig.from_profile("neutral",
    database_url="postgresql+asyncpg://nmem:nmem@localhost:5433/nmem",
    embedding={"provider": "sentence-transformers"},
))
await mem.initialize()

# Store a memory
await mem.journal.add(
    agent_id="support",
    entry_type="lesson_learned",
    title="Refund process requires manager approval",
    content="Customer requested refund for order #1234. Process requires...",
    importance=7,  # High importance → auto-promotes to LTM
)

# Search across all tiers
results = await mem.search(agent_id="support", query="refund process")

# Build prompt injection
ctx = await mem.prompt.build(agent_id="support", query="How do I process a refund?")
system_prompt = f"You are a support agent.\n\n{ctx.full_injection}"

# Start background consolidation
mem.start_consolidation()
```

## Memory Tiers

| Tier | Purpose | Lifespan | Promotion |
|------|---------|----------|-----------|
| **Working** | Current session context | Session | → Journal on close |
| **Journal** | Activity log | 30 days | → LTM at importance ≥7 |
| **LTM** | Permanent knowledge | Forever | → Shared when ≥2 agents access |
| **Shared** | Cross-agent facts | Forever | Canonical source |
| **Entity** | Per-object workspace | Forever | Collaborative |
| **Policy** | Governance rules | Forever | Writer-controlled |

## Providers

| Component | Options |
|-----------|---------|
| **Database** | PostgreSQL + pgvector (production), SQLite (dev) |
| **Embedding** | sentence-transformers (local), OpenAI (cloud), no-op |
| **LLM** | OpenAI-compatible (vLLM, Ollama), Anthropic, no-op |

## Documentation

| Guide | Description |
|-------|-------------|
| [Quickstart](docs/quickstart.md) | Install to first search in under 5 minutes |
| [Concepts](docs/concepts.md) | The 6-tier hierarchy, consolidation, social learning, hybrid search |
| [Profiles](docs/profiles.md) | Pre-tuned configs for common scenarios + suggested configs by use case |
| [Configuration](docs/configuration.md) | Every config option with tradeoffs and examples |
| [MCP Integration](docs/mcp-integration.md) | Connect to Claude Code / Cursor with persistent memory |
| [API Reference](docs/api-reference.md) | Full method documentation with signatures and examples |
| [Benchmarks](docs/benchmarks/) | Empirical evaluations — institutional knowledge retrieval, recognition signals |
| [Testing](TESTING.md) | Run tests, benchmarks, E2E QA checklist |

## Contributing

nmem is built in the open and improved by its community. We're looking for people who want to shape what cognitive memory for AI agents looks like — not just patch bugs, but rethink how agents learn.

### Ways to contribute

- **Code and docs**: bug fixes, new importers (Slack exports, Notion, Obsidian, Roam, etc.), new provider backends, performance work, typo fixes. First-time contributors are explicitly welcome. Open an issue before starting large changes so we can align on direction.
- **Independent benchmarks**: the benchmark suite in [src/nmem/benchmark/](src/nmem/benchmark/) only tells you what *we* measured. We want external, reproducible benchmarks that we don't control — run nmem against LongMemEval, LoCoMo, or your own workload, publish your methodology and raw numbers, and link back. Critical or negative results are just as valuable as positive ones.
- **Adversarial testing**: try to confuse the consolidation engine, pollute shared knowledge via promotion loopholes, exploit policy memory writer permissions, race the pool into inconsistent state, or construct inputs that cause embedding/compression failures. Security reports go to support@spwig.com; everything else belongs in a public issue.
- **Real-world usage reports**: if you deployed nmem at a meaningful scale, write up what broke, what surprised you, and what you wish worked differently. Blog posts, gists, GitHub issues — any format. This is how the project gets better where theory meets practice.
- **Ideas and discussion**: the memory tier hierarchy, consolidation policies, and promotion criteria are not sacred. If you have a better model, open a discussion. The [concepts doc](docs/concepts.md) explains the current design rationale as a starting point.

### How to get started

1. Read the [Concepts](docs/concepts.md) and [Quickstart](docs/quickstart.md) guides
2. Run `nmem demo` locally to see the system end-to-end
3. Look at [TESTING.md](TESTING.md) for how to run the test suite and benchmark harness
4. Browse open issues tagged `good-first-issue` and `help-wanted`
5. Fork, branch, PR. Add tests for anything non-trivial. Keep existing tests green.

### Benchmark submissions

If you run an independent benchmark:

- Publish it somewhere public (blog post, paper, gist, README in your own repo)
- Include the nmem commit SHA, hardware, and configuration so others can reproduce
- Open an issue with the link — we'll add it to a `docs/independent-benchmarks.md` index if you're willing

We want this project to have honest numbers, even when they're unflattering. If nmem loses to a simpler system on your workload, we want to know why and fix it.

## CLI

```bash
nmem init [--sqlite]              # Initialize database
nmem demo                         # Run interactive demo
nmem search <query>               # Search across all tiers
nmem stats                        # Show tier counts + per-agent breakdown
nmem token-trends [--days 30]     # Token usage trends (prompt injection + LLM costs)
nmem consolidate [--nightly]      # Run consolidation cycle
nmem conflicts list [--pending]   # Show detected memory conflicts
nmem setup [--auto-append]        # Configure MCP + generate CLAUDE.md snippet
nmem benchmark [--sizes 50,200]   # Run performance benchmarks
nmem doctor                       # Diagnose database + provider health
nmem serve                        # Start the REST API server
nmem import claude-code           # Import Claude Code memories
nmem import chatgpt <file>        # Import ChatGPT conversations
nmem import markdown <dir>        # Import markdown directory
nmem import jsonl <file>          # Import structured JSONL
```

## License

MIT, see [LICENSE](LICENSE)

## Credits

Created by [Dayyan James](https://dj-ai.ai), extracted from the cognitive memory architecture powering [Spwig](https://spwig.com)'s production AI agent systems.

- [dj-ai.ai](https://dj-ai.ai): AI research and engineering blog
- [spwig.com](https://spwig.com): where nmem runs in commercial production
