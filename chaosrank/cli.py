import logging
import sys
from pathlib import Path
from typing import Optional

import typer
import yaml
from rich.console import Console

from chaosrank.graph.blast_radius import compute_blast_radius
from chaosrank.graph.builder import build_graph
from chaosrank.output.table import render_table
from chaosrank.parser.incidents import parse_incidents
from chaosrank.parser.normalize import load_aliases
from chaosrank.scorer.ranker import rank_services

app = typer.Typer(
    name="chaosrank",
    help="Risk-driven chaos experiment scheduler.",
    add_completion=False,
)

console = Console()


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.WARNING,
        format="%(levelname)s: %(message)s",
        stream=sys.stderr,
    )


def _load_config(config_path: Path) -> dict:
    if config_path.exists():
        with open(config_path) as f:
            return yaml.safe_load(f) or {}
    return {}


@app.command()
def rank(
    traces: Path = typer.Option(
        ..., "--traces", "-t",
        help="Path to Jaeger JSON trace export.",
        exists=True, file_okay=True, dir_okay=False,
    ),
    incidents: Optional[Path] = typer.Option(
        None, "--incidents", "-i",
        help="Path to incident history CSV.",
    ),
    window: str = typer.Option(
        "7d", "--window", "-w",
        help="Observation window (e.g. 7d, 30d). Currently informational.",
    ),
    output: str = typer.Option(
        "table", "--output", "-o",
        help="Output format: table | json | litmus",
    ),
    top_n: Optional[int] = typer.Option(
        None, "--top-n",
        help="Show only top N services.",
    ),
    config: Path = typer.Option(
        Path("chaosrank.yaml"), "--config",
        help="Path to chaosrank.yaml config file.",
    ),
    alpha: Optional[float] = typer.Option(
        None, "--alpha",
        help="Blast radius weight (overrides config).",
    ),
    beta: Optional[float] = typer.Option(
        None, "--beta",
        help="Fragility weight (overrides config).",
    ),
    verbose: bool = typer.Option(
        False, "--verbose", "-v",
        help="Enable debug logging.",
    ),
) -> None:
    """Rank services by chaos experiment priority."""
    _setup_logging(verbose)

    cfg = _load_config(config)

    _alpha = alpha or cfg.get("weights", {}).get("blast_radius", 0.6)
    _beta  = beta  or cfg.get("weights", {}).get("fragility",    0.4)

    if abs(_alpha + _beta - 1.0) > 1e-6:
        console.print(f"[red]Error: alpha + beta must equal 1.0 (got {_alpha + _beta:.2f})[/red]")
        raise typer.Exit(1)

    min_call_freq = cfg.get("graph", {}).get("min_call_frequency", 10)
    frag_cfg      = cfg.get("fragility", {})
    decay_lambda  = frag_cfg.get("decay_lambda", 0.10)
    base_window   = frag_cfg.get("burst_window_minutes", 5.0)
    _top_n        = top_n or cfg.get("output", {}).get("top_n")

    aliases = cfg.get("aliases", {})
    if aliases:
        load_aliases(aliases)

    typer.echo("Parsing traces...", err=True)
    try:
        G = build_graph(traces, min_call_frequency=min_call_freq)
    except Exception as e:
        console.print(f"[red]Failed to parse traces: {e}[/red]")
        raise typer.Exit(1)

    if G.number_of_nodes() == 0:
        console.print("[red]Error: No services found in trace data.[/red]")
        raise typer.Exit(1)

    typer.echo("Computing blast radius...", err=True)
    blast = compute_blast_radius(G)

    service_incidents = {}
    if incidents:
        if not incidents.exists():
            console.print(f"[red]Incidents file not found: {incidents}[/red]")
            raise typer.Exit(1)
        typer.echo("Parsing incidents...", err=True)
        try:
            service_incidents = parse_incidents(incidents)
        except Exception as e:
            console.print(f"[red]Failed to parse incidents: {e}[/red]")
            raise typer.Exit(1)

    typer.echo("Ranking services...", err=True)
    ranked = rank_services(
        blast_radius=blast,
        service_incidents=service_incidents,
        alpha=_alpha,
        beta=_beta,
        decay_lambda=decay_lambda,
        base_window=base_window,
    )

    if output == "json":
        from chaosrank.output.json_out import render_json
        render_json(ranked)
    elif output == "table":
        render_table(ranked, top_n=_top_n)
    elif output == "litmus":
        from chaosrank.output.litmus import render_litmus
        print(render_litmus(ranked, top_n=_top_n or 1))
    else:
        console.print(f"[red]Unknown output format: {output}[/red]")
        raise typer.Exit(1)


@app.command()
def graph(
    traces: Path = typer.Option(
        ..., "--traces", "-t",
        help="Path to Jaeger JSON trace export.",
        exists=True,
    ),
    output: str = typer.Option(
        "dot", "--output", "-o",
        help="Output format: dot",
    ),
    config: Path = typer.Option(
        Path("chaosrank.yaml"), "--config",
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Visualize the service dependency graph."""
    _setup_logging(verbose)

    cfg = _load_config(config)
    min_call_freq = cfg.get("graph", {}).get("min_call_frequency", 10)

    G = build_graph(traces, min_call_frequency=min_call_freq)

    if output == "dot":
        lines = ["digraph G {"]
        for u, v, data in G.edges(data=True):
            lines.append(f'  "{u}" -> "{v}" [weight={data.get("weight", 1)}];')
        lines.append("}")
        print("\n".join(lines))
    else:
        console.print(f"[red]Unknown graph output format: {output}[/red]")
        raise typer.Exit(1)


if __name__ == "__main__":
    app()
