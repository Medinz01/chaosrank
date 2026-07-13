"""CLI command for federated multi-domain ranking."""

from __future__ import annotations


from pathlib import Path
import typer
import yaml

from chaosrank.cli_utils import (
    console, _setup_logging, _load_config, _init_engine_client
)
from chaosrank.graph.builder import build_graph

def federation_rank(
    domains: list[str] = typer.Option(..., "--domain", "-d", help="Format: name:traces.json"),
    inter_domain: Path | None = typer.Option(None, "--inter-domain", help="Path to inter-domain edges yaml"),
    trace_format: str = typer.Option("jaeger", "--format", "-f"),
    config: Path = typer.Option(Path("chaosrank.yaml"), "--config"),
    output: str = typer.Option("table", "--output", "-o"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
):
    """Rank a federated multi-domain graph."""
    _setup_logging(verbose)
    cfg = _load_config(config)
    
    domain_payloads = []
    for d in domains:
        if ":" not in d:
            console.print(f"[red]Invalid domain format: {d}. Must be 'name:path'[/red]")
            raise typer.Exit(1)
        name, path = d.split(":", 1)
        path = Path(path)
        if not path.exists():
            console.print(f"[red]Trace file not found: {path}[/red]")
            raise typer.Exit(1)
        
        G = build_graph(path, trace_format=trace_format)
        from chaosrank.engine.serializer import graph_to_payload
        g_payload = graph_to_payload(G)
        
        domain_payloads.append({
            "domain_id": name,
            "edges": g_payload["edges"],
            "incidents": {},
            "components": g_payload["components"]
        })
        
    inter_edges = []
    if inter_domain and inter_domain.exists():
        with open(inter_domain) as f:
            yaml_data = yaml.safe_load(f)
            inter_edges = yaml_data.get("dependencies", yaml_data)
            
    client, engine_online = _init_engine_client(cfg, verbose)
    if not engine_online:
        console.print("[red]Engine must be online for federation ranking.[/red]")
        raise typer.Exit(1)
        
    try:
        ranked = client.federation_rank(domain_payloads, inter_edges)
        if output == "table":
            from chaosrank.output.table import render_table
            render_table(ranked, top_n=None)
        elif output == "json":
            from chaosrank.output.json_out import render_json
            render_json(ranked, async_deps_provided=False)
        else:
            console.print(f"[red]Format {output} not supported for federation[/red]")
            raise typer.Exit(1)
    except Exception as e:
        console.print(f"[red]Federation ranking failed: {e}[/red]")
        raise typer.Exit(1)
