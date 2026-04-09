"""nmem import — import data from various sources."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from nmem.cli.output import console, run_async, get_mem, print_import_result

import_app = typer.Typer(
    help="Import data into nmem from various sources.",
    no_args_is_help=True,
)


@import_app.command("claude-code")
def import_claude_code(
    path: Annotated[Path, typer.Argument(
        help="Path to .claude directory")] = Path.home() / ".claude",
    agent_id: Annotated[str, typer.Option("--agent-id", "-a",
        help="Agent ID for imported entries")] = "claude-code",
    compress: Annotated[bool, typer.Option("--compress/--no-compress",
        help="LLM-compress content (off by default)")] = False,
    include_global: Annotated[bool, typer.Option("--include-global",
        help="Include global ~/.claude/CLAUDE.md (may contain credentials)")] = False,
):
    """Import Claude Code memory files (~/.claude/projects/*/memory/*.md)."""
    if not path.is_dir():
        console.print(f"[red]Directory not found: {path}[/red]")
        raise typer.Exit(1)

    async def _import():
        from nmem.cli.importers.claude_code import (
            import_claude_code as do_import,
            discover_memory_files,
        )

        files = discover_memory_files(path, include_global)
        console.print(f"Found [cyan]{len(files)}[/cyan] memory files in {path}")

        if not files:
            console.print("[yellow]No memory files found. Check the path.[/yellow]")
            return

        async with get_mem() as mem:
            result = await do_import(mem, path, agent_id, compress, include_global)
            print_import_result(result, "Claude Code")

    run_async(_import())


@import_app.command("markdown")
def import_markdown(
    directory: Annotated[Path, typer.Argument(help="Directory containing .md files")],
    agent_id: Annotated[str, typer.Option("--agent-id", "-a",
        help="Agent ID for imported entries")] = "default",
    category: Annotated[str, typer.Option("--category", "-c",
        help="LTM category for all entries")] = "knowledge",
    compress: Annotated[bool, typer.Option("--compress/--no-compress",
        help="LLM-compress content (off by default)")] = False,
):
    """Import a directory of markdown files as LTM entries."""
    if not directory.is_dir():
        console.print(f"[red]Directory not found: {directory}[/red]")
        raise typer.Exit(1)

    async def _import():
        from nmem.cli.importers.markdown import import_markdown as do_import

        md_files = list(directory.rglob("*.md"))
        console.print(f"Found [cyan]{len(md_files)}[/cyan] markdown files in {directory}")

        if not md_files:
            console.print("[yellow]No .md files found.[/yellow]")
            return

        async with get_mem() as mem:
            result = await do_import(mem, directory, agent_id, category, compress)
            print_import_result(result, f"Markdown ({directory})")

    run_async(_import())


@import_app.command("jsonl")
def import_jsonl(
    file: Annotated[Path, typer.Argument(help="Path to .jsonl file")],
    agent_id: Annotated[str, typer.Option("--agent-id", "-a",
        help="Default agent ID")] = "default",
    compress: Annotated[bool, typer.Option("--compress/--no-compress",
        help="LLM-compress content (off by default)")] = False,
):
    """Import structured JSONL data. Each line: {"content": "...", "tier": "ltm", ...}"""
    if not file.is_file():
        console.print(f"[red]File not found: {file}[/red]")
        raise typer.Exit(1)

    async def _import():
        from nmem.cli.importers.jsonl import import_jsonl as do_import

        lines = file.read_text().strip().splitlines()
        console.print(f"Found [cyan]{len(lines)}[/cyan] lines in {file}")

        async with get_mem() as mem:
            result = await do_import(mem, file, agent_id, compress)
            print_import_result(result, f"JSONL ({file.name})")

    run_async(_import())


@import_app.command("chatgpt")
def import_chatgpt(
    file: Annotated[Path, typer.Argument(help="Path to conversations.json from ChatGPT export")],
    agent_id: Annotated[str, typer.Option("--agent-id", "-a",
        help="Agent ID for imported entries")] = "chatgpt",
    min_messages: Annotated[int, typer.Option("--min-messages",
        help="Skip conversations shorter than this")] = 4,
    compress: Annotated[bool, typer.Option("--compress/--no-compress",
        help="LLM-compress content (off by default)")] = False,
):
    """Import ChatGPT conversations.json from OpenAI data export."""
    if not file.is_file():
        console.print(f"[red]File not found: {file}[/red]")
        raise typer.Exit(1)

    async def _import():
        import json as json_mod
        from nmem.cli.importers.chatgpt import import_chatgpt as do_import

        data = json_mod.loads(file.read_text(encoding="utf-8"))
        console.print(f"Found [cyan]{len(data)}[/cyan] conversations in {file}")

        async with get_mem() as mem:
            result = await do_import(mem, file, agent_id, min_messages, compress)
            print_import_result(result, f"ChatGPT ({file.name})")

    run_async(_import())
