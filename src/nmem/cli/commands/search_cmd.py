"""nmem search — cross-tier memory search."""

from __future__ import annotations

import json
from typing import Annotated

import typer

from nmem.cli.output import console, run_async, get_mem, print_results


def search(
    query: Annotated[str, typer.Argument(help="Search query")],
    agent_id: Annotated[str, typer.Option("--agent-id", "-a",
        help="Agent ID to search as")] = "default",
    tiers: Annotated[str | None, typer.Option("--tiers", "-t",
        help="Comma-separated tiers: journal,ltm,shared,entity")] = None,
    top_k: Annotated[int, typer.Option("--top-k", "-k",
        help="Maximum results")] = 10,
    json_output: Annotated[bool, typer.Option("--json",
        help="Output as JSON")] = False,
):
    """Search across all memory tiers."""
    tier_tuple = tuple(tiers.split(",")) if tiers else None

    async def _search():
        async with get_mem() as mem:
            results = await mem.search(
                agent_id=agent_id, query=query,
                tiers=tier_tuple, top_k=top_k,
            )

            if json_output:
                out = [
                    {
                        "tier": r.tier,
                        "score": r.score,
                        "title": getattr(r, "title", None) or getattr(r, "key", None),
                        "content": r.content,
                        "metadata": r.metadata,
                    }
                    for r in results
                ]
                typer.echo(json.dumps(out, indent=2, default=str))
            else:
                print_results(results, title=f'Search: "{query}"')

    run_async(_search())
