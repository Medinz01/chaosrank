"""CLI command for displaying environment and version info."""

from __future__ import annotations


import platform
import sys
from pathlib import Path
import typer
from rich.table import Table

from chaosrank.cli_utils import console, _setup_logging, _load_config, _init_engine_client

def info_cmd(
    config: Path = typer.Option(Path("chaosrank.yaml"), "--config"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
):
    """Display CLI version, Python version, and Engine status."""
    _setup_logging(verbose)
    cfg = _load_config(config)
    
    # Try to get CLI version
    from importlib.metadata import version
    try:
        cli_version = version("chaosrank-cli")
    except Exception:
        cli_version = "Unknown (not installed via pip)"
        
    client, engine_online = _init_engine_client(cfg, verbose)
    
    table = Table(title="ChaosRank Environment Info")
    table.add_column("Component", style="cyan")
    table.add_column("Details", style="magenta")
    
    table.add_row("ChaosRank CLI Version", cli_version)
    table.add_row("Python Version", sys.version.split(" ")[0])
    table.add_row("Operating System", platform.platform())
    
    engine_url = cfg.get("engine", {}).get("url", "http://localhost:8080")
    status_str = "[green]Online[/green]" if engine_online else "[red]Offline[/red]"
    table.add_row("Engine URL", engine_url)
    table.add_row("Engine Status", status_str)
    
    console.print(table)
