"""CLI command for outputting the service graph."""

from __future__ import annotations


from pathlib import Path
import typer
import yaml

from chaosrank.cli_utils import (
    console, _setup_logging, _load_config, _TRACE_FORMATS, _OTLP_FORMATS
)
from chaosrank.graph.builder import build_graph

def graph_cmd(
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
