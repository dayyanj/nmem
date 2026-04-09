"""nmem doctor — diagnose installation, config, and connectivity."""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

from nmem.cli.output import console


def doctor():
    """Diagnose nmem installation, configuration, and connectivity."""
    from rich.panel import Panel

    console.print()
    console.print("[bold]nmem doctor[/bold]")
    console.print()

    checks: list[tuple[str, bool, str]] = []  # (label, passed, detail)
    fixes: list[str] = []

    # ── 1. nmem installed ─────────────────────────────────────────
    try:
        from nmem import __version__
        checks.append(("nmem installed", True, f"v{__version__}"))
    except ImportError:
        checks.append(("nmem installed", False, "not found"))
        fixes.append("pip install nmem")

    # ── 2. Database URL configured ────────────────────────────────
    import os
    db_url = os.environ.get("NMEM_DATABASE_URL", "")

    # Check nmem.toml too
    toml_path = Path.cwd() / "nmem.toml"
    toml_db = ""
    if toml_path.exists():
        try:
            if sys.version_info >= (3, 11):
                import tomllib
            else:
                import tomli as tomllib
            with open(toml_path, "rb") as f:
                data = tomllib.load(f)
            toml_db = data.get("database_url", "")
        except Exception:
            pass

    effective_db = db_url or toml_db
    if effective_db:
        # Mask password
        display = effective_db
        if "@" in display:
            display = "***@" + display.split("@")[-1]
        is_pg = "postgresql" in effective_db
        checks.append(("Database configured", True,
                        f"{'PostgreSQL' if is_pg else 'SQLite'}: {display}"))
    else:
        checks.append(("Database configured", False, "no NMEM_DATABASE_URL or nmem.toml"))
        fixes.append('export NMEM_DATABASE_URL="postgresql+asyncpg://nmem:nmem@localhost:5433/nmem"')
        fixes.append("# or: nmem init --sqlite")

    # ── 3. Database connectable ───────────────────────────────────
    if effective_db:
        import asyncio

        async def _check_db():
            try:
                from nmem.db.session import DatabaseManager
                db = DatabaseManager(effective_db)
                from sqlalchemy import text
                async with db.session() as session:
                    await session.execute(text("SELECT 1"))
                await db.close()
                return True, "connected"
            except Exception as e:
                return False, str(e)[:80]

        db_ok, db_detail = asyncio.run(_check_db())
        checks.append(("Database reachable", db_ok, db_detail))
        if not db_ok:
            if "postgresql" in effective_db:
                fixes.append("docker compose up -d  # Start PostgreSQL")
            fixes.append("nmem init  # Create tables")
    else:
        checks.append(("Database reachable", False, "no URL configured"))

    # ── 4. Tables exist ───────────────────────────────────────────
    if effective_db and db_ok:
        async def _check_tables():
            try:
                from nmem.db.session import DatabaseManager
                from sqlalchemy import text
                db = DatabaseManager(effective_db)
                async with db.session() as session:
                    await session.execute(text("SELECT COUNT(*) FROM nmem_metadata"))
                await db.close()
                return True, "initialized"
            except Exception:
                return False, "tables not found"

        tbl_ok, tbl_detail = asyncio.run(_check_tables())
        checks.append(("Tables initialized", tbl_ok, tbl_detail))
        if not tbl_ok:
            fixes.append("nmem init")

    # ── 5. Embedding provider ─────────────────────────────────────
    emb_provider = os.environ.get("NMEM_EMBEDDING__PROVIDER", "")
    if not emb_provider and toml_path.exists():
        try:
            emb_provider = data.get("embedding", {}).get("provider", "")
        except Exception:
            pass

    if emb_provider and emb_provider != "noop":
        if emb_provider == "sentence-transformers":
            try:
                import sentence_transformers
                checks.append(("Embedding provider", True, f"sentence-transformers"))
            except ImportError:
                checks.append(("Embedding provider", False, "sentence-transformers not installed"))
                fixes.append("pip install 'nmem[st]'")
        elif emb_provider == "openai":
            try:
                import openai
                checks.append(("Embedding provider", True, "openai"))
            except ImportError:
                checks.append(("Embedding provider", False, "openai package not installed"))
                fixes.append("pip install 'nmem[openai]'")
        else:
            checks.append(("Embedding provider", True, emb_provider))
    elif emb_provider == "noop":
        checks.append(("Embedding provider", True, "noop (hash-based, testing only)"))
    else:
        checks.append(("Embedding provider", False, "not configured (defaults to noop)"))
        fixes.append("export NMEM_EMBEDDING__PROVIDER=sentence-transformers && pip install 'nmem[st]'")

    # ── 6. LLM provider ──────────────────────────────────────────
    llm_provider = os.environ.get("NMEM_LLM__PROVIDER", "")
    if not llm_provider and toml_path.exists():
        try:
            llm_provider = data.get("llm", {}).get("provider", "")
        except Exception:
            pass

    if llm_provider and llm_provider != "noop":
        checks.append(("LLM provider", True, llm_provider))
    else:
        checks.append(("LLM provider", False, "not configured (compression/synthesis disabled)"))

    # ── 7. nmem-mcp on PATH ──────────────────────────────────────
    if shutil.which("nmem-mcp"):
        checks.append(("MCP server (nmem-mcp)", True, "on PATH"))
    else:
        checks.append(("MCP server (nmem-mcp)", False, "not on PATH"))
        fixes.append("pip install 'nmem[mcp-server]'  # Then ensure venv is on PATH")

    # ── 8. Claude Code integration ────────────────────────────────
    claude_json = Path.cwd() / ".claude.json"
    if claude_json.exists():
        try:
            import json
            config = json.loads(claude_json.read_text())
            if "nmem" in config.get("mcpServers", {}):
                checks.append(("Claude Code MCP", True, "configured in .claude.json"))
            else:
                checks.append(("Claude Code MCP", False, "not in .claude.json"))
                fixes.append("nmem setup")
        except Exception:
            checks.append(("Claude Code MCP", False, "invalid .claude.json"))
    else:
        checks.append(("Claude Code MCP", False, "no .claude.json"))
        fixes.append("nmem setup")

    # ── 9. CLAUDE.md has nmem instructions ────────────────────────
    claude_md = Path.cwd() / "CLAUDE.md"
    if claude_md.exists():
        content = claude_md.read_text()
        if "Agent Memory (nmem)" in content:
            checks.append(("CLAUDE.md instructions", True, "nmem section present"))
        else:
            checks.append(("CLAUDE.md instructions", False, "no nmem section"))
            fixes.append("nmem setup --auto-append")
    else:
        checks.append(("CLAUDE.md instructions", False, "no CLAUDE.md"))
        fixes.append("nmem setup --auto-append")

    # ── 10. AGENTS.md exists ──────────────────────────────────────
    agents_md = Path.cwd() / "AGENTS.md"
    if agents_md.exists():
        content = agents_md.read_text()
        if "nmem" in content.lower():
            checks.append(("AGENTS.md", True, "nmem section present"))
        else:
            checks.append(("AGENTS.md", False, "no nmem section"))
            fixes.append("nmem setup --agents-md")
    else:
        checks.append(("AGENTS.md", False, "not found"))

    # ── Print Results ─────────────────────────────────────────────
    passed = sum(1 for _, ok, _ in checks if ok)
    total = len(checks)

    for label, ok, detail in checks:
        icon = "[green]\u2713[/green]" if ok else "[red]\u2717[/red]"
        detail_style = "" if ok else "[dim]"
        detail_end = "" if ok else "[/dim]"
        console.print(f"  {icon} {label}: {detail_style}{detail}{detail_end}")

    console.print()
    if passed == total:
        console.print(f"[bold green]All {total} checks passed![/bold green]")
    else:
        console.print(f"[bold]{passed}/{total} checks passed[/bold]")

    if fixes:
        console.print()
        console.print("[bold]Suggested fixes:[/bold]")
        for fix in fixes:
            console.print(f"  [cyan]{fix}[/cyan]")

    console.print()
