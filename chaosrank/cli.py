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
from chaosrank.parser.async_deps import parse_async_deps
from chaosrank.parser.incidents import parse_incidents
from chaosrank.parser.normalize import load_aliases
from chaosrank.scorer.ranker import rank_services

app = typer.Typer(
    name="chaosrank",
    help="Risk-driven chaos experiment scheduler.",
    add_completion=False,
)

console = Console()

_SUPPORTED_FORMATS = ("asyncapi", "kafka")
_TRACE_FORMATS = ("jaeger", "otlp")


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
        help="Path to trace export file.",
        exists=True, file_okay=True, dir_okay=False,
    ),
    trace_format: str = typer.Option(
        "jaeger", "--format", "-f",
        help="Trace format: jaeger (default) | otlp",
    ),
    incidents: Optional[Path] = typer.Option(
        None, "--incidents", "-i",
        help="Path to incident history CSV.",
    ),
    async_deps: Optional[Path] = typer.Option(
        None, "--async-deps", "-a",
        help=(
            "Path to async dependency manifest YAML. "
            "Describes Kafka/SQS/async relationships missing from trace spans. "
            "Async edges are assigned weight equal to median trace edge weight."
        ),
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
    _setup_logging(verbose)

    if trace_format not in _TRACE_FORMATS:
        console.print(
            f"[red]Unknown trace format: {trace_format!r}. "
            f"Supported: {', '.join(_TRACE_FORMATS)}[/red]"
        )
        raise typer.Exit(1)

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

    typer.echo(f"Parsing traces ({trace_format})...", err=True)
    try:
        G = build_graph(traces, min_call_frequency=min_call_freq, trace_format=trace_format)
    except Exception as e:
        console.print(f"[red]Failed to parse traces: {e}[/red]")
        raise typer.Exit(1)

    if G.number_of_nodes() == 0:
        console.print("[red]Error: No services found in trace data.[/red]")
        raise typer.Exit(1)

    if async_deps:
        if not async_deps.exists():
            console.print(f"[red]Async deps file not found: {async_deps}[/red]")
            raise typer.Exit(1)
        typer.echo("Merging async dependencies...", err=True)
        try:
            G = parse_async_deps(async_deps, G)
        except Exception as e:
            console.print(f"[red]Failed to parse async deps: {e}[/red]")
            raise typer.Exit(1)

    typer.echo("Computing blast radius...", err=True)
    blast = compute_blast_radius(G, async_deps_provided=async_deps is not None)

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
        render_json(ranked, async_deps_provided=async_deps is not None)
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
        help="Path to trace export file.",
        exists=True,
    ),
    trace_format: str = typer.Option(
        "jaeger", "--format", "-f",
        help="Trace format: jaeger (default) | otlp",
    ),
    async_deps: Optional[Path] = typer.Option(
        None, "--async-deps", "-a",
        help="Path to async dependency manifest YAML.",
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
    _setup_logging(verbose)

    if trace_format not in _TRACE_FORMATS:
        console.print(
            f"[red]Unknown trace format: {trace_format!r}. "
            f"Supported: {', '.join(_TRACE_FORMATS)}[/red]"
        )
        raise typer.Exit(1)

    cfg = _load_config(config)
    min_call_freq = cfg.get("graph", {}).get("min_call_frequency", 10)

    G = build_graph(traces, min_call_frequency=min_call_freq, trace_format=trace_format)

    if async_deps:
        if not async_deps.exists():
            console.print(f"[red]Async deps file not found: {async_deps}[/red]")
            raise typer.Exit(1)
        from chaosrank.parser.async_deps import parse_async_deps
        G = parse_async_deps(async_deps, G)

    if output == "dot":
        lines = ["digraph G {"]
        for u, v, data in G.edges(data=True):
            edge_type = data.get("edge_type", "sync")
            style = ' style=dashed' if edge_type == "async" else ""
            lines.append(f'  "{u}" -> "{v}" [weight={data.get("weight", 1)}{style}];')
        lines.append("}")
        print("\n".join(lines))
    else:
        console.print(f"[red]Unknown graph output format: {output}[/red]")
        raise typer.Exit(1)


@app.command()
def convert(
    from_format: str = typer.Option(
        ..., "--from",
        help=f"Source format to convert from: {' | '.join(_SUPPORTED_FORMATS)}",
    ),
    input: Path = typer.Option(
        ..., "--input", "-i",
        help=(
            "Path to source file or directory. "
            "For asyncapi: pass a directory to process multiple single-service specs. "
            "For kafka: pass the consumer groups JSON export file."
        ),
        exists=True, file_okay=True, dir_okay=True,
    ),
    output: Optional[Path] = typer.Option(
        None, "--output", "-o",
        help="Path to write async-deps.yaml. Omit to print to stdout.",
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run",
        help="Print what would be written without writing the file.",
    ),
    verbose: bool = typer.Option(
        False, "--verbose", "-v",
        help="Enable debug logging.",
    ),
) -> None:
    _setup_logging(verbose)

    if from_format not in _SUPPORTED_FORMATS:
        console.print(
            f"[red]Unknown format: {from_format!r}. "
            f"Supported: {', '.join(_SUPPORTED_FORMATS)}[/red]"
        )
        raise typer.Exit(1)

    if from_format == "asyncapi":
        from chaosrank.adapters.asyncapi import AsyncAPIAdapter
        adapter = AsyncAPIAdapter()
    elif from_format == "kafka":
        from chaosrank.adapters.kafka import KafkaAdapter
        adapter = KafkaAdapter()

    typer.echo(f"Converting {from_format} → async-deps.yaml...", err=True)
    try:
        dependencies = adapter.convert(input)
    except Exception as e:
        console.print(f"[red]Conversion failed: {e}[/red]")
        raise typer.Exit(1)

    if not dependencies:
        console.print("[yellow]Warning: no dependencies extracted. Output will be empty.[/yellow]")

    manifest = yaml.dump({"dependencies": dependencies}, default_flow_style=False, sort_keys=False)

    if dry_run:
        console.print("[dim]--- dry run output (not written) ---[/dim]")
        console.print(manifest)
        typer.echo(f"{len(dependencies)} dependencies would be written.", err=True)
        return

    if output:
        output.write_text(manifest)
        typer.echo(f"Written {len(dependencies)} dependencies to {output}", err=True)
    else:
        sys.stdout.write(manifest)


if __name__ == "__main__":
    app()