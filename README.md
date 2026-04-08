# nmem

**Cognitive memory for AI agents** — hierarchical, self-refining, and framework-agnostic.

nmem gives your agents a brain that learns. Not just storage and retrieval — active cognition with automatic promotion, confidence decay, conflict detection, and nightly synthesis.

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
| **LTM** | Permanent knowledge | Forever | → Shared at importance ≥8 |
| **Shared** | Cross-agent facts | Forever | Canonical source |
| **Entity** | Per-object workspace | Forever | Collaborative |
| **Policy** | Governance rules | Forever | Writer-controlled |

## Providers

| Component | Options |
|-----------|---------|
| **Database** | PostgreSQL + pgvector (production), SQLite (dev) |
| **Embedding** | sentence-transformers (local), OpenAI (cloud), no-op |
| **LLM** | OpenAI-compatible (vLLM, Ollama), Anthropic, no-op |

## Configuration

Via Python, environment variables, or YAML:

```bash
export NMEM_DATABASE_URL=postgresql+asyncpg://localhost/mydb
export NMEM_EMBEDDING__PROVIDER=sentence-transformers
export NMEM_LLM__PROVIDER=openai
export NMEM_LLM__BASE_URL=http://localhost:11434/v1
export NMEM_LLM__MODEL=qwen3
```

## License

MIT — see [LICENSE](LICENSE)

## Credits

Adapted from Dayyan James' cognitive memory architecture, battle-tested in production AI agent systems.
