"""nmem init — initialize database and tables."""

from __future__ import annotations

from typing import Annotated

import typer

from nmem.cli.output import console, run_async, get_mem


def init(
    database_url: Annotated[str | None, typer.Option("--database-url", "-d",
        help="Database URL (overrides config/env)")] = None,
    sqlite: Annotated[bool, typer.Option("--sqlite",
        help="Use SQLite in current directory (zero-config)")] = False,
):
    """Initialize the nmem database — create tables and indexes."""
    overrides = {}
    if sqlite:
        overrides["database_url"] = "sqlite+aiosqlite:///nmem.db"
        overrides["embedding"] = {"provider": "noop"}
    if database_url:
        overrides["database_url"] = database_url

    async def _init():
        async with get_mem(**overrides) as mem:
            from sqlalchemy import text

            # Count rows per tier
            tier_counts = {}
            tables = {
                "Working": "nmem_working_memory",
                "Journal": "nmem_journal_entries",
                "LTM": "nmem_long_term_memory",
                "Shared": "nmem_shared_knowledge",
                "Entity": "nmem_entity_memory",
                "Policy": "nmem_policies",
            }
            for label, table in tables.items():
                try:
                    async with mem._db.session() as session:
                        result = await session.execute(text(f"SELECT COUNT(*) FROM {table}"))
                        tier_counts[label] = result.scalar() or 0
                except Exception:
                    tier_counts[label] = 0

            db_url = mem._config.database_url
            # Mask password in URL
            if "@" in db_url:
                parts = db_url.split("@")
                db_url = "***@" + parts[-1]

            from rich.table import Table
            from rich.panel import Panel

            table = Table(show_header=True)
            table.add_column("Tier", style="bold")
            table.add_column("Entries", style="cyan", justify="right")
            for label, count in tier_counts.items():
                table.add_row(label, str(count))

            console.print(Panel(
                f"[green]Database initialized successfully[/green]\n\n"
                f"  Database: [cyan]{db_url}[/cyan]\n"
                f"  Embedding: [cyan]{mem._config.embedding.provider}[/cyan]\n"
                f"  LLM: [cyan]{mem._config.llm.provider}[/cyan]",
                title="nmem init",
            ))
            console.print(table)
            console.print()
            console.print("[dim]Next steps:[/dim]")
            console.print("  nmem demo                    [dim]— Run interactive demo[/dim]")
            console.print("  nmem import claude-code      [dim]— Import Claude Code memories[/dim]")
            console.print("  nmem import markdown ./docs  [dim]— Import markdown files[/dim]")

    run_async(_init())
