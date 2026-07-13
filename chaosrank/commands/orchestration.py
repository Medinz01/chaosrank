"""CLI command for merging distributed traces via agents."""

from __future__ import annotations


from pathlib import Path
import typer

from chaosrank.cli_utils import (
    console, _setup_logging, _load_config, _init_engine_client
)

def orchestration_merge(
    agent_id: str = typer.Option(..., "--agent-id"),
    traces: Path = typer.Option(..., "--traces", "-t", exists=True),
    trace_format: str = typer.Option("jaeger", "--format", "-f"),
    config: Path = typer.Option(Path("chaosrank.yaml"), "--config"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
):
    """Merge a local trace snapshot into the central engine."""
    _setup_logging(verbose)
    cfg = _load_config(config)
    client, engine_online = _init_engine_client(cfg, verbose)
    if not engine_online:
        console.print("[red]Engine must be online for orchestration.[/red]")
        raise typer.Exit(1)
        
    from chaosrank.orchestration.agent import CollectionAgent
    
    agent = CollectionAgent(agent_id=agent_id, traces_path=traces, trace_format=trace_format)
    snap = agent.observe()
    
    try:
        res = client.merge_snapshots([snap])
        console.print(f"[green]Successfully merged snapshot. Global graph has {len(res.get('graph', {}).get('edges', []))} edges.[/green]")
    except Exception as e:
        console.print(f"[red]Orchestration merge failed: {e}[/red]")
        raise typer.Exit(1)
