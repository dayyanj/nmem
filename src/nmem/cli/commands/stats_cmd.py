"""nmem stats — show memory tier statistics."""

from __future__ import annotations

from nmem.cli.output import console, run_async, get_mem


def stats():
    """Show memory system statistics."""
    async def _stats():
        async with get_mem() as mem:
            from sqlalchemy import text
            from rich.table import Table
            from rich.panel import Panel
            from datetime import datetime, timedelta

            tables = {
                "Working Memory": "nmem_working_memory",
                "Journal": "nmem_journal_entries",
                "Long-Term Memory": "nmem_long_term_memory",
                "Shared Knowledge": "nmem_shared_knowledge",
                "Entity Memory": "nmem_entity_memory",
                "Policies": "nmem_policies",
                "Delegations": "nmem_delegations",
                "Curiosity Signals": "nmem_curiosity_signals",
            }

            table = Table(title="Memory Tier Statistics", show_lines=True)
            table.add_column("Tier", style="bold", width=20)
            table.add_column("Total", style="cyan", justify="right")
            table.add_column("Details", style="dim")

            total = 0
            for label, tbl in tables.items():
                try:
                    async with mem._db.session() as session:
                        result = await session.execute(text(f"SELECT COUNT(*) FROM {tbl}"))
                        count = result.scalar() or 0
                except Exception:
                    count = 0

                details = ""
                if tbl == "nmem_journal_entries" and count > 0:
                    try:
                        async with mem._db.session() as session:
                            now = datetime.utcnow()
                            for days, label_d in [(1, "24h"), (7, "7d"), (30, "30d")]:
                                cutoff = now - timedelta(days=days)
                                r = await session.execute(
                                    text(f"SELECT COUNT(*) FROM {tbl} WHERE created_at >= :cutoff"),
                                    {"cutoff": cutoff},
                                )
                                c = r.scalar() or 0
                                details += f"{label_d}: {c}  "
                    except Exception:
                        pass

                total += count
                table.add_row(label, str(count), details.strip())

            console.print(table)

            # Database info
            db_type = "PostgreSQL" if mem._db.is_postgres else "SQLite"
            db_size = ""
            if mem._db.is_postgres:
                try:
                    async with mem._db.session() as session:
                        r = await session.execute(
                            text("SELECT pg_size_pretty(pg_database_size(current_database()))")
                        )
                        db_size = f" ({r.scalar()})"
                except Exception:
                    pass

            console.print(Panel(
                f"  Database: [cyan]{db_type}{db_size}[/cyan]\n"
                f"  Embedding: [cyan]{mem._config.embedding.provider}[/cyan] "
                f"({mem._config.embedding.dimensions}d)\n"
                f"  LLM: [cyan]{mem._config.llm.provider}[/cyan]\n"
                f"  Total entries: [bold]{total}[/bold]",
                title="System Info",
            ))

    run_async(_stats())
