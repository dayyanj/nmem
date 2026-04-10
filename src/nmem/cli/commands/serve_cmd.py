"""
nmem serve — start the REST API server.
"""

from __future__ import annotations

import sys

import typer

from nmem.cli.output import console


def serve(
    host: str = typer.Option("127.0.0.1", "--host", "-H", help="Bind address"),
    port: int = typer.Option(8080, "--port", "-p", help="Port number"),
    workers: int = typer.Option(1, "--workers", "-w", help="Number of uvicorn workers"),
    reload: bool = typer.Option(False, "--reload", "-r", help="Auto-reload on code changes (dev only)"),
    log_level: str = typer.Option("info", "--log-level", "-l", help="Log level"),
) -> None:
    """Start the nmem REST API server.

    Reads configuration from NMEM_* environment variables. For per-tenant
    SaaS deployment, also set NMEM_CONTROL_PLANE_URL.
    """
    try:
        import uvicorn
    except ImportError:
        console.print(
            "[red]uvicorn is not installed.[/red] Install the API extras:\n"
            "    pip install 'nmem[api]'"
        )
        sys.exit(1)

    console.print(f"[green]Starting nmem API on http://{host}:{port}[/green]")
    console.print(f"  Docs: http://{host}:{port}/docs")
    console.print(f"  OpenAPI: http://{host}:{port}/openapi.json")

    uvicorn.run(
        "nmem.api.main:app",
        host=host,
        port=port,
        workers=workers if not reload else 1,
        reload=reload,
        log_level=log_level,
    )
