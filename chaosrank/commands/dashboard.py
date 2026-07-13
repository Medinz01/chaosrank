"""CLI command to launch the standalone UI dashboard."""

from __future__ import annotations


import typer

from chaosrank.cli_utils import console
from chaosrank.commands.dashboard_server import start_ui_server

def dashboard_cmd(
    port: int = typer.Option(8082, "--port", "-p", help="Port to serve the dashboard on."),
) -> None:
    """Launch the interactive ChaosRank React Dashboard (standalone, no live data)."""
    console.print("[cyan]Launching ChaosRank Dashboard in standalone mode...[/cyan]")
    console.print("[dim]For live ranking data, use `chaosrank rank --ui` instead.[/dim]")
    start_ui_server(shared_state=None, repl_instance=None, port=port)
