"""nmem setup — configure MCP server and generate CLAUDE.md snippet."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer

from nmem.cli.output import console

# The CLAUDE.md snippet that teaches Claude to use nmem MCP tools
CLAUDE_MD_SNIPPET = '''
## Agent Memory (nmem)

This project uses nmem for persistent cognitive memory via MCP. You have access to these memory tools:

### When to use memory

**At the start of complex tasks**, search for relevant context:
- Before debugging: `memory_search("error X in module Y")` — check if this was solved before
- Before implementing: `memory_search("feature X design decisions")` — check for past decisions
- Before refactoring: `memory_search("module X architecture")` — check for known constraints

**After completing significant work**, store what you learned:
- Bug fixes: `memory_store(title="Fixed X by doing Y", content="Root cause was...", importance=7)`
- Design decisions: `memory_save_ltm(key="auth_architecture", content="We chose JWT because...", category="architecture", importance=8)`
- Lessons learned: `memory_store(title="Never do X because Y", content="...", importance=8, entry_type="lesson_learned")`
- Discovered constraints: `memory_save_ltm(key="db_connection_limit", content="Max 20 connections...", category="constraint", importance=7)`

**For shared team knowledge**, save cross-agent facts:
- `memory_save_shared(key="deploy_process", content="1. Run migrations...", category="procedure", importance=8)`

### Importance guide
- 1-4: Low — transient observations, minor notes
- 5-6: Medium — useful context, session summaries
- 7-8: High — lessons learned, procedures, decisions (auto-promotes to permanent memory)
- 9-10: Critical — architecture decisions, incident post-mortems, policies

### What NOT to store
- Code snippets (they're in git)
- Temporary debugging state (use working memory for that)
- Anything already in this CLAUDE.md file
'''.strip()


def setup(
    database_url: Annotated[str | None, typer.Option("--database-url", "-d",
        help="Database URL for MCP server")] = None,
    embedding_provider: Annotated[str, typer.Option("--embedding",
        help="Embedding provider")] = "sentence-transformers",
    project_dir: Annotated[Path, typer.Option("--project-dir", "-p",
        help="Project directory (for .claude.json and CLAUDE.md)")] = Path.cwd(),
    auto_append: Annotated[bool, typer.Option("--auto-append",
        help="Auto-append memory instructions to CLAUDE.md")] = False,
):
    """Configure nmem MCP server for Claude Code and generate CLAUDE.md instructions."""
    # Determine database URL
    db_url = database_url or "postgresql+asyncpg://nmem:nmem@localhost:5433/nmem"

    # ── 0. Verify nmem-mcp is available ────────────────────────────
    import shutil
    if not shutil.which("nmem-mcp"):
        console.print(
            "[yellow]Warning:[/yellow] [bold]nmem-mcp[/bold] not found on PATH.\n"
            "  Claude Code won't be able to start the MCP server.\n"
            "  Install with: [cyan]pip install nmem\\[mcp-server][/cyan]\n"
            "  Or if using a venv, ensure it's activated when Claude Code runs.\n"
        )

    # ── 1. Configure MCP server in .claude.json ──────────────────────
    claude_json_path = project_dir / ".claude.json"
    claude_config = {}
    if claude_json_path.exists():
        try:
            claude_config = json.loads(claude_json_path.read_text())
        except json.JSONDecodeError:
            pass

    mcp_servers = claude_config.setdefault("mcpServers", {})
    mcp_servers["nmem"] = {
        "command": "nmem-mcp",
        "env": {
            "NMEM_DATABASE_URL": db_url,
            "NMEM_EMBEDDING__PROVIDER": embedding_provider,
        },
    }

    claude_json_path.write_text(json.dumps(claude_config, indent=2) + "\n")
    console.print(f"[green]MCP server configured in {claude_json_path}[/green]")

    # ── 2. Generate CLAUDE.md snippet ────────────────────────────────
    claude_md_path = project_dir / "CLAUDE.md"

    if auto_append and claude_md_path.exists():
        existing = claude_md_path.read_text()
        if "Agent Memory (nmem)" not in existing:
            claude_md_path.write_text(existing.rstrip() + "\n\n" + CLAUDE_MD_SNIPPET + "\n")
            console.print(f"[green]Memory instructions appended to {claude_md_path}[/green]")
        else:
            console.print(f"[yellow]CLAUDE.md already contains nmem instructions[/yellow]")
    else:
        console.print()
        console.print("[bold]Add this to your project's CLAUDE.md:[/bold]")
        console.print()
        from rich.panel import Panel
        from rich.syntax import Syntax
        console.print(Panel(
            Syntax(CLAUDE_MD_SNIPPET, "markdown", theme="monokai"),
            title="CLAUDE.md snippet",
            border_style="cyan",
        ))

    # ── 3. Print summary ─────────────────────────────────────────────
    console.print()
    console.print("[bold]Setup complete![/bold]")
    console.print()
    console.print("[dim]What was configured:[/dim]")
    console.print(f"  MCP server:  [cyan]{claude_json_path}[/cyan]")
    console.print(f"  Database:    [cyan]{db_url}[/cyan]")
    console.print(f"  Embedding:   [cyan]{embedding_provider}[/cyan]")
    console.print()
    console.print("[dim]Next steps:[/dim]")
    console.print("  1. Restart Claude Code to pick up the MCP server")
    console.print("  2. Add the CLAUDE.md snippet to your project")
    console.print("  3. Try: ask Claude to 'search memory for deployment process'")
    if not auto_append:
        console.print()
        console.print("[dim]Tip: run with --auto-append to add instructions to CLAUDE.md automatically[/dim]")
