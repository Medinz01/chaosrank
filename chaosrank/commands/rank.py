"""Main CLI command for running the ChaosRank scoring engine."""

from __future__ import annotations


from pathlib import Path
import typer
import yaml

from chaosrank.cli_utils import (
    console, _setup_logging, _load_config, _init_engine_client,
    _TRACE_FORMATS, _OTLP_FORMATS
)
from chaosrank.graph.builder import build_graph
from chaosrank.output.table import render_table
from chaosrank.parser.async_deps import parse_async_deps
from chaosrank.parser.incidents import parse_incidents
from chaosrank.parser.normalize import load_aliases

def rank_cmd(
    traces: Path = typer.Option(
        ..., "--traces", "-t",
        help="Path to trace export file.",
        exists=True, file_okay=True, dir_okay=False,
    ),
    trace_format: str = typer.Option(
        "jaeger", "--format", "-f",
        help="Trace format: jaeger (default) | otlp",
    ),
    otlp_format: str = typer.Option(
        "json", "--otlp-format",
        help="OTLP encoding: json (default) | protobuf. Requires [protobuf] extra.",
    ),
    incidents: Path | None = typer.Option(
        None, "--incidents", "-i",
        help="Path to incident history CSV.",
    ),
    async_deps: Path | None = typer.Option(
        None, "--async-deps", "-a",
        help="Path to async dependency manifest YAML.",
    ),
    kafka: Path | None = typer.Option(
        None, "--kafka",
        help=(
            "Path to Kafka topic export JSON. "
            "Direct-mode shortcut: converts and merges async topology without "
            "an intermediate async-deps.yaml file."
        ),
        exists=True, file_okay=True, dir_okay=False,
    ),
    asyncapi: Path | None = typer.Option(
        None, "--asyncapi",
        help=(
            "Path to AsyncAPI 2.x spec file or directory. "
            "Direct-mode shortcut: converts and merges async topology without "
            "an intermediate async-deps.yaml file."
        ),
        exists=True, file_okay=True, dir_okay=True,
    ),
    async_weight_factor: float = typer.Option(
        0.5, "--async-weight-factor",
        help="Weight multiplier for async edges (0.0 to 1.0). Default 0.5.",
    ),
    betweenness: bool = typer.Option(
        False, "--betweenness",
        help="Enable betweenness centrality as a blast radius component.",
    ),
    w_bc: float | None = typer.Option(
        None, "--w-bc",
        help=(
            "Betweenness centrality weight in the blast radius blend. "
            "Only used when --betweenness is set. "
            "w_pr + w_od + w_bc must equal 1.0. "
            "If omitted, auto-adjusts: w_pr and w_od are scaled proportionally "
            "to make room for w_bc=0.20. A warning shows the adjusted values."
        ),
    ),
    prometheus_url: str | None = typer.Option(
        None, "--prometheus-url",
        help=(
            "Prometheus base URL for request_volume backfill "
            "(e.g. http://prometheus:9090). "
            "When set, ChaosRank queries Prometheus to fill in request_volume "
            "for incidents loaded via --incidents. Improves fragility accuracy. "
            "Use --prometheus-metric / --prometheus-service-label to customise."
        ),
    ),
    prometheus_metric: str = typer.Option(
        "http_requests_total", "--prometheus-metric",
        help="Prometheus counter metric for request volume. Default: http_requests_total.",
    ),
    prometheus_service_label: str = typer.Option(
        "service", "--prometheus-service-label",
        help="Prometheus label that identifies the service. Default: service.",
    ),
    prometheus_rate_window: str = typer.Option(
        "5m", "--prometheus-rate-window",
        help="Rate window for Prometheus rate(). Default: 5m.",
    ),
    window: str = typer.Option(
        "7d", "--window", "-w",
        help="Observation window (e.g. 7d, 30d). Currently informational.",
    ),
    output: str = typer.Option(
        "table", "--output", "-o",
        help="Output format: table | json | litmus | html",
    ),
    top_n: int | None = typer.Option(
        None, "--top-n",
        help="Show only top N services.",
    ),
    config: Path = typer.Option(
        Path("chaosrank.yaml"), "--config",
        help="Path to chaosrank.yaml config file.",
    ),
    alpha: float | None = typer.Option(
        None, "--alpha",
        help="Blast radius weight (overrides config).",
    ),
    beta: float | None = typer.Option(
        None, "--beta",
        help="Fragility weight (overrides config).",
    ),
    verbose: bool = typer.Option(
        False, "--verbose", "-v",
        help="Enable debug logging.",
    ),
    ui: bool = typer.Option(
        False, "--ui",
        help="Launch the interactive React Dashboard in the browser instead of printing to console.",
    ),
) -> None:
    """Rank services by chaos experiment priority."""
    _setup_logging(verbose)

    if trace_format not in _TRACE_FORMATS:
        console.print(
            f"[red]Unknown trace format: {trace_format!r}. "
            f"Supported: {', '.join(_TRACE_FORMATS)}[/red]"
        )
        raise typer.Exit(1)

    if otlp_format not in _OTLP_FORMATS:
        console.print(
            f"[red]Unknown --otlp-format: {otlp_format!r}. "
            f"Supported: {', '.join(_OTLP_FORMATS)}[/red]"
        )
        raise typer.Exit(1)

    if otlp_format != "json" and trace_format != "otlp":
        console.print(
            f"[yellow]Warning: --otlp-format {otlp_format!r} has no effect "
            f"when --format is {trace_format!r}. "
            f"Add --format otlp to use this option.[/yellow]"
        )

    if w_bc is not None and not betweenness:
        console.print(
            "[yellow]Warning: --w-bc has no effect without --betweenness. "
            "Add --betweenness to enable betweenness centrality.[/yellow]"
        )

    if not 0.0 < async_weight_factor <= 1.0:
        console.print(
            f"[red]--async-weight-factor must be in (0.0, 1.0], got {async_weight_factor}[/red]"
        )
        raise typer.Exit(1)

    async_sources = sum([async_deps is not None, kafka is not None, asyncapi is not None])
    if async_sources > 1:
        console.print("[red]Specify at most one of --async-deps, --kafka, --asyncapi[/red]")
        raise typer.Exit(1)

    cfg = _load_config(config)

    _alpha = alpha or cfg.get("weights", {}).get("blast_radius", 0.6)
    _beta  = beta  or cfg.get("weights", {}).get("fragility",    0.4)

    if abs(_alpha + _beta - 1.0) > 1e-6:
        console.print(f"[red]Error: alpha + beta must equal 1.0 (got {_alpha + _beta:.2f})[/red]")
        raise typer.Exit(1)

    min_call_freq        = cfg.get("graph", {}).get("min_call_frequency", 10)
    frag_cfg             = cfg.get("fragility", {})
    decay_lambda         = frag_cfg.get("decay_lambda", 0.10)
    base_window          = frag_cfg.get("burst_window_minutes", 5.0)
    _top_n               = top_n or cfg.get("output", {}).get("top_n")
    _async_weight_factor = cfg.get("graph", {}).get("async_weight_factor", async_weight_factor)

    aliases = cfg.get("aliases", {})
    if aliases:
        load_aliases(aliases)

    if trace_format == "otlp" and otlp_format == "json":
        from chaosrank.parser.otlp_json_guard import warn_if_binary
        warn_if_binary(traces)

    typer.echo(f"Parsing traces ({trace_format}/{otlp_format})...", err=True)
    try:
        G = build_graph(
            traces,
            min_call_frequency=min_call_freq,
            trace_format=trace_format,
            otlp_format=otlp_format,
        )
    except Exception as e:
        console.print(f"[red]Failed to parse traces: {e}[/red]")
        raise typer.Exit(1)

    if G.number_of_nodes() == 0:
        console.print("[red]Error: No services found in trace data.[/red]")
        raise typer.Exit(1)

    _async_provided = False
    if kafka or asyncapi:
        import tempfile
        _async_provided = True
        typer.echo("Converting async topology (direct mode)...", err=True)
        try:
            if kafka:
                from chaosrank.adapters.kafka import KafkaAdapter
                deps = KafkaAdapter().convert(kafka)
            else:
                from chaosrank.adapters.asyncapi import AsyncAPIAdapter
                deps = AsyncAPIAdapter().convert(asyncapi)
        except Exception as e:
            console.print(f"[red]Direct-mode conversion failed: {e}[/red]")
            raise typer.Exit(1)

        manifest = yaml.dump({"dependencies": deps}, default_flow_style=False, sort_keys=False)
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", delete=False, prefix="chaosrank-async-"
        ) as tmp:
            tmp.write(manifest)
            _tmp_async_deps = Path(tmp.name)

        try:
            G = parse_async_deps(_tmp_async_deps, G)
        except Exception as e:
            console.print(f"[red]Failed to merge async deps: {e}[/red]")
            raise typer.Exit(1)
        finally:
            _tmp_async_deps.unlink(missing_ok=True)

    elif async_deps:
        _async_provided = True
        if not async_deps.exists():
            console.print(f"[red]Async deps file not found: {async_deps}[/red]")
            raise typer.Exit(1)
        typer.echo("Merging async dependencies...", err=True)
        try:
            G = parse_async_deps(async_deps, G)
        except Exception as e:
            console.print(f"[red]Failed to parse async deps: {e}[/red]")
            raise typer.Exit(1)

    client, engine_online = _init_engine_client(cfg, verbose)

    typer.echo("Computing blast radius...", err=True)

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

        if prometheus_url:
            typer.echo("Backfilling request_volume from Prometheus...", err=True)
            try:
                from chaosrank.incident_adapters.prometheus_volume import PrometheusVolumeBackfiller
                backfiller = PrometheusVolumeBackfiller(
                    url=prometheus_url,
                    metric=prometheus_metric,
                    service_label=prometheus_service_label,
                    rate_window=prometheus_rate_window,
                )
                all_incs = [inc for incs in service_incidents.values() for inc in incs]
                filled   = backfiller.backfill(all_incs)
                from collections import defaultdict
                from chaosrank.parser.incidents import ServiceIncidents
                regrouped = defaultdict(list)
                for inc in filled:
                    regrouped[inc.service].append(inc)
                service_incidents = {
                    svc: ServiceIncidents(service=svc, incidents=incs)
                    for svc, incs in regrouped.items()
                }
            except Exception as e:
                console.print(f"[yellow]Warning: Prometheus backfill failed: {e}[/yellow]")

    typer.echo("Ranking services...", err=True)
    if not engine_online:
        console.print(
            "\n[bold yellow]Engine still offline.[/bold yellow]\n"
            "ChaosRank cannot perform risk-scoring without the engine.\n"
            "Please start the engine and try again:\n"
            "  [dim]docker run -p 8080:8080 -e CHAOSRANK_API_KEYS=devkey chaosrank-engine[/dim]\n"
        )
        raise typer.Exit(0)

    try:
        # Prepare scoring config
        scoring_config = {
            "weights": {"blast_radius": _alpha, "fragility": _beta},
            "fragility": {
                "decay_lambda": decay_lambda,
                "burst_window_minutes": base_window,
            },
            "graph": {
                "async_deps_provided": _async_provided,
                "async_weight_factor": _async_weight_factor,
                "use_betweenness": betweenness,
                "w_bc": w_bc,
                "min_call_frequency": min_call_freq,
            }
        }

        ranked = client.rank(G, service_incidents, config=scoring_config)
    except Exception as e:
        console.print(f"[red]Engine ranking failed: {e}[/red]")
        raise typer.Exit(1)

    if ui:
        from chaosrank.commands.dashboard_server import start_ui_server
        from chaosrank.commands.repl import SharedState
        typer.echo("Starting ChaosRank Dashboard...", err=True)
        
        # Create a temporary shared state for the UI server
        state = SharedState()
        state.G = G
        state.ranked = ranked
        state.config = scoring_config
        state.incidents = service_incidents
        state.client = client
        
        start_ui_server(shared_state=state)
        return

    if output == "json":
        from chaosrank.output.json_out import render_json
        render_json(ranked, async_deps_provided=_async_provided)
    elif output == "table":
        render_table(ranked, top_n=_top_n)
    elif output == "litmus":
        from chaosrank.output.litmus import render_litmus
        print(render_litmus(ranked, top_n=_top_n or 1))
    elif output == "html":
        from chaosrank.output.html_out import render_html
        print(render_html(ranked, G=G, top_n=_top_n, alpha=_alpha, beta=_beta))
    else:
        console.print(f"[red]Unknown output format: {output}[/red]")
        raise typer.Exit(1)
