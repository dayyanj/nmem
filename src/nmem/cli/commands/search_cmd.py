"""nmem search — cross-tier memory search."""

from __future__ import annotations

import json
from typing import Annotated

import typer

from nmem.cli.output import console, run_async, get_mem, print_results


def search(
    query: Annotated[str, typer.Argument(help="Search query")],
    agent_id: Annotated[str | None, typer.Option("--agent-id", "-a",
        help="Agent ID (searches all agents if not specified)")] = None,
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
            if agent_id:
                results = await mem.search(
                    agent_id=agent_id, query=query,
                    tiers=tier_tuple, top_k=top_k,
                )
            else:
                # Search all agents: get distinct agent IDs, search each, merge
                from sqlalchemy import text
                agents = set()
                for table in ["nmem_journal_entries", "nmem_long_term_memory"]:
                    try:
                        async with mem._db.session() as session:
                            r = await session.execute(
                                text(f"SELECT DISTINCT agent_id FROM {table} LIMIT 20")
                            )
                            agents.update(row[0] for row in r.all())
                    except Exception:
                        pass
                agents = agents or {"default"}

                all_results = []
                for aid in agents:
                    r = await mem.search(
                        agent_id=aid, query=query,
                        tiers=tier_tuple, top_k=top_k,
                    )
                    all_results.extend(r)
                # Sort by score, dedup by id+tier, take top_k
                seen = set()
                results = []
                for r in sorted(all_results, key=lambda x: x.score, reverse=True):
                    key = (r.tier, r.id)
                    if key not in seen:
                        seen.add(key)
                        results.append(r)
                    if len(results) >= top_k:
                        break

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
