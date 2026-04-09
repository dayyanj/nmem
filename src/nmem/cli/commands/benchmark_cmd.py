"""nmem benchmark — run performance benchmarks."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from nmem.cli.output import console, run_async


def benchmark(
    sizes: Annotated[str, typer.Option("--sizes", "-s",
        help="Comma-separated dataset sizes")] = "50,200",
    output: Annotated[Path | None, typer.Option("--output", "-o",
        help="Save JSON report to file")] = None,
    database_url: Annotated[str | None, typer.Option("--database-url", "-d",
        help="Database URL (default: SQLite)")] = None,
    embedding: Annotated[str, typer.Option("--embedding",
        help="Embedding provider to benchmark")] = "noop",
):
    """Run performance benchmarks and print results."""
    size_list = [int(s.strip()) for s in sizes.split(",")]
    db_url = database_url or "sqlite+aiosqlite:///nmem_benchmark.db"

    async def _bench():
        from nmem.benchmark.runner import run_benchmarks
        from rich.table import Table
        from rich.panel import Panel

        console.print()
        console.print("[bold]nmem Benchmark Suite[/bold]")
        console.print(f"[dim]Database: {db_url}[/dim]")
        console.print(f"[dim]Embedding: {embedding}[/dim]")
        console.print(f"[dim]Dataset sizes: {size_list}[/dim]")
        console.print()

        report = await run_benchmarks(
            database_url=db_url,
            embedding_provider=embedding,
            sizes=size_list,
            output_path=output,
        )

        # ── Throughput Table ──────────────────────────────────
        throughput = [r for r in report.results if r.category == "throughput"]
        if throughput:
            t = Table(title="Throughput", show_lines=True)
            t.add_column("Benchmark", style="bold", width=22)
            t.add_column("Dataset", style="cyan", justify="right", width=8)
            t.add_column("Result", style="green", width=25)
            t.add_column("Time", style="dim", width=10)

            for r in throughput:
                m = r.metrics
                size = str(m.get("dataset_size", ""))

                if "journal_entries_per_second" in m:
                    result = (
                        f"Journal: {m['journal_entries_per_second']} e/s\n"
                        f"LTM batch: {m['ltm_batch_entries_per_second']} e/s"
                    )
                    time_str = f"{r.duration_seconds:.2f}s"
                elif "queries_per_second" in m:
                    result = (
                        f"{m['queries_per_second']} q/s\n"
                        f"avg {m['avg_query_ms']}ms/query"
                    )
                    time_str = f"{r.duration_seconds:.2f}s"
                elif "cycle_seconds" in m:
                    result = (
                        f"Cycle: {m['cycle_seconds']}s\n"
                        f"Promoted: {m['promoted']}"
                    )
                    time_str = f"{m['cycle_seconds']}s"
                else:
                    result = str(m)
                    time_str = f"{r.duration_seconds:.2f}s"

                t.add_row(r.name, size, result, time_str)

            console.print(t)

        # ── Retrieval Quality ─────────────────────────────────
        quality = [r for r in report.results if r.category == "retrieval_quality"]
        if quality:
            q = quality[0].metrics
            improved = "Yes" if q["quality_improved"] else "No"
            console.print(Panel(
                f"  Entries: {q['entries_inserted']} "
                f"({q['high_importance_count']} high, {q['low_importance_count']} low)\n"
                f"  Promoted to LTM: {q['promoted_to_ltm']}\n"
                f"  High-importance in top 3 (before): {q['pre_consolidation_high_in_top3']}/3\n"
                f"  High-importance in top 3 (after):  {q['post_consolidation_high_in_top3']}/3\n"
                f"  Quality improved: [{'green' if q['quality_improved'] else 'yellow'}]{improved}[/]",
                title="Retrieval Quality (consolidation impact)",
            ))

        # ── Multi-Agent Propagation ───────────────────────────
        multi = [r for r in report.results if r.category == "multi_agent"]
        if multi:
            m = multi[0].metrics
            promoted = "Yes" if m["cross_agent_entry_promoted"] else "No"
            private = "Yes" if m["solo_entry_stayed_private"] else "No"
            console.print(Panel(
                f"  Cross-agent entry promoted to shared: "
                f"[{'green' if m['cross_agent_entry_promoted'] else 'red'}]{promoted}[/]\n"
                f"  Solo entry stayed private: "
                f"[{'green' if m['solo_entry_stayed_private'] else 'red'}]{private}[/]\n"
                f"  Agents who accessed: {', '.join(m['agents_who_accessed'])}\n"
                f"  Accesses at promotion: {m['access_count_at_promotion']}",
                title="Multi-Agent Knowledge Propagation",
            ))

        # ── Summary ───────────────────────────────────────────
        total_time = sum(r.duration_seconds for r in report.results)
        console.print(f"\n[dim]Total benchmark time: {total_time:.1f}s | "
                      f"nmem v{report.version} | {report.database}[/dim]")

        if output:
            console.print(f"[dim]Report saved to: {output}[/dim]")

    run_async(_bench())
