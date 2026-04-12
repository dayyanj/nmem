"""
Shared CLI output utilities — console, async bridging, formatting.
"""

from __future__ import annotations

import asyncio
import sys
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from rich.console import Console
from rich.table import Table
from rich.panel import Panel

from nmem.cli.config_loader import load_config

console = Console()


def run_async(coro):
    """Run an async coroutine from sync CLI context."""
    try:
        return asyncio.run(coro)
    except KeyboardInterrupt:
        console.print("\n[dim]Interrupted[/dim]")
        sys.exit(1)
    except Exception as e:
        err = str(e)
        if "does not exist" in err or "no such table" in err:
            console.print("[red]Database not initialized.[/red] Run [bold]nmem init[/bold] first.")
            sys.exit(1)
        if "password authentication failed" in err or "Connection refused" in err:
            console.print(f"[red]Database connection failed:[/red] {err}")
            console.print("[dim]Check NMEM_DATABASE_URL or use --sqlite for local testing.[/dim]")
            sys.exit(1)
        raise


@asynccontextmanager
async def get_mem(**overrides: Any) -> AsyncIterator:
    """Create and initialize a MemorySystem, yield it, then close."""
    from nmem import MemorySystem

    config = load_config(**overrides)
    mem = MemorySystem(config)
    try:
        await mem.initialize()
        yield mem
    finally:
        await mem.close()


def print_results(results: list, title: str = "Search Results") -> None:
    """Print search results as a rich table."""
    if not results:
        console.print("[dim]No results found.[/dim]")
        return

    table = Table(title=title, show_lines=True)
    table.add_column("Tier", style="cyan", width=8)
    table.add_column("Score", style="yellow", width=7)
    table.add_column("Title / Key", style="bold", max_width=30)
    table.add_column("Content", max_width=60)

    for r in results:
        title_str = getattr(r, "title", None) or getattr(r, "key", None) or ""
        content = (r.content or "")[:80]
        if len(r.content or "") > 80:
            content += "..."
        score = f"{r.score:.3f}" if isinstance(r.score, float) else str(r.score)
        table.add_row(r.tier, score, str(title_str)[:30], content)

    console.print(table)


def print_import_result(result, source: str) -> None:
    """Print import results summary."""
    console.print(Panel(
        f"[green]Imported:[/green] {result.imported}  "
        f"[yellow]Skipped:[/yellow] {result.skipped}  "
        f"[red]Errors:[/red] {result.errors}",
        title=f"Import: {source}",
    ))
    for detail in result.details[-5:]:
        console.print(f"  [dim]{detail}[/dim]")


def print_consolidation_stats(stats) -> None:
    """Print consolidation stats as a rich table."""
    table = Table(title="Consolidation Results")
    table.add_column("Metric", style="bold")
    table.add_column("Value", style="cyan")

    table.add_row("Expired deleted", str(stats.expired_deleted))
    table.add_row("Expired promoted", str(stats.expired_promoted))
    table.add_row("Promoted to LTM", str(stats.promoted_to_ltm))
    table.add_row("Promoted to Shared", str(stats.promoted_to_shared))
    table.add_row("Duplicates merged", str(stats.duplicates_merged))
    table.add_row("Auto-importance rescored", str(stats.auto_importance_rescored))
    table.add_row("Conflicts auto-resolved", str(stats.conflicts_auto_resolved))
    table.add_row("Conflicts needs review", str(stats.conflicts_needs_review))
    table.add_row("Lessons validated", str(stats.lessons_validated))
    table.add_row("Lessons disputed", str(stats.lessons_disputed))
    table.add_row("Salience decayed", str(stats.salience_decayed))
    table.add_row("Curiosity decayed", str(stats.curiosity_decayed))
    table.add_row("Duration", f"{stats.duration_seconds:.1f}s")

    console.print(table)
