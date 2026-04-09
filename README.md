# nmem

**Cognitive memory for AI agents** — hierarchical, self-refining, and framework-agnostic.

nmem gives your agents a brain that learns. Not just storage and retrieval — active cognition with automatic promotion, confidence decay, conflict detection, and nightly synthesis.

## Architecture

```mermaid
graph TB
    subgraph Agent["Your Agent / Framework"]
        LC[LangChain Adapter]
        CR[CrewAI Adapter]
        PY[Plain Python]
    end

    subgraph nmem["nmem — Cognitive Memory System"]
        direction TB

        MS[MemorySystem]
        PB[Prompt Builder]
        CE[Cognitive Engine]

        subgraph Tiers["Memory Tiers"]
            direction LR
            T1["<b>Working</b><br/><i>ephemeral</i><br/>session slots"]
            T2["<b>Journal</b><br/><i>30 days</i><br/>activity log"]
            T3["<b>LTM</b><br/><i>permanent</i><br/>per-agent knowledge"]
            T4["<b>Shared</b><br/><i>permanent</i><br/>cross-agent facts"]
            T5["<b>Entity</b><br/><i>permanent</i><br/>per-object workspace"]
            T6["<b>Policy</b><br/><i>permanent</i><br/>governance rules"]
        end

        subgraph Consolidator["Consolidation Engine (background)"]
            direction LR
            DECAY[Decay Expired]
            PROMO[Promote to LTM]
            SHARED_PROMO[Promote to Shared<br/><i>cross-agent access</i>]
            DEDUP[Dedup via<br/>Union-Find + LLM]
            CONF[Confidence<br/>Decay]
            SYNTH[Nightly<br/>Synthesis]
        end

        HS[Hybrid Search<br/>60/40 Vector + FTS]
    end

    subgraph Providers["Pluggable Providers"]
        EMB["Embedding<br/>sentence-transformers<br/>OpenAI · no-op"]
        LLM["LLM<br/>vLLM · Ollama · OpenAI<br/>Anthropic · no-op"]
        DB["Database<br/>PostgreSQL + pgvector<br/>SQLite (dev)"]
    end

    LC & CR & PY --> MS
    MS --> PB
    MS --> CE
    MS --> Tiers
    Tiers --> HS
    HS --> DB
    HS --> EMB

    T2 -- "importance ≥ 7" --> PROMO
    PROMO --> T3
    T3 -- "≥ 2 agents accessed" --> SHARED_PROMO
    SHARED_PROMO --> T4
    SYNTH -- "patterns" --> T4
    Consolidator --> LLM
    Consolidator --> EMB

    style T1 fill:#e8f5e9,stroke:#4caf50
    style T2 fill:#fff3e0,stroke:#ff9800
    style T3 fill:#e3f2fd,stroke:#2196f3
    style T4 fill:#f3e5f5,stroke:#9c27b0
    style T5 fill:#fce4ec,stroke:#e91e63
    style T6 fill:#efebe9,stroke:#795548
    style Consolidator fill:#fff8e1,stroke:#ffc107
    style HS fill:#e0f7fa,stroke:#00bcd4
```

### How it works

1. **Write** — agents store observations, decisions, and outcomes in their journal. Write-time compression distills verbose content into dense facts. Dedup prevents redundant entries.

2. **Search** — hybrid search combines pgvector cosine similarity (60%) with PostgreSQL full-text search (40%) across all tiers simultaneously. Access stats are updated on every retrieval.

3. **Consolidate** — a background engine promotes high-importance journal entries to permanent LTM, clusters and merges duplicates via union-find + LLM, decays confidence on stale entries, and runs nightly cross-agent pattern synthesis.

4. **Inject** — the prompt builder assembles relevant memory context with tiered verbosity (policies get full text, journal gets title stubs) and injects it into your agent's system prompt.

## Features

- **6-tier memory hierarchy** — working memory, journal, long-term memory, shared knowledge, entity memory, policy memory
- **Write-time compression** — LLM distills verbose content into dense facts
- **Hybrid search** — 60/40 vector + full-text search across all tiers
- **Background consolidation** — auto-promotes important entries, deduplicates, decays stale knowledge
- **Cognitive capabilities** — deja vu (past experience matching), counterfactual reasoning, curiosity signals
- **Governance** — policy memory with writer/proposer permissions, entity memory with grounding levels
- **Framework-agnostic** — works with LangChain, CrewAI, or plain Python
- **Pluggable providers** — bring your own LLM, embedding model, and database

## Quick Start

```bash
pip install nmem[postgres,st]
docker compose up -d  # PostgreSQL + pgvector
```

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

MIT — see [LICENSE](LICENSE)

## Credits

Adapted from Dayyan James' cognitive memory architecture, battle-tested in production AI agent systems.
