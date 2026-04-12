"""nmem token-trends — show prompt injection and LLM token usage over time."""

from __future__ import annotations

from typing import Optional

import typer

from nmem.cli.output import console, run_async, get_mem


def token_trends(
    days: int = typer.Option(30, "--days", "-d", help="Number of days to look back."),
    agent: Optional[str] = typer.Option(None, "--agent", "-a", help="Filter by agent ID."),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON."),
):
    """Show token usage trends — prompt injection sizes and LLM costs over time."""

    async def _run():
        async with get_mem() as mem:
            from nmem.token_stats import query_token_summary, query_token_trends
            import json as json_mod

            if json_output:
                records = await query_token_trends(mem._db, days=days, agent_id=agent)
                console.print(json_mod.dumps(records, indent=2))
                return

            summary = await query_token_summary(mem._db, days=days)

            if summary["total_prompt_calls"] == 0:
                console.print(
                    "[dim]No token stats recorded yet. "
                    "Stats are collected automatically each time "
                    "mem.prompt.build() is called.[/dim]"
                )
                return

            from rich.table import Table
            from rich.panel import Panel

            # ── Summary Panel ────────────────────────────────
            console.print(Panel(
                f"  Period: [cyan]last {days} days[/cyan]\n"
                f"  Prompt builds: [bold]{summary['total_prompt_calls']:,}[/bold]\n"
                f"  Total tokens injected: [bold]{summary['total_prompt_tokens']:,}[/bold]\n"
                f"  Avg tokens/call: [bold]{summary['avg_tokens_per_call']:,}[/bold]",
                title="Token Usage Summary",
            ))

            # ── Per-Agent Breakdown ──────────────────────────
            if summary["agents"]:
                agent_table = Table(title="Per-Agent Token Usage", show_lines=True)
                agent_table.add_column("Agent", style="bold", width=25)
                agent_table.add_column("Calls", style="cyan", justify="right")
                agent_table.add_column("Tokens", style="yellow", justify="right")
                agent_table.add_column("Avg/Call", style="dim", justify="right")

                sorted_agents = sorted(
                    summary["agents"].items(),
                    key=lambda x: x[1]["tokens"],
                    reverse=True,
                )
                for aid, data in sorted_agents:
                    calls = data["calls"]
                    tokens = data["tokens"]
                    avg = tokens // calls if calls else 0
                    agent_table.add_row(aid, f"{calls:,}", f"{tokens:,}", f"{avg:,}")

                console.print(agent_table)

            # ── Per-Section Breakdown ────────────────────────
            if summary["sections"]:
                sec_table = Table(title="Token Distribution by Memory Tier", show_lines=True)
                sec_table.add_column("Section", style="bold", width=20)
                sec_table.add_column("Tokens", style="yellow", justify="right")
                sec_table.add_column("% of Total", style="dim", justify="right")

                total = summary["total_prompt_tokens"] or 1
                sorted_secs = sorted(
                    summary["sections"].items(),
                    key=lambda x: x[1],
                    reverse=True,
                )
                for sec, tokens in sorted_secs:
                    pct = tokens * 100 / total
                    sec_table.add_row(sec, f"{tokens:,}", f"{pct:.1f}%")

                console.print(sec_table)

            # ── LLM Operations ───────────────────────────────
            if summary["llm_operations"]:
                llm_table = Table(title="LLM Token Usage (Consolidation)", show_lines=True)
                llm_table.add_column("Operation", style="bold", width=20)
                llm_table.add_column("Tokens", style="yellow", justify="right")

                for op, tokens in sorted(
                    summary["llm_operations"].items(),
                    key=lambda x: x[1],
                    reverse=True,
                ):
                    llm_table.add_row(op, f"{tokens:,}")

                console.print(llm_table)

            # ── Daily Trend ──────────────────────────────────
            records = await query_token_trends(mem._db, days=min(days, 14), agent_id=agent)
            # Group by date
            daily: dict[str, dict] = {}
            for rec in records:
                if rec["agent_id"] == "_system_llm":
                    continue
                d = rec["date"]
                if d not in daily:
                    daily[d] = {"calls": 0, "tokens": 0}
                daily[d]["calls"] += rec["calls"]
                daily[d]["tokens"] += rec["total_tokens"]

            if daily:
                trend_table = Table(title="Daily Trend", show_lines=True)
                trend_table.add_column("Date", style="bold", width=12)
                trend_table.add_column("Calls", style="cyan", justify="right")
                trend_table.add_column("Tokens", style="yellow", justify="right")
                trend_table.add_column("Avg", style="dim", justify="right")

                for date in sorted(daily.keys()):
                    d = daily[date]
                    avg = d["tokens"] // d["calls"] if d["calls"] else 0
                    trend_table.add_row(
                        date, f"{d['calls']:,}", f"{d['tokens']:,}", f"{avg:,}",
                    )

                console.print(trend_table)

    run_async(_run())
