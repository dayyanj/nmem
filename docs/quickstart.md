# Quickstart

<!-- i18n:start -->
**English** | [简体中文](i18n/zh-hans/quickstart.md) | [日本語](i18n/ja/quickstart.md) | [한국어](i18n/ko/quickstart.md) | [Español](i18n/es/quickstart.md) | [Português](i18n/pt/quickstart.md) | [Français](i18n/fr/quickstart.md) | [Deutsch](i18n/de/quickstart.md) | [Русский](i18n/ru/quickstart.md)
<!-- i18n:end -->


Get nmem running with real data in under 5 minutes.

> **Note**: nmem is not yet on PyPI. All install commands below use `pip install -e` from a local clone:
>
> ```bash
> git clone https://github.com/dayyanj/nmem.git
> cd nmem
> ```

## Option A: Zero-config demo (30 seconds)

```bash
pip install -e ".[cli]"
nmem demo
```

This uses SQLite (no Docker needed) and loads a built-in dataset showing 3 agents collaborating. You'll see cross-tier search, consolidation, and prompt injection working immediately.

## Option B: Import your Claude Code memories (2 minutes)

```bash
pip install -e ".[cli,sqlite]"
nmem init --sqlite
nmem import claude-code
nmem search "your topic"
nmem stats
```

This imports your existing `~/.claude/` memory files into nmem. Each memory file becomes a searchable LTM entry.

## Option C: Full production setup (5 minutes)

### 1. Start PostgreSQL + pgvector

```bash
pip install -e ".[cli,postgres,st]"
docker compose up -d
```

### 2. Initialize the database

```bash
nmem init
```

### 3. Import your data

```bash
# Claude Code memories
nmem import claude-code

# Or a directory of markdown files
nmem import markdown ./docs

# Or ChatGPT conversations
nmem import chatgpt ~/Downloads/conversations.json

# Or structured JSONL
nmem import jsonl data.jsonl
```

### 4. Search and explore

```bash
# Search across all memory tiers
nmem search "deployment process"

# See what's stored
nmem stats

# Run a consolidation cycle (promotes important entries)
nmem consolidate
```

### 5. Connect to Claude Code (optional)

```bash
nmem setup --auto-append
# Restart Claude Code to pick up the MCP server
```

Now Claude Code can store and search memories across conversations.

## Using nmem in Python

```python
import asyncio
from nmem import MemorySystem, NmemConfig

async def main():
    mem = MemorySystem(NmemConfig(
        database_url="postgresql+asyncpg://nmem:nmem@localhost:5433/nmem",
        embedding={"provider": "sentence-transformers"},
    ))
    await mem.initialize()

    # Store a memory
    await mem.journal.add(
        agent_id="my-agent",
        entry_type="lesson_learned",
        title="Always validate inputs before DB queries",
        content="A production incident was caused by unvalidated user input...",
        importance=8,  # High importance → auto-promotes to LTM
    )

    # Search across all tiers
    results = await mem.search("my-agent", "input validation")
    for r in results:
        print(f"[{r.tier}] {r.content[:80]}")

    # Build prompt context
    ctx = await mem.prompt.build(agent_id="my-agent", query="security best practices")
    print(ctx.full_injection)

    await mem.close()

asyncio.run(main())
```

## What's next?

- [Concepts](concepts.md): understand the 6-tier memory hierarchy
- [Configuration](configuration.md): tune thresholds and providers
- [MCP Integration](mcp-integration.md): connect to Claude Code / Cursor
- [API Reference](api-reference.md): full method documentation
