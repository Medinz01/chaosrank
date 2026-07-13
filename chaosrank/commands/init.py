"""CLI command for creating a default config file."""

from __future__ import annotations


from pathlib import Path
import typer
import yaml

from chaosrank.cli_utils import console, _setup_logging

def init_cmd(
    force: bool = typer.Option(False, "--force", "-f", help="Overwrite existing config if it exists."),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
):
    """Generate a default chaosrank.yaml configuration file."""
    _setup_logging(verbose)
    
    config_path = Path("chaosrank.yaml")
    
    if config_path.exists() and not force:
        console.print(f"[yellow]Configuration file {config_path} already exists. Use --force to overwrite.[/yellow]")
        raise typer.Exit(1)
        
    default_config = {
        "engine": {
            "url": "http://localhost:8080",
            "timeout_seconds": 30
        },
        "weights": {
            "blast_radius": 0.6,
            "fragility": 0.4
        },
        "graph": {
            "min_call_frequency": 10,
            "async_weight_factor": 0.5
        },
        "fragility": {
            "decay_lambda": 0.1,
            "burst_window_minutes": 5.0
        },
        "output": {
            "top_n": 20
        }
    }
    
    try:
        with open(config_path, "w") as f:
            yaml.dump(default_config, f, sort_keys=False, default_flow_style=False)
        console.print(f"[green]Successfully generated default configuration at {config_path}[/green]")
    except Exception as e:
        console.print(f"[red]Failed to write config file: {e}[/red]")
        raise typer.Exit(1)
