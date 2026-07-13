"""CLI commands for adaptive ranking and outcome feedback."""

from __future__ import annotations


from pathlib import Path
import typer

from chaosrank.cli_utils import (
    console, _setup_logging, _load_config, _init_engine_client
)
from chaosrank.graph.builder import build_graph
from chaosrank.parser.incidents import parse_incidents

def adaptive_rank(
    traces: Path = typer.Option(..., "--traces", "-t", exists=True),
    trace_format: str = typer.Option("jaeger", "--format", "-f"),
    otlp_format: str = typer.Option("json", "--otlp-format"),
    incidents: Path | None = typer.Option(None, "--incidents", "-i"),
    config: Path = typer.Option(Path("chaosrank.yaml"), "--config"),
    output: str = typer.Option("table", "--output", "-o"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
):
    """Rank services using Adaptive RL tuning."""
    _setup_logging(verbose)
    cfg = _load_config(config)
    G = build_graph(traces, trace_format=trace_format, otlp_format=otlp_format)
    
    client, engine_online = _init_engine_client(cfg, verbose)
    if not engine_online:
        console.print("[red]Engine must be online for adaptive ranking.[/red]")
        raise typer.Exit(1)
        
    service_incidents = {}
    if incidents and incidents.exists():
        service_incidents = parse_incidents(incidents)
        
    try:
        ranked = client.adaptive_rank(G, service_incidents)
        if output == "table":
            from chaosrank.output.table import render_table
            render_table(ranked, top_n=None)
        elif output == "json":
            from chaosrank.output.json_out import render_json
            render_json(ranked, async_deps_provided=False)
        else:
            console.print(f"[red]Format {output} not fully supported for adaptive output.[/red]")
            raise typer.Exit(1)
    except Exception as e:
        console.print(f"[red]Adaptive ranking failed: {e}[/red]")
        raise typer.Exit(1)

def adaptive_outcome(
    service: str = typer.Option(..., "--service", "-s", help="The service that was tested"),
    risk_score: float = typer.Option(..., "--risk-score", help="The risk score it had"),
    blast_radius: float = typer.Option(..., "--blast-radius"),
    fragility: float = typer.Option(..., "--fragility"),
    alpha_used: float = typer.Option(..., "--alpha-used"),
    beta_used: float = typer.Option(..., "--beta-used"),
    rank_pos: int = typer.Option(..., "--rank"),
    outcome: str = typer.Option(..., "--outcome", "-o", help="WEAKNESS_CONFIRMED | WEAKNESS_NOT_FOUND | INCONCLUSIVE"),
    config: Path = typer.Option(Path("chaosrank.yaml"), "--config"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
):
    """Record an experiment outcome to retrain the engine."""
    _setup_logging(verbose)
    cfg = _load_config(config)
    client, engine_online = _init_engine_client(cfg, verbose)
    if not engine_online:
        console.print("[red]Engine must be online.[/red]")
        raise typer.Exit(1)
        
    row = {
        "service": service,
        "risk": risk_score,
        "blast_radius": blast_radius,
        "fragility": fragility,
        "alpha_used": alpha_used,
        "beta_used": beta_used,
        "rank": rank_pos
    }
    try:
        res = client.record_outcome(row, outcome)
        console.print(f"[green]Outcome recorded! New Alpha: {res.get('new_alpha', 0):.2f}, New Beta: {res.get('new_beta', 0):.2f}[/green]")
    except Exception as e:
        console.print(f"[red]Failed to record outcome: {e}[/red]")
        raise typer.Exit(1)

def adaptive_summary(
    config: Path = typer.Option(Path("chaosrank.yaml"), "--config"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
):
    """View the Adaptive RL state."""
    _setup_logging(verbose)
    cfg = _load_config(config)
    client, engine_online = _init_engine_client(cfg, verbose)
    if not engine_online:
        console.print("[red]Engine must be online.[/red]")
        raise typer.Exit(1)
    
    try:
        res = client.adaptive_summary()
        import json
        console.print(json.dumps(res, indent=2))
    except Exception as e:
        console.print(f"[red]Failed to fetch summary: {e}[/red]")
        raise typer.Exit(1)
