"""
nmem MCP Server — exposes cognitive memory as tools for Claude Code, Cursor, etc.

Configuration in .claude.json:
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
"""

from __future__ import annotations

import logging
import sys
from contextlib import asynccontextmanager

from mcp.server.fastmcp import FastMCP, Context

# Route all logging to stderr (stdout is reserved for MCP JSON-RPC)
logging.basicConfig(stream=sys.stderr, level=logging.WARNING)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(server: FastMCP):
    """Initialize MemorySystem on startup, close on shutdown."""
    from nmem import MemorySystem
    from nmem.cli.config_loader import load_config

    config = load_config()
    mem = MemorySystem(config)
    await mem.initialize()
    logger.info("nmem MCP server initialized")
    try:
        yield {"mem": mem}
    finally:
        await mem.close()
        logger.info("nmem MCP server shut down")


mcp = FastMCP("nmem", lifespan=lifespan)


def _get_mem(ctx: Context):
    """Get MemorySystem from lifespan context."""
    return ctx.request_context.lifespan_context["mem"]


# ── Tools ─────────────────────────────────────────────────────────────────────


@mcp.tool()
async def memory_store(
    ctx: Context,
    title: str,
    content: str,
    agent_id: str = "default",
    importance: int = 5,
    entry_type: str = "observation",
    tags: list[str] | None = None,
) -> str:
    """Store a memory in the journal. High-importance entries (>=7) auto-promote to long-term memory.

    Args:
        title: Short descriptive title.
        content: Full memory content.
        agent_id: Agent storing the memory.
        importance: 1-10 (7+ auto-promotes to LTM).
        entry_type: Type: observation, decision, lesson_learned, session_summary.
        tags: Optional tags for filtering.
    """
    mem = _get_mem(ctx)
    entry = await mem.journal.add(
        agent_id=agent_id, entry_type=entry_type,
        title=title, content=content,
        importance=importance, tags=tags,
    )
    return f"Stored journal entry #{entry.id}: {entry.title} (importance={entry.importance})"


@mcp.tool()
async def memory_search(
    ctx: Context,
    query: str,
    agent_id: str = "default",
    tiers: str | None = None,
    top_k: int = 10,
) -> str:
    """Search across all memory tiers using hybrid vector + full-text search.

    Args:
        query: Natural language search query.
        agent_id: Agent perspective for search.
        tiers: Comma-separated tier filter: journal,ltm,shared,entity.
        top_k: Maximum results to return.
    """
    mem = _get_mem(ctx)
    tier_tuple = tuple(tiers.split(",")) if tiers else None
    results = await mem.search(
        agent_id=agent_id, query=query, tiers=tier_tuple, top_k=top_k,
    )

    if not results:
        return "No results found."

    lines = [f"Found {len(results)} results:\n"]
    for i, r in enumerate(results, 1):
        title = getattr(r, "title", None) or getattr(r, "key", None) or ""
        score = f"{r.score:.3f}" if isinstance(r.score, float) else str(r.score)
        lines.append(f"{i}. [{r.tier}] (score: {score}) {title}")
        lines.append(f"   {r.content[:200]}")
        lines.append("")

    return "\n".join(lines)


@mcp.tool()
async def memory_recall(
    ctx: Context,
    agent_id: str = "default",
    days: int = 7,
    limit: int = 10,
) -> str:
    """Get recent journal entries for an agent.

    Args:
        agent_id: Agent whose journal to recall.
        days: Look back N days.
        limit: Maximum entries.
    """
    mem = _get_mem(ctx)
    entries = await mem.journal.recent(agent_id, days=days, limit=limit)

    if not entries:
        return f"No journal entries in the last {days} days."

    lines = [f"Recent entries ({len(entries)}):\n"]
    for e in entries:
        ts = e.created_at.strftime("%Y-%m-%d %H:%M") if e.created_at else "?"
        lines.append(f"- [{ts}] ({e.entry_type}) {e.title}")
        lines.append(f"  {e.content[:150]}")
        lines.append("")

    return "\n".join(lines)


@mcp.tool()
async def memory_context(
    ctx: Context,
    query: str,
    agent_id: str = "default",
) -> str:
    """Build memory context for prompt injection. Returns formatted memory relevant to the query.

    Use this to retrieve all relevant memory context for a specific topic.

    Args:
        query: Topic or question to build context for.
        agent_id: Agent perspective.
    """
    mem = _get_mem(ctx)
    prompt_ctx = await mem.prompt.build(agent_id=agent_id, query=query)
    return prompt_ctx.full_injection


@mcp.tool()
async def memory_save_ltm(
    ctx: Context,
    key: str,
    content: str,
    agent_id: str = "default",
    category: str = "fact",
    importance: int = 5,
) -> str:
    """Save permanent knowledge to long-term memory. Upserts by (agent_id, key).

    Use this for knowledge that should persist permanently: procedures, lessons,
    architecture decisions, facts.

    Args:
        key: Unique key (e.g., "deploy_process", "refund_policy").
        content: Knowledge content.
        agent_id: Agent saving the knowledge.
        category: Category: fact, procedure, lesson, pattern, policy, contact, troubleshooting.
        importance: 1-10 (8+ may promote to shared knowledge).
    """
    mem = _get_mem(ctx)
    entry = await mem.ltm.save(
        agent_id=agent_id, category=category,
        key=key, content=content, importance=importance,
    )
    return f"Saved LTM entry: {entry.key} (category={entry.category}, v{entry.version})"


@mcp.tool()
async def memory_save_shared(
    ctx: Context,
    key: str,
    content: str,
    category: str = "fact",
    importance: int = 5,
) -> str:
    """Save knowledge visible to all agents. Good for shared facts, policies, and procedures.

    Args:
        key: Unique key (e.g., "company_policy", "vendor_contact").
        content: Knowledge content.
        category: Category: fact, policy, procedure, vendor, insight.
        importance: 1-10.
    """
    mem = _get_mem(ctx)
    entry = await mem.shared.save(
        agent_id="mcp", category=category,
        key=key, content=content, importance=importance,
    )
    return f"Saved shared knowledge: {entry.key} (v{entry.version})"


@mcp.tool()
async def memory_stats(ctx: Context) -> str:
    """Get memory system statistics: tier counts, database info, and system status."""
    mem = _get_mem(ctx)
    from sqlalchemy import text

    tables = {
        "Working Memory": "nmem_working_memory",
        "Journal": "nmem_journal_entries",
        "Long-Term Memory": "nmem_long_term_memory",
        "Shared Knowledge": "nmem_shared_knowledge",
        "Entity Memory": "nmem_entity_memory",
        "Policies": "nmem_policies",
    }

    lines = ["Memory System Statistics:\n"]
    total = 0
    for label, tbl in tables.items():
        try:
            async with mem._db.session() as session:
                result = await session.execute(text(f"SELECT COUNT(*) FROM {tbl}"))
                count = result.scalar() or 0
        except Exception:
            count = 0
        total += count
        lines.append(f"  {label}: {count}")

    db_type = "PostgreSQL" if mem._db.is_postgres else "SQLite"
    lines.append(f"\nDatabase: {db_type}")
    lines.append(f"Embedding: {mem._config.embedding.provider}")
    lines.append(f"Total entries: {total}")

    return "\n".join(lines)


# ── Resources ────────────────────────────────────────────────────────────────


@mcp.resource("nmem://agent/{agent_id}/journal")
async def agent_journal(agent_id: str) -> str:
    """Recent journal entries for an agent (last 7 days)."""
    from nmem import MemorySystem
    from nmem.cli.config_loader import load_config

    config = load_config()
    mem = MemorySystem(config)
    await mem.initialize()
    try:
        entries = await mem.journal.recent(agent_id, days=7, limit=20)
        if not entries:
            return f"No journal entries for {agent_id} in the last 7 days."
        lines = [f"# Journal: {agent_id}\n"]
        for e in entries:
            ts = e.created_at.strftime("%Y-%m-%d") if e.created_at else "?"
            lines.append(f"## [{ts}] {e.title}")
            lines.append(f"*{e.entry_type} | importance: {e.importance}*\n")
            lines.append(e.content[:500])
            lines.append("")
        return "\n".join(lines)
    finally:
        await mem.close()


@mcp.resource("nmem://agent/{agent_id}/ltm")
async def agent_ltm(agent_id: str) -> str:
    """Long-term memory entries for an agent."""
    from nmem import MemorySystem
    from nmem.cli.config_loader import load_config

    config = load_config()
    mem = MemorySystem(config)
    await mem.initialize()
    try:
        from sqlalchemy import text
        async with mem._db.session() as session:
            result = await session.execute(
                text("SELECT key, category, content, importance FROM nmem_long_term_memory "
                     "WHERE agent_id = :agent_id ORDER BY importance DESC LIMIT 50"),
                {"agent_id": agent_id},
            )
            rows = result.all()
        if not rows:
            return f"No LTM entries for {agent_id}."
        lines = [f"# Long-Term Memory: {agent_id}\n"]
        for key, category, content, importance in rows:
            lines.append(f"## {key} [{category}] (importance: {importance})")
            lines.append(content[:500])
            lines.append("")
        return "\n".join(lines)
    finally:
        await mem.close()


@mcp.resource("nmem://shared")
async def shared_knowledge() -> str:
    """All shared knowledge entries."""
    from nmem import MemorySystem
    from nmem.cli.config_loader import load_config

    config = load_config()
    mem = MemorySystem(config)
    await mem.initialize()
    try:
        from sqlalchemy import text
        async with mem._db.session() as session:
            result = await session.execute(
                text("SELECT key, category, content, importance FROM nmem_shared_knowledge "
                     "ORDER BY importance DESC LIMIT 50")
            )
            rows = result.all()
        if not rows:
            return "No shared knowledge entries."
        lines = ["# Shared Knowledge\n"]
        for key, category, content, importance in rows:
            lines.append(f"## {key} [{category}] (importance: {importance})")
            lines.append(content[:500])
            lines.append("")
        return "\n".join(lines)
    finally:
        await mem.close()


# ── Entry Point ───────────────────────────────────────────────────────────────


def main():
    """Run the nmem MCP server (STDIO transport)."""
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
