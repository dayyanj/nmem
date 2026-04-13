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

This project uses nmem for persistent cognitive memory via MCP. Tools are
grouped by purpose:

### Retrieval — before starting work
- `memory_search(query, tiers?)` — hybrid search across tiers. Default tiers:
  journal, ltm, shared, entity. Add "policy" to search governance rules.
- `memory_context(query)` — full formatted context for prompt injection
- `memory_recall(agent_id, days)` — recent journal entries
- `memory_linked(entry_id, tier)` — associative traversal (what else is
  related via shared entities, tags, or temporal proximity?)

### Writes — after work, by purpose
- `memory_store(title, content, importance)` — ephemeral observation,
  lesson learned, session summary (journal; auto-promotes at importance >=7)
- `memory_save_ltm(key, content)` — permanent knowledge, upserts by key
- `memory_save_shared(key, content)` — cross-agent facts
- `memory_write_entity(entity_type, entity_id, entity_name, content,
  record_type, grounding)` — typed facts ABOUT a specific entity with
  explicit grounding lifecycle
- `memory_write_policy(scope, category, key, content)` — governance rules

### Integrity — verify memory health
- `memory_check_conflicts(status?, all_scopes?)` — contradictions the scanner flagged
- `memory_mark_grounding(entity_record_id, grounding, evidence_ref)` —
  transition an entity record between inferred / confirmed / disputed
- `memory_stats()` — tier counts, DB info

### When to use which tier (decision tree)

Storing something? Ask in order:
1. Is it a rule everyone must follow? -> `memory_write_policy`
2. Is it a fact ABOUT a specific entity (person, bug, product)?
   -> `memory_write_entity`
3. Is it permanent knowledge with a natural key (procedure, decision)?
   -> `memory_save_ltm`
4. Should all agents see it? -> `memory_save_shared`
5. Otherwise (observation, lesson, session note) -> `memory_store`

Retrieving something? Ask:
- Need facts about a known entity? -> `memory_search` with
  `tiers="entity"`, or `memory_linked` from a known starting point
- Need governance rules? -> `memory_search` with `tiers="policy"`
- Need past lessons for debugging/implementing? -> `memory_search` default
- Need recent activity? -> `memory_recall`

### Integrity discipline
Before trusting a high-stakes retrieval, call `memory_check_conflicts` —
if the scanner flagged contradictions touching your topic, don't act on
the result until you've arbitrated.

After confirming an `inferred` entity record via a second source, call
`memory_mark_grounding(..., grounding="confirmed", evidence_ref=...)`
so future retrievals know the record is trustworthy.

### Importance guide
- 1-4: Low — transient observations
- 5-6: Medium — useful context
- 7-8: High — auto-promotes to permanent memory
- 9-10: Critical — architecture decisions, incident post-mortems

### What NOT to store
- Code (it's in git)
- Ephemeral debugging state
- Anything already in this CLAUDE.md file
'''.strip()

# AGENTS.md content — framework-agnostic AI agent instructions
AGENTS_MD_CONTENT = '''# AI Agent Instructions

## Memory System

This project uses [nmem](https://github.com/spwig/nmem) for persistent cognitive memory.
nmem is available as an MCP server — check your tool list for `memory_*` tools.

### Available memory tools

#### Retrieval
| Tool | When to use |
|------|-------------|
| `memory_search(query, tiers?)` | Before starting work — hybrid search. Default: journal,ltm,shared,entity. Add "policy" for governance rules. |
| `memory_context(query)` | Get full formatted memory context for a topic |
| `memory_recall(agent_id, days)` | See recent journal activity |
| `memory_linked(entry_id, tier)` | Associative traversal — related entries via tags, entities, or temporal proximity |

#### Writes
| Tool | When to use |
|------|-------------|
| `memory_store(title, content, importance)` | Observations, lessons, session notes (journal; auto-promotes at importance >=7) |
| `memory_save_ltm(key, content, category)` | Permanent knowledge — procedures, architecture, lessons (upserts by key) |
| `memory_save_shared(key, content)` | Cross-agent facts all agents should know |
| `memory_write_entity(entity_type, entity_id, entity_name, content, record_type, grounding)` | Typed facts ABOUT a specific entity with grounding lifecycle |
| `memory_write_policy(scope, category, key, content)` | Governance rules (upserts on scope+key) |

#### Integrity
| Tool | When to use |
|------|-------------|
| `memory_check_conflicts(status?, all_scopes?)` | Review contradictions the scanner flagged (scoped by default) |
| `memory_mark_grounding(entity_record_id, grounding, evidence_ref)` | Transition entity grounding: inferred -> confirmed / disputed |
| `memory_stats()` | See tier counts and system status |

### Which tier to use (decision tree)

Storing something? Ask in order:
1. Is it a rule everyone must follow? -> `memory_write_policy`
2. Is it a fact ABOUT a specific entity (person, bug, product)? -> `memory_write_entity`
3. Is it permanent knowledge with a natural key? -> `memory_save_ltm`
4. Should all agents see it? -> `memory_save_shared`
5. Otherwise -> `memory_store`

### Importance scale

- **1-4**: Transient — will expire in 30 days
- **5-6**: Useful — may promote if accessed frequently
- **7-8**: Important — auto-promotes to permanent long-term memory
- **9-10**: Critical — architecture decisions, incident learnings

### What NOT to store

- Code (it's in git)
- Ephemeral debugging state
- Anything already documented in project files
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
    agents_md: Annotated[bool, typer.Option("--agents-md",
        help="Generate AGENTS.md for AI agent instructions")] = False,
    hooks: Annotated[bool, typer.Option("--hooks",
        help="Install auto-capture hooks for Claude Code")] = False,
):
    """Configure MCP server, generate CLAUDE.md snippet, and optionally create AGENTS.md."""
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
    # Detect project scope from CWD
    import os
    cwd = os.getcwd()
    project_scope = f"project:{cwd}"

    mcp_servers["nmem"] = {
        "command": "nmem-mcp",
        "env": {
            "NMEM_DATABASE_URL": db_url,
            "NMEM_EMBEDDING__PROVIDER": embedding_provider,
            "NMEM_PROJECT_SCOPE": project_scope,
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

    # ── 3. Generate AGENTS.md (if requested) ─────────────────────────
    agents_md_path = project_dir / "AGENTS.md"
    if agents_md:
        if agents_md_path.exists():
            existing = agents_md_path.read_text()
            if "nmem" not in existing.lower():
                agents_md_path.write_text(existing.rstrip() + "\n\n" + AGENTS_MD_CONTENT + "\n")
                console.print(f"[green]nmem instructions appended to {agents_md_path}[/green]")
            else:
                console.print(f"[yellow]AGENTS.md already contains nmem instructions[/yellow]")
        else:
            agents_md_path.write_text(AGENTS_MD_CONTENT + "\n")
            console.print(f"[green]Created {agents_md_path}[/green]")

    # ── 4. Install auto-capture hooks (if requested) ──────────────────
    if hooks:
        _install_hooks(project_dir, db_url, embedding_provider, project_scope)

    # ── 5. Print summary ─────────────────────────────────────────────
    console.print()
    console.print("[bold]Setup complete![/bold]")
    console.print()
    console.print("[dim]What was configured:[/dim]")
    console.print(f"  MCP server:  [cyan]{claude_json_path}[/cyan]")
    console.print(f"  Database:    [cyan]{db_url}[/cyan]")
    console.print(f"  Embedding:   [cyan]{embedding_provider}[/cyan]")
    if hooks:
        console.print(f"  Hooks:       [cyan]auto-capture enabled[/cyan]")
    console.print()
    console.print("[dim]Next steps:[/dim]")
    console.print("  1. Restart Claude Code to pick up the MCP server")
    console.print("  2. Add the CLAUDE.md snippet to your project")
    console.print("  3. Try: ask Claude to 'search memory for deployment process'")
    if not auto_append:
        console.print()
        console.print("[dim]Tip: run with --auto-append to add instructions to CLAUDE.md automatically[/dim]")


def _install_hooks(project_dir: Path, db_url: str, embedding_provider: str, project_scope: str) -> None:
    """Install Claude Code auto-capture hooks.

    Configures hooks in the project's .claude/settings.json that call
    nmem's hook entry points for automatic knowledge capture.
    """
    import shutil

    # Check if nmem is installed (for hook scripts to work)
    python_path = shutil.which("python3") or shutil.which("python")
    if not python_path:
        console.print("[red]Error: python3 not found on PATH[/red]")
        return

    # Claude Code hooks are configured in .claude/settings.json (project-level)
    # or ~/.claude/settings.json (global)
    claude_dir = project_dir / ".claude"
    claude_dir.mkdir(exist_ok=True)

    settings_path = claude_dir / "settings.json"
    settings = {}
    if settings_path.exists():
        try:
            settings = json.loads(settings_path.read_text())
        except json.JSONDecodeError:
            pass

    # Define hook commands using Python module entry points
    env_block = {
        "NMEM_DATABASE_URL": db_url,
        "NMEM_EMBEDDING__PROVIDER": embedding_provider,
        "NMEM_PROJECT_SCOPE": project_scope,
    }

    hooks_config = settings.setdefault("hooks", {})

    # PostToolUse hook — capture edits and bash commands
    hooks_config["PostToolUse"] = [
        {
            "type": "command",
            "command": f"{python_path} -m nmem.hooks post_tool_use",
            "timeout": 5000,
            "env": env_block,
        },
    ]

    # Stop hook — session end summary
    hooks_config["Stop"] = [
        {
            "type": "command",
            "command": f"{python_path} -m nmem.hooks session_end",
            "timeout": 15000,
            "env": env_block,
        },
    ]

    settings_path.write_text(json.dumps(settings, indent=2) + "\n")
    console.print(f"[green]Auto-capture hooks installed in {settings_path}[/green]")
    console.print("[dim]  PostToolUse: captures edits and bash commands[/dim]")
    console.print("[dim]  Stop: writes session summary to journal[/dim]")

    # Create nmem.toml with default hook config if it doesn't exist
    config_path = project_dir / "nmem.toml"
    if not config_path.exists():
        config_path.write_text(
            '# nmem hook configuration\n'
            '[hooks]\n'
            'enabled = true\n'
            'capture_edits = true\n'
            'capture_bash = true\n'
            'capture_reads = false  # too noisy by default\n'
            'session_summary = true\n'
            'summary_llm = false\n'
            '\n'
            '[hooks.filters]\n'
            'skip_paths = ["node_modules/", ".git/", "__pycache__/", ".venv/"]\n'
            'skip_commands = ["ls", "pwd", "cd", "echo"]\n'
        )
        console.print(f"[green]Hook config created at {config_path}[/green]")
