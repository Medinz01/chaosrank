from __future__ import annotations

import logging
import sys
from pathlib import Path

import typer
import yaml
from rich.console import Console

from chaosrank.engine.client import EngineClient
from chaosrank.graph.builder import build_graph
from chaosrank.output.table import render_table
from chaosrank.parser.async_deps import parse_async_deps
from chaosrank.parser.incidents import parse_incidents
from chaosrank.parser.normalize import load_aliases

app = typer.Typer(
    name="chaosrank",
    help="Risk-driven chaos experiment scheduler.",
    add_completion=False,
)

console = Console()

_SUPPORTED_FORMATS  = ("asyncapi", "kafka", "confluent")
_TRACE_FORMATS      = ("jaeger", "otlp")
_OTLP_FORMATS       = ("json", "protobuf")
_INCIDENT_FORMATS   = ("pagerduty", "alertmanager", "grafana-oncall", "opsgenie", "datadog")
_NAMING_STRATEGIES  = ("auto", "topic", "record", "topic_record")


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


def _maybe_backfill_volume(incidents_list, prometheus_url, prometheus_metric,
                           prometheus_service_label, prometheus_rate_window):
    """Backfill request_volume via Prometheus if --prometheus-url is set."""
    if not prometheus_url:
        return incidents_list
    from chaosrank.incident_adapters.prometheus_volume import PrometheusVolumeBackfiller
    backfiller = PrometheusVolumeBackfiller(
        url=prometheus_url,
        metric=prometheus_metric,
        service_label=prometheus_service_label,
        rate_window=prometheus_rate_window,
    )
    return backfiller.backfill(incidents_list)


def _version_callback(value: bool) -> None:
    if value:
        from importlib.metadata import version
        try:
            v = version("chaosrank-cli")
        except Exception:
            v = "1.0.0"
        typer.echo(f"ChaosRank CLI version {v}")
        raise typer.Exit()


@app.callback()
def main(
    ctx: typer.Context,
    version: bool = typer.Option(
        None, "--version", "-v",
        callback=_version_callback,
        is_eager=True,
        help="Show version and exit.",
    ),
) -> None:
    """Risk-driven chaos experiment scheduler."""
    pass


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

    # Engine Client Initialisation
    engine_url = cfg.get("engine", {}).get("url", "http://localhost:8080")
    engine_key = cfg.get("engine", {}).get("api_key", "devkey")
    engine_timeout = cfg.get("engine", {}).get("timeout_seconds", 30)

    # Respect environment variable overrides
    import os
    engine_key = os.getenv("CHAOSRANK_API_KEY", engine_key)

    client = EngineClient(url=engine_url, api_key=engine_key, timeout=engine_timeout)

    typer.echo("Checking ChaosRank Engine status...", err=True)
    engine_online = client.health()
    if not engine_online:
        console.print(
            f"[yellow]Warning: ChaosRank Engine at {engine_url} is offline. "
            "Proceeding with data parsing and preprocessing...[/yellow]"
        )

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
                regrouped = defaultdict(list)
                for inc in filled:
                    regrouped[inc.service].append(inc)
                service_incidents = dict(regrouped)
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
    otlp_format: str = typer.Option(
        "json", "--otlp-format",
        help="OTLP encoding: json (default) | protobuf. Only used when --format otlp.",
    ),
    async_deps: Path | None = typer.Option(
        None, "--async-deps", "-a",
        help="Path to async dependency manifest YAML.",
    ),
    kafka: Path | None = typer.Option(
        None, "--kafka",
        help="Path to Kafka topic export JSON. Direct-mode shortcut.",
        exists=True, file_okay=True, dir_okay=False,
    ),
    asyncapi: Path | None = typer.Option(
        None, "--asyncapi",
        help="Path to AsyncAPI 2.x spec file or directory. Direct-mode shortcut.",
        exists=True, file_okay=True, dir_okay=True,
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

    if trace_format == "otlp" and otlp_format == "json":
        from chaosrank.parser.otlp_json_guard import warn_if_binary
        warn_if_binary(traces)

    cfg = _load_config(config)
    min_call_freq = cfg.get("graph", {}).get("min_call_frequency", 10)

    G = build_graph(
        traces,
        min_call_frequency=min_call_freq,
        trace_format=trace_format,
        otlp_format=otlp_format,
    )

    if sum([async_deps is not None, kafka is not None, asyncapi is not None]) > 1:
        console.print("[red]Specify at most one of --async-deps, --kafka, --asyncapi[/red]")
        raise typer.Exit(1)

    if kafka or asyncapi:
        import tempfile
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
            _tmp = Path(tmp.name)
        try:
            from chaosrank.parser.async_deps import parse_async_deps
            G = parse_async_deps(_tmp, G)
        finally:
            _tmp.unlink(missing_ok=True)
    elif async_deps:
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
        None, "--input", "-i",
        help=(
            "Path to source file or directory. "
            "For asyncapi: directory of single-service specs. "
            "For kafka: kafka-topics.json export. "
            "For confluent --mode file: sr-export.json. "
            "Not required for confluent --mode api."
        ),
    ),
    output: Path | None = typer.Option(
        None, "--output", "-o",
        help="Path to write async-deps.yaml. Omit to print to stdout.",
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run",
        help="Print what would be written without writing the file.",
    ),
    # Confluent-specific options
    mode: str = typer.Option(
        "file", "--mode",
        help="[confluent] Input mode: file (default) | api.",
    ),
    url: str | None = typer.Option(
        None, "--url",
        help="[confluent --mode api] Schema Registry base URL (e.g. http://sr:8081).",
    ),
    token: str | None = typer.Option(
        None, "--token",
        help="[confluent --mode api] Auth token. Use 'user:pass' for basic auth.",
    ),
    naming_strategy: str = typer.Option(
        "auto", "--naming-strategy",
        help=(
            "[confluent] Subject naming strategy: "
            "auto (default) | topic | record | topic_record."
        ),
    ),
    kafka_fallback: Path | None = typer.Option(
        None, "--kafka",
        help=(
            "[confluent] Path to kafka-topics.json for service name fallback "
            "when schema metadata tags are absent."
        ),
        exists=True, file_okay=True, dir_okay=False,
    ),
    verbose: bool = typer.Option(
        False, "--verbose", "-v",
        help="Enable debug logging.",
    ),
) -> None:
    """Convert an async topology source file to async-deps.yaml format."""
    _setup_logging(verbose)

    if from_format not in _SUPPORTED_FORMATS:
        console.print(
            f"[red]Unknown format: {from_format!r}. "
            f"Supported: {', '.join(_SUPPORTED_FORMATS)}[/red]"
        )
        raise typer.Exit(1)

    if from_format == "asyncapi":
        if input is None or not input.exists():
            console.print("[red]--input is required for --from asyncapi[/red]")
            raise typer.Exit(1)
        from chaosrank.adapters.asyncapi import AsyncAPIAdapter
        adapter = AsyncAPIAdapter()

    elif from_format == "kafka":
        if input is None or not input.exists():
            console.print("[red]--input is required for --from kafka[/red]")
            raise typer.Exit(1)
        from chaosrank.adapters.kafka import KafkaAdapter
        adapter = KafkaAdapter()

    elif from_format == "confluent":
        if naming_strategy not in _NAMING_STRATEGIES:
            console.print(
                f"[red]Unknown --naming-strategy: {naming_strategy!r}. "
                f"Supported: {', '.join(_NAMING_STRATEGIES)}[/red]"
            )
            raise typer.Exit(1)
        if mode == "api" and not url:
            console.print("[red]--url is required for --from confluent --mode api[/red]")
            raise typer.Exit(1)
        if mode == "file" and (input is None or not input.exists()):
            console.print("[red]--input is required for --from confluent --mode file[/red]")
            raise typer.Exit(1)
        if mode not in ("file", "api"):
            console.print(f"[red]--mode must be 'file' or 'api', got {mode!r}[/red]")
            raise typer.Exit(1)
        try:
            from chaosrank.adapters.confluent import ConfluentSchemaRegistryAdapter
            adapter = ConfluentSchemaRegistryAdapter(
                mode=mode,
                url=url,
                token=token,
                naming_strategy=naming_strategy,
                kafka_path=kafka_fallback,
            )
        except ValueError as e:
            console.print(f"[red]Confluent adapter error: {e}[/red]")
            raise typer.Exit(1)

    # Use a sentinel path for api mode — adapter ignores it
    _input = input or Path("/dev/null")

    typer.echo(f"Converting {from_format} → async-deps.yaml...", err=True)
    try:
        dependencies = adapter.convert(_input)
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


@app.command()
def incidents(
    from_format: str  = typer.Option(...,    "--from",    help=f"Source: {', '.join(_INCIDENT_FORMATS)}"),
    token:       str | None  = typer.Option(None, "--token",   help="API key / token"),
    app_key:     str | None  = typer.Option(None, "--app-key", help="Application key (Datadog only: DD-APPLICATION-KEY)"),
    url:         str | None  = typer.Option(None, "--url",     help="Base URL (Alertmanager, Grafana OnCall)"),
    site:        str            = typer.Option("datadoghq.com", "--site", help="Datadog site hostname (default: datadoghq.com, EU: datadoghq.eu)"),
    window:      str            = typer.Option("30d", "--window", "-w", help="Lookback window, e.g. 7d, 30d"),
    output:      Path | None = typer.Option(None,  "--output", "-o", help="Output CSV path (omit to print to stdout)"),
    dry_run:     bool           = typer.Option(False, "--dry-run",      help="Print row count + sample without writing"),
    prometheus_url: str | None = typer.Option(
        None, "--prometheus-url",
        help=(
            "Prometheus base URL for request_volume backfill "
            "(e.g. http://prometheus:9090). "
            "Fills in request_volume for each incident before writing CSV."
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
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Fetch incidents from an alerting system and export as ChaosRank CSV."""
    _setup_logging(verbose)

    if from_format not in _INCIDENT_FORMATS:
        console.print(
            f"[red]Unknown --from '{from_format}'. "
            f"Supported: {', '.join(_INCIDENT_FORMATS)}[/red]"
        )
        raise typer.Exit(1)

    try:
        window_days = int(window.removesuffix("d"))
        if window_days <= 0:
            raise ValueError
    except ValueError:
        console.print("[red]--window must be a positive integer followed by 'd', e.g. 7d or 30d[/red]")
        raise typer.Exit(1)

    try:
        if from_format == "pagerduty":
            if not token:
                console.print("[red]--token is required for PagerDuty[/red]")
                raise typer.Exit(1)
            from chaosrank.incident_adapters.pagerduty import PagerDutyAdapter
            adapter = PagerDutyAdapter(api_key=token)

        elif from_format == "alertmanager":
            if not url:
                console.print("[red]--url is required for Alertmanager (e.g. http://alertmanager:9093)[/red]")
                raise typer.Exit(1)
            from chaosrank.incident_adapters.alertmanager import AlertmanagerAdapter
            adapter = AlertmanagerAdapter(url=url, token=token)

        elif from_format == "grafana-oncall":
            if not url or not token:
                console.print("[red]--url and --token are required for Grafana OnCall[/red]")
                raise typer.Exit(1)
            from chaosrank.incident_adapters.grafana_oncall import GrafanaOnCallAdapter
            adapter = GrafanaOnCallAdapter(url=url, token=token)

        elif from_format == "opsgenie":
            if not token:
                console.print("[red]--token is required for Opsgenie[/red]")
                raise typer.Exit(1)
            from chaosrank.incident_adapters.opsgenie import OpsgenieAdapter
            adapter = OpsgenieAdapter(api_key=token)

        elif from_format == "datadog":
            if not token:
                console.print("[red]--token (DD-API-KEY) is required for Datadog[/red]")
                raise typer.Exit(1)
            if not app_key:
                console.print("[red]--app-key (DD-APPLICATION-KEY) is required for Datadog[/red]")
                raise typer.Exit(1)
            from chaosrank.incident_adapters.datadog import DatadogIncidentAdapter
            adapter = DatadogIncidentAdapter(api_key=token, app_key=app_key, site=site)

    except typer.Exit:
        raise
    except Exception as e:
        console.print(f"[red]Failed to initialise adapter: {e}[/red]")
        raise typer.Exit(1)

    typer.echo(f"Fetching incidents from {from_format} (window: {window})…", err=True)

    try:
        fetched = adapter.fetch(window_days=window_days)
    except Exception as e:
        console.print(f"[red]Fetch failed: {e}[/red]")
        raise typer.Exit(1)

    if not fetched:
        console.print("[yellow]No incidents returned for the given window. Output will be empty.[/yellow]")

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
            fetched = backfiller.backfill(fetched)
        except Exception as e:
            console.print(f"[yellow]Warning: Prometheus backfill failed: {e}[/yellow]")

    if dry_run:
        console.print(f"[dim]{len(fetched)} incidents fetched.[/dim]")
        for inc in fetched[:5]:
            vol = f"{inc.request_volume:.0f}" if inc.request_volume is not None else "N/A"
            console.print(
                f"  {inc.timestamp.isoformat()}  {inc.service:<30} "
                f"{inc.severity:<10} {inc.type:<12} vol={vol}"
            )
        if len(fetched) > 5:
            console.print(f"  … and {len(fetched) - 5} more.")
        return

    from chaosrank.incident_adapters.csv_export import incidents_to_csv
    count = incidents_to_csv(fetched, output)

    if output:
        typer.echo(f"Written {count} incidents to {output}", err=True)


if __name__ == "__main__":
    app()