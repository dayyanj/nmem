"""nmem demo — run an interactive demo with built-in dataset."""

from __future__ import annotations

from nmem.cli.output import (
    console, run_async, get_mem, print_results, print_consolidation_stats,
)


def demo():
    """Run interactive demo with built-in dataset (Acme Corp Support Team)."""
    async def _demo():
        from nmem.demo.dataset import (
            JOURNAL_ENTRIES, LTM_ENTRIES, SHARED_ENTRIES, DEMO_SEARCHES,
        )
        from tqdm import tqdm

        console.print()
        console.print("[bold]nmem Demo — Acme Corp Support Team[/bold]")
        console.print("[dim]3 agents: support, engineering, sales[/dim]")
        console.print()

        # Use PostgreSQL if configured, otherwise SQLite with noop embeddings
        import os
        db_url = os.environ.get("NMEM_DATABASE_URL")
        overrides = {}
        if not db_url:
            overrides["database_url"] = "sqlite+aiosqlite:///nmem_demo.db"
            overrides["embedding"] = {"provider": "noop"}
            console.print("[dim]No NMEM_DATABASE_URL set — using SQLite + noop embeddings[/dim]")
            console.print("[dim]Set NMEM_DATABASE_URL for full pgvector hybrid search[/dim]")
            console.print()

        async with get_mem(**overrides) as mem:
            # ── Load Dataset ──────────────────────────────────────
            console.print("[bold cyan]Loading demo dataset...[/bold cyan]")

            entries = []
            for e in JOURNAL_ENTRIES:
                entries.append(("journal", e))
            for e in LTM_ENTRIES:
                entries.append(("ltm", e))
            for e in SHARED_ENTRIES:
                entries.append(("shared", e))

            for tier, entry in tqdm(entries, desc="Loading", unit="entry"):
                if tier == "journal":
                    await mem.journal.add(**entry, compress=False)
                elif tier == "ltm":
                    await mem.ltm.save(**entry, compress=False)
                elif tier == "shared":
                    await mem.shared.save(agent_id="system", **entry)

            console.print(f"[green]Loaded {len(entries)} entries across 3 tiers[/green]")
            console.print()

            # ── Demo Searches ─────────────────────────────────────
            for agent_id, query in DEMO_SEARCHES:
                console.print(f'[bold cyan]Search:[/bold cyan] "{query}" [dim](as {agent_id})[/dim]')
                results = await mem.search(agent_id=agent_id, query=query, top_k=5)
                print_results(results)
                console.print()

            # ── Consolidation ─────────────────────────────────────
            console.print("[bold cyan]Running consolidation cycle...[/bold cyan]")
            stats = await mem.consolidation.run_full_cycle()
            print_consolidation_stats(stats)
            console.print()

            # ── Prompt Context ────────────────────────────────────
            console.print('[bold cyan]Building prompt context for "support" agent...[/bold cyan]')
            ctx = await mem.prompt.build(
                agent_id="support", query="customer payment issue",
            )
            console.print()
            from rich.panel import Panel
            console.print(Panel(
                ctx.full_injection[:800] + ("..." if len(ctx.full_injection) > 800 else ""),
                title="Prompt Injection (first 800 chars)",
                border_style="cyan",
            ))
            console.print(f"[dim]Total injection: {len(ctx.full_injection)} chars "
                          f"(~{ctx.token_estimate} tokens)[/dim]")
            console.print()

            console.print("[bold green]Demo complete![/bold green]")
            console.print("[dim]Try: nmem search 'payment timeout' --agent-id support[/dim]")
            console.print("[dim]Try: nmem import claude-code[/dim]")
            console.print("[dim]Try: nmem stats[/dim]")

    run_async(_demo())
