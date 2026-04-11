"""nmem consolidate — run a consolidation cycle manually."""

from __future__ import annotations

from typing import Annotated

import typer

from nmem.cli.output import console, run_async, get_mem, print_consolidation_stats


def consolidate(
    nightly: Annotated[bool, typer.Option("--nightly",
        help="Run nightly synthesis instead of full cycle")] = False,
):
    """Run a consolidation cycle (decay, promote, dedup, salience)."""
    async def _consolidate():
        async with get_mem() as mem:
            if nightly:
                console.print("[bold cyan]Running nightly synthesis...[/bold cyan]")
                stats = await mem.consolidation.run_nightly_synthesis()
            else:
                console.print("[bold cyan]Running full consolidation cycle...[/bold cyan]")
                stats = await mem.consolidation.run_full_cycle()

            print_consolidation_stats(stats)

    run_async(_consolidate())
