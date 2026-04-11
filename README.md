# nmem

**Cognitive memory for AI agents**: hierarchical, self-refining, and framework-agnostic.

nmem gives your agents a brain that learns. Not just storage and retrieval, but active cognition with automatic promotion, confidence decay, conflict detection, and nightly synthesis.

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
    subgraph cycle ["Every 6 hours"]
        direction LR
        D["Decay\nexpired"] --> P["Promote\nto LTM"] --> SP["Promote\nto Shared"] --> DD["Dedup\nmerge similar"] --> C["Confidence\ndecay stale"]
    end

    subgraph nightly ["Daily at 11 PM UTC"]
        SY["Synthesize\npatterns"] --> RB["Retroactive\nboost"]
    end

    style D fill:#ffebee,stroke:#ef5350
    style P fill:#e8f5e9,stroke:#4caf50
    style SP fill:#f3e5f5,stroke:#9c27b0
    style DD fill:#fff3e0,stroke:#ff9800
    style C fill:#e3f2fd,stroke:#2196f3
    style SY fill:#fce4ec,stroke:#e91e63
    style RB fill:#fff8e1,stroke:#ffc107
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
│  │ tiered   │ │ 40% FTS  │ │   curiosity)     ││
│  │ verbosity│ │          │ │                  ││
│  └──────────┘ └──────────┘ └──────────────────┘│
│                                                 │
│  6 Memory Tiers + Consolidation Engine          │
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

**Write**: agents store observations, decisions, and outcomes in their journal. Write-time compression distills verbose content into dense facts. Dedup prevents redundant entries.

**Search**: hybrid search combines pgvector cosine similarity (60%) with PostgreSQL full-text search (40%) across all tiers simultaneously. Access stats are updated on every retrieval.

**Consolidate**: a background engine promotes high-importance journal entries to permanent LTM, clusters and merges duplicates via union-find + LLM, decays confidence on stale entries, and synthesizes cross-agent patterns nightly.

**Promote**: no LLM decides what's "universal." Entries promote to shared knowledge when multiple agents actually search for them. The agents vote with their queries.

## Features

- **6-tier memory hierarchy**: working memory, journal, long-term memory, shared knowledge, entity memory, policy memory
- **Write-time compression**: LLM distills verbose content into dense facts
- **Hybrid search**: 60/40 vector + full-text search across all tiers
- **Background consolidation**: auto-promotes important entries, deduplicates, decays stale knowledge
- **Cognitive capabilities**: deja vu (past experience matching), counterfactual reasoning, curiosity signals
- **Governance**: policy memory with writer/proposer permissions, entity memory with grounding levels
- **Framework-agnostic**: works with LangChain, CrewAI, or plain Python
- **Pluggable providers**: bring your own LLM, embedding model, and database

## Quick Start

> **Note**: nmem is not yet on PyPI. Install from source:
>
> ```bash
> git clone https://github.com/dayyanj/nmem.git
> cd nmem
> pip install -e ".[postgres,st]"
> docker compose up -d  # PostgreSQL + pgvector
> ```

```python
from nmem import MemorySystem, NmemConfig

mem = MemorySystem(NmemConfig(
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
| [Concepts](docs/concepts.md) | The 6-tier hierarchy, consolidation, hybrid search explained |
| [MCP Integration](docs/mcp-integration.md) | Connect to Claude Code / Cursor with persistent memory |
| [Configuration](docs/configuration.md) | Every config option with tradeoffs and examples |
| [API Reference](docs/api-reference.md) | Full method documentation with signatures and examples |
| [Testing](TESTING.md) | Run tests, benchmarks, E2E QA checklist |

## Contributing

nmem is built in the open and improved by its community. We're looking for people who want to shape what cognitive memory for AI agents looks like — not just patch bugs, but rethink how agents learn.

### Ways to contribute

- **Code and docs**: bug fixes, new importers (Slack exports, Notion, Obsidian, Roam, etc.), new provider backends, performance work, typo fixes. First-time contributors are explicitly welcome. Open an issue before starting large changes so we can align on direction.
- **Independent benchmarks**: the benchmark suite in [src/nmem/benchmark/](src/nmem/benchmark/) only tells you what *we* measured. We want external, reproducible benchmarks that we don't control — run nmem against LongMemEval, LoCoMo, or your own workload, publish your methodology and raw numbers, and link back. Critical or negative results are just as valuable as positive ones.
- **Adversarial testing**: try to break cross-tenant isolation, confuse the consolidation engine, pollute shared knowledge, exploit policy memory permissions, or starve the pool. Security reports go to security@spwig.com (we'll set up a security.txt shortly); everything else belongs in a public issue.
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
nmem consolidate [--nightly]      # Run consolidation cycle
nmem setup [--auto-append]        # Configure MCP + generate CLAUDE.md snippet
nmem benchmark [--sizes 50,200]   # Run performance benchmarks
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
