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


_shared_mem = None  # Module-level reference for resources


@asynccontextmanager
async def lifespan(server: FastMCP):
    """Initialize MemorySystem on startup, close on shutdown."""
    global _shared_mem
    from nmem import MemorySystem
    from nmem.cli.config_loader import load_config

    config = load_config()
    # Inject project scope from environment if set
    import os
    env_scope = os.environ.get("NMEM_PROJECT_SCOPE")
    if env_scope and not config.project_scope:
        config = config.model_copy(update={"project_scope": env_scope})
    mem = MemorySystem(config)
    await mem.initialize()
    _shared_mem = mem
    scope_info = f", scope={config.project_scope}" if config.project_scope else ""
    logger.info("nmem MCP server initialized%s", scope_info)
    try:
        yield {"mem": mem}
    finally:
        _shared_mem = None
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
    importance: int | None = None,
    entry_type: str = "observation",
    tags: list[str] | None = None,
) -> str:
    """Store a memory in the journal. High-importance entries (>=7) auto-promote to long-term memory.

    Args:
        title: Short descriptive title.
        content: Full memory content.
        agent_id: Agent storing the memory.
        importance: 1-10 — or leave unset (default) to let the consolidation
            heuristic scorer manage it. Explicit values are preserved verbatim
            forever and never silently adjusted.
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
    all_scopes: bool = False,
) -> str:
    """Search across all memory tiers using hybrid vector + full-text search.

    By default, searches within the current project_scope (if set) plus global
    entries. Set all_scopes=True to search every scope — useful for finding
    cross-project patterns, portfolio-wide insights, or past lessons from
    other projects/customers.

    Args:
        query: Natural language search query.
        agent_id: Agent perspective for search.
        tiers: Comma-separated tier filter: journal,ltm,shared,entity,policy.
            Policy is NOT searched by default — add "policy" explicitly.
        top_k: Maximum results to return.
        all_scopes: If True, search across ALL project scopes (ignores current scope).
    """
    mem = _get_mem(ctx)
    tier_tuple = tuple(tiers.split(",")) if tiers else None
    scope_arg = "*" if all_scopes else ...
    results = await mem.search(
        agent_id=agent_id, query=query, tiers=tier_tuple, top_k=top_k,
        project_scope=scope_arg,
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
    importance: int | None = None,
) -> str:
    """Save permanent knowledge to long-term memory. Upserts by (agent_id, key).

    Use this for knowledge that should persist permanently: procedures, lessons,
    architecture decisions, facts.

    Args:
        key: Unique key (e.g., "deploy_process", "refund_policy").
        content: Knowledge content.
        agent_id: Agent saving the knowledge.
        category: Category: fact, procedure, lesson, pattern, policy, contact, troubleshooting.
        importance: 1-10 (8+ may promote to shared knowledge) — or leave unset
            to let the consolidation heuristic scorer manage it.
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
async def memory_linked(
    ctx: Context,
    entry_id: int,
    tier: str,
    link_types: str | None = None,
) -> str:
    """Find entries linked to a specific memory entry via associative knowledge links.

    Unlike semantic search, knowledge links connect entries that share entities,
    tags, temporal proximity, or causal relationships — even when they aren't
    semantically similar. Use this to discover orthogonal but contextually related
    knowledge (e.g., "what else happened around the time of this bug?").

    Args:
        entry_id: The source entry ID.
        tier: Source tier — one of "journal", "ltm", "shared", "entity".
        link_types: Optional comma-separated filter: "shared_entity,shared_tag,temporal,causal,pattern".
    """
    mem = _get_mem(ctx)
    types_filter = [t.strip() for t in link_types.split(",")] if link_types else None

    links = await mem.links.get_linked(
        entry_id=entry_id,
        tier=tier,
        link_types=types_filter,
    )

    if not links:
        return f"No links found for {tier}#{entry_id}."

    lines = [f"Found {len(links)} linked entries:\n"]
    for link in links:
        lines.append(f"  [{link.link_type}|{link.strength:.2f}] → {link.target_tier}#{link.target_id}")
        if link.evidence:
            lines.append(f"    {link.evidence}")
    return "\n".join(lines)


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
        "Policies": "nmem_policy_memory",
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


@mcp.tool()
async def memory_write_entity(
    ctx: Context,
    entity_type: str,
    entity_id: str,
    entity_name: str,
    content: str,
    agent_id: str = "default",
    record_type: str = "evidence",
    grounding: str = "inferred",
    confidence: float = 0.8,
    tags: list[str] | None = None,
) -> str:
    """Write a typed entity record with explicit grounding lifecycle.

    Entity memory stores evidence, judgments, tasks, and summaries tied to a
    specific entity (person, product, bug, etc.) with a grounding lifecycle:
    - source_material: directly quoted from a document / API response
    - inferred: derived by reasoning (default)
    - confirmed: validated against a second independent source
    - disputed: contradicted by other evidence (see memory_check_conflicts)

    Args:
        entity_type: Category label (e.g. "person", "bug", "product").
        entity_id: Stable identifier you supply (e.g. "PR-1234", "user:alice").
        entity_name: Human-readable label.
        content: The record content.
        agent_id: Agent writing the record.
        record_type: evidence | judgment | task | summary.
        grounding: source_material | inferred | confirmed | disputed.
        confidence: 0.0-1.0.
        tags: Optional tags for filtering.
    """
    mem = _get_mem(ctx)

    valid_record_types = {"evidence", "judgment", "task", "summary"}
    if record_type not in valid_record_types:
        return f"Error: record_type must be one of: {', '.join(sorted(valid_record_types))}"

    valid_groundings = {"source_material", "inferred", "confirmed", "disputed"}
    if grounding not in valid_groundings:
        return f"Error: grounding must be one of: {', '.join(sorted(valid_groundings))}"

    confidence = max(0.0, min(1.0, confidence))

    try:
        record = await mem.entity.save(
            entity_type=entity_type,
            entity_id=entity_id,
            entity_name=entity_name,
            agent_id=agent_id,
            content=content,
            record_type=record_type,
            confidence=confidence,
            grounding=grounding,
            tags=tags,
        )
    except Exception as e:
        return f"Error: {e}"

    return (
        f"Saved entity record #{record.id}: {entity_type}/{entity_name} "
        f"(type={record_type}, grounding={grounding}, confidence={confidence:.2f})"
    )


@mcp.tool()
async def memory_write_policy(
    ctx: Context,
    scope: str,
    category: str,
    key: str,
    content: str,
    agent_id: str = "default",
) -> str:
    """Write a governance policy, upserting on (scope, key).

    Policies are system-wide rules like "sales agents may send refunds up to
    $500 without approval" or "never auto-merge PRs touching auth middleware".

    Scope examples:
    - "global" — applies everywhere
    - "agent:sales" — applies to one agent
    - "entity_type:lead" — applies when operating on a specific entity type

    Writing the same (scope, key) twice updates the existing policy and
    increments its version. Use memory_search(..., tiers="policy") to read back.

    Args:
        scope: Policy scope (e.g. "global", "agent:sales").
        category: e.g. "escalation", "approval", "autonomy".
        key: Unique key within scope.
        content: Full policy text.
        agent_id: Agent proposing the policy (recorded as created_by).
    """
    mem = _get_mem(ctx)

    if not scope or not key:
        return "Error: scope and key are required"

    try:
        entry = await mem.policy.save(
            scope=scope, category=category,
            key=key, content=content,
            agent_id=agent_id,
        )
    except Exception as e:
        return f"Error: {e}"

    return f"Saved policy: [{entry.scope}/{entry.category}] {entry.key} (v{entry.version})"


@mcp.tool()
async def memory_check_conflicts(
    ctx: Context,
    status: str = "open",
    agent_id: str | None = None,
    limit: int = 20,
    since_days: int | None = None,
    all_scopes: bool = False,
) -> str:
    """List memory conflicts detected by the automatic scanner.

    Conflicts are raised when two memory records in LTM or shared knowledge
    have high text overlap but divergent vector similarity, suggesting they
    say contradictory things about the same topic.

    Use this to review what the scanner flagged for arbitration, or to check
    for poisoning before trusting retrieval results.

    By default, returns conflicts from the current project_scope (if set)
    plus global conflicts. Set all_scopes=True to see conflicts across
    every scope.

    Args:
        status: Comma-separated status filter. Default: "open".
                Valid: open, needs_review, manual, auto_resolved, stale.
        agent_id: Filter to conflicts involving this agent (either side).
        limit: Maximum conflicts to return (default 20).
        since_days: Only conflicts created in the last N days.
        all_scopes: If True, return conflicts from ALL project scopes.
    """
    mem = _get_mem(ctx)
    from nmem.conflicts import list_conflicts

    valid_statuses = {"open", "needs_review", "manual", "auto_resolved", "stale"}
    status_tuple = tuple(s.strip() for s in status.split(","))
    invalid = set(status_tuple) - valid_statuses
    if invalid:
        return f"Error: invalid status value(s): {invalid}. Valid: {', '.join(sorted(valid_statuses))}"

    scope_arg = "*" if all_scopes else mem._config.project_scope

    conflicts = await list_conflicts(
        mem._db,
        status=status_tuple,
        agent_id=agent_id,
        project_scope=scope_arg,
        limit=limit,
        since_days=since_days,
    )

    if not conflicts:
        return "No conflicts matching filter."

    lines = [f"Found {len(conflicts)} conflict(s):\n"]
    for i, c in enumerate(conflicts, 1):
        ts = c.created_at.strftime("%Y-%m-%d") if c.created_at else "?"
        scope_label = f" scope={c.project_scope}" if c.project_scope else ""
        lines.append(
            f"{i}. [{c.status.upper()}] similarity={c.similarity_score:.2f} created={ts}{scope_label}"
        )
        lines.append(f"   A: {c.record_a_table}#{c.record_a_id} (agent={c.agent_a})")
        lines.append(f"   B: {c.record_b_table}#{c.record_b_id} (agent={c.agent_b})")
        lines.append(f"   {c.description[:200]}")
        lines.append("")

    return "\n".join(lines)


@mcp.tool()
async def memory_mark_grounding(
    ctx: Context,
    entity_record_id: int,
    grounding: str,
    evidence_ref: str | None = None,
    agent_id: str = "default",
) -> str:
    """Transition an entity record's grounding lifecycle.

    Use after gathering evidence to move a record from 'inferred' to
    'confirmed', or after finding contradicting evidence to move it to
    'disputed'.

    Valid transitions:
    - inferred -> confirmed (verified via second source)
    - inferred -> disputed (contradicted by other evidence)
    - confirmed -> disputed (later contradicted)
    - source_material -> * (rarely needed)

    Args:
        entity_record_id: ID of the entity record to update.
        grounding: New grounding value: source_material | inferred | confirmed | disputed.
        evidence_ref: Free-text reference explaining the transition (URL, doc id).
        agent_id: Agent making the transition.
    """
    mem = _get_mem(ctx)

    valid_groundings = {"source_material", "inferred", "confirmed", "disputed"}
    if grounding not in valid_groundings:
        return f"Error: grounding must be one of: {', '.join(sorted(valid_groundings))}"

    try:
        record = await mem.entity.update_grounding(
            record_id=entity_record_id,
            grounding=grounding,
            evidence_ref=evidence_ref,
            agent_id=agent_id,
        )
    except KeyError:
        return f"Error: entity record #{entity_record_id} not found"
    except Exception as e:
        return f"Error: {e}"

    # update_grounding returns early without an audit entry on no-op,
    # so check whether the latest evidence_refs entry is our transition.
    last = record.evidence_refs[-1] if record.evidence_refs else None
    if last and last.get("type") == "grounding_transition" and last.get("to") == grounding:
        return f"Entity record #{record.id}: grounding {last['from']} → {grounding}"

    return f"Entity record #{record.id}: grounding already '{grounding}' (no change)"


# ── Resources ────────────────────────────────────────────────────────────────


@mcp.resource("nmem://agent/{agent_id}/journal")
async def agent_journal(agent_id: str) -> str:
    """Recent journal entries for an agent (last 7 days)."""
    if not _shared_mem:
        return "nmem not initialized"
    entries = await _shared_mem.journal.recent(agent_id, days=7, limit=20)
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


@mcp.resource("nmem://agent/{agent_id}/ltm")
async def agent_ltm(agent_id: str) -> str:
    """Long-term memory entries for an agent."""
    if not _shared_mem:
        return "nmem not initialized"
    from sqlalchemy import text as sa_text
    async with _shared_mem._db.session() as session:
        result = await session.execute(
            sa_text("SELECT key, category, content, importance FROM nmem_long_term_memory "
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


@mcp.resource("nmem://shared")
async def shared_knowledge() -> str:
    """All shared knowledge entries."""
    if not _shared_mem:
        return "nmem not initialized"
    from sqlalchemy import text as sa_text
    async with _shared_mem._db.session() as session:
        result = await session.execute(
            sa_text("SELECT key, category, content, importance FROM nmem_shared_knowledge "
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


# ── Entry Point ───────────────────────────────────────────────────────────────


def main():
    """Run the nmem MCP server (STDIO transport)."""
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
