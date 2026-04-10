# MCP Integration

nmem ships an MCP (Model Context Protocol) server that lets Claude Code, Cursor, and other AI tools use nmem as persistent memory across conversations.

## Setup

> **Note**: nmem is not yet on PyPI. Clone and install from source:

### 1. Install nmem with MCP support

```bash
git clone https://github.com/dayyanj/nmem.git
cd nmem
pip install -e ".[cli,postgres,st,mcp-server]"
```

### 2. Start the database

```bash
docker compose up -d
nmem init
```

### 3. Configure your AI tool

Run the setup command from your project directory:

```bash
cd /your/project
nmem setup
```

This does two things:
- Creates `.claude.json` with the MCP server configuration
- Prints a CLAUDE.md snippet to paste into your project

To auto-append the snippet to your CLAUDE.md:

```bash
nmem setup --auto-append
```

### 4. Restart your AI tool

Restart Claude Code (or Cursor) to pick up the new MCP server. You should see `nmem` listed when you check available tools.

## Manual configuration

If you prefer manual setup, add this to your `.claude.json`:

```json
{
  "mcpServers": {
    "nmem": {
      "command": "nmem-mcp",
      "env": {
        "NMEM_DATABASE_URL": "postgresql+asyncpg://nmem:nmem@localhost:5433/nmem",
        "NMEM_EMBEDDING__PROVIDER": "sentence-transformers"
      }
    }
  }
}
```

And add the memory instructions to your `CLAUDE.md` (see the [CLAUDE.md snippet](#claudemd-snippet) below).

## Available tools

The MCP server exposes 7 tools:

### `memory_store`

Store a journal entry. High-importance entries (≥7) auto-promote to permanent LTM.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `title` | string | required | Short descriptive title |
| `content` | string | required | Full memory content |
| `agent_id` | string | `"default"` | Agent storing the memory |
| `importance` | int | `5` | 1-10 scale (7+ auto-promotes) |
| `entry_type` | string | `"observation"` | Type: observation, decision, lesson_learned, etc. |
| `tags` | list[str] | null | Optional tags |

### `memory_search`

Search across all memory tiers using hybrid vector + full-text search.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `query` | string | required | Natural language search query |
| `agent_id` | string | `"default"` | Agent perspective |
| `tiers` | string | null | Comma-separated filter: `"journal,ltm,shared"` |
| `top_k` | int | `10` | Maximum results |

### `memory_recall`

Get recent journal entries chronologically.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `agent_id` | string | `"default"` | Agent whose journal to recall |
| `days` | int | `7` | Look back N days |
| `limit` | int | `10` | Maximum entries |

### `memory_context`

Build full memory context for prompt injection. Returns a formatted block with relevant entries from all tiers.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `query` | string | required | Topic to build context for |
| `agent_id` | string | `"default"` | Agent perspective |

### `memory_save_ltm`

Save permanent knowledge. Upserts by `(agent_id, key)`: re-saving the same key updates the entry.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `key` | string | required | Unique key (e.g., `"deploy_process"`) |
| `content` | string | required | Knowledge content |
| `agent_id` | string | `"default"` | Agent saving the knowledge |
| `category` | string | `"fact"` | Category: fact, procedure, lesson, etc. |
| `importance` | int | `5` | 1-10 scale |

### `memory_save_shared`

Save knowledge visible to all agents.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `key` | string | required | Unique key |
| `content` | string | required | Knowledge content |
| `category` | string | `"fact"` | Category |
| `importance` | int | `5` | 1-10 scale |

### `memory_stats`

Get memory system statistics: tier counts, database info, system status. No parameters.

## Available resources

The MCP server also exposes 3 resources for direct reading:

| URI | Description |
|-----|-------------|
| `nmem://agent/{agent_id}/journal` | Recent journal entries (last 7 days) |
| `nmem://agent/{agent_id}/ltm` | All LTM entries for an agent |
| `nmem://shared` | All shared knowledge |

## CLAUDE.md snippet

This is what `nmem setup` adds to your CLAUDE.md. It teaches Claude to proactively use the memory tools:

```markdown
## Agent Memory (nmem)

This project uses nmem for persistent cognitive memory via MCP.

### When to use memory

**At the start of complex tasks**, search for relevant context:
- Before debugging: `memory_search("error X in module Y")`
- Before implementing: `memory_search("feature X design decisions")`
- Before refactoring: `memory_search("module X architecture")`

**After completing significant work**, store what you learned:
- Bug fixes: `memory_store(title="Fixed X by doing Y", content="...", importance=7)`
- Design decisions: `memory_save_ltm(key="auth_architecture", content="...", importance=8)`
- Lessons learned: `memory_store(title="Never do X because Y", content="...", importance=8)`

**For shared team knowledge**:
- `memory_save_shared(key="deploy_process", content="...", importance=8)`

### Importance guide
- 1-4: Low: transient observations
- 5-6: Medium: useful context
- 7-8: High: auto-promotes to permanent memory
- 9-10: Critical: architecture decisions, incident post-mortems
```

## How it works in practice

1. **Claude starts a conversation**: the MCP server initializes nmem
2. **Claude reads CLAUDE.md**: sees the memory instructions
3. **Before complex work**: Claude calls `memory_search` to check for relevant past context
4. **After significant work**: Claude calls `memory_store` to save learnings
5. **Next conversation**: Claude searches again and finds what it learned last time
6. **Over time**: the consolidation engine promotes important knowledge, decays stale entries, and deduplicates

The net effect: Claude genuinely gets smarter about your codebase with each conversation.

## Troubleshooting

**MCP server not connecting:**
- Check `nmem-mcp` is on PATH: `which nmem-mcp`
- Check the database is running: `docker compose ps`
- Test manually: `echo '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"test","version":"0.1"}}}' | NMEM_DATABASE_URL=... nmem-mcp`

**Claude not using memory tools:**
- Ensure the CLAUDE.md snippet is present in your project
- Check that the MCP server shows in Claude Code's tool list
- Try explicitly asking: "search your memory for X"

**Slow responses:**
- The embedding model loads on first use (~2-3s). Subsequent calls are fast.
- For large datasets (1000+ entries), ensure PostgreSQL is used (not SQLite)
