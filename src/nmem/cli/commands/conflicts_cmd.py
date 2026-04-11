"""nmem conflicts — inspect the belief-revision conflict table.

Phase 3 ships a read-only command. Manual resolution is a SQL UPDATE
until someone actually needs more (we will bikeshed the UX later).
"""

from __future__ import annotations

from typing import Annotated

import typer
from rich.table import Table
from sqlalchemy import select

from nmem.cli.output import console, run_async, get_mem
from nmem.db.models import MemoryConflictModel

conflicts_app = typer.Typer(
    help="Inspect belief-revision conflict rows.",
    no_args_is_help=True,
)


@conflicts_app.command("list")
def list_conflicts(
    pending: Annotated[
        bool,
        typer.Option(
            "--pending",
            help="Only show conflicts awaiting review (needs_review + open).",
        ),
    ] = False,
    limit: Annotated[int, typer.Option(help="Max rows to show.")] = 20,
) -> None:
    """List conflict rows from the memory conflict table."""

    async def _list() -> None:
        async with get_mem() as mem:
            async with mem._db.session() as session:
                stmt = select(MemoryConflictModel).order_by(
                    MemoryConflictModel.created_at.desc()
                ).limit(limit)
                if pending:
                    stmt = stmt.where(
                        MemoryConflictModel.status.in_(["open", "needs_review"])
                    )
                result = await session.execute(stmt)
                rows = result.scalars().all()

            if not rows:
                console.print("[dim]No conflicts found.[/dim]")
                return

            table = Table(title=f"Memory Conflicts ({len(rows)} row(s))")
            table.add_column("ID", style="dim")
            table.add_column("Status", style="bold")
            table.add_column("A", style="cyan")
            table.add_column("B", style="cyan")
            table.add_column("Agents")
            table.add_column("Created")

            for row in rows:
                status_style = {
                    "auto_resolved": "green",
                    "needs_review": "yellow",
                    "open": "white",
                    "stale": "dim",
                    "manual": "blue",
                }.get(row.status, "white")
                table.add_row(
                    str(row.id),
                    f"[{status_style}]{row.status}[/{status_style}]",
                    f"{row.record_a_table.replace('nmem_', '')}#{row.record_a_id}",
                    f"{row.record_b_table.replace('nmem_', '')}#{row.record_b_id}",
                    f"{row.agent_a} vs {row.agent_b}",
                    row.created_at.strftime("%Y-%m-%d %H:%M") if row.created_at else "-",
                )
            console.print(table)

    run_async(_list())
