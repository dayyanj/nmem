"""
nmem CLI — cognitive memory for AI agents.

Usage:
    nmem init              Initialize database
    nmem demo              Run interactive demo
    nmem import ...        Import data (claude-code, markdown, jsonl)
    nmem search <query>    Search across all memory tiers
    nmem stats             Show memory statistics
    nmem consolidate       Run consolidation cycle
"""

from __future__ import annotations

import typer

from nmem.cli.commands.init_cmd import init
from nmem.cli.commands.search_cmd import search
from nmem.cli.commands.stats_cmd import stats
from nmem.cli.commands.demo_cmd import demo
from nmem.cli.commands.consolidate_cmd import consolidate
from nmem.cli.commands.conflicts_cmd import conflicts_app
from nmem.cli.commands.setup_cmd import setup
from nmem.cli.commands.benchmark_cmd import benchmark
from nmem.cli.commands.doctor_cmd import doctor
from nmem.cli.commands.serve_cmd import serve
from nmem.cli.commands.import_cmd import import_app

app = typer.Typer(
    name="nmem",
    help="Cognitive memory for AI agents — hierarchical, self-refining, framework-agnostic.",
    no_args_is_help=True,
)


def _version_callback(value: bool):
    if value:
        from nmem import __version__
        typer.echo(f"nmem {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: bool = typer.Option(
        False, "--version", "-v", callback=_version_callback, is_eager=True,
        help="Show version and exit.",
    ),
):
    """nmem — cognitive memory for AI agents."""


# Register commands
app.command()(init)
app.command()(search)
app.command()(stats)
app.command()(demo)
app.command()(consolidate)
app.command()(setup)
app.command()(benchmark)
app.command()(doctor)
app.command()(serve)
app.add_typer(import_app, name="import")
app.add_typer(conflicts_app, name="conflicts")


if __name__ == "__main__":
    app()
