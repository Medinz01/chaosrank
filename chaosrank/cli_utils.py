"""Shared helpers for the CLI, mostly logging and setup stuff."""
from __future__ import annotations


import logging
import sys
from pathlib import Path

import typer
import yaml
from rich.console import Console

from chaosrank.engine.client import EngineClient

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


def _init_engine_client(cfg: dict, verbose: bool = False) -> tuple[EngineClient, bool]:
    engine_url = cfg.get("engine", {}).get("url", "http://localhost:8080")
    engine_timeout = cfg.get("engine", {}).get("timeout_seconds", 30)

    client = EngineClient(url=engine_url, timeout=engine_timeout)

    if verbose:
        typer.echo("Checking ChaosRank Engine status...", err=True)
    engine_online = client.health()
    if not engine_online:
        console.print(
            f"[yellow]Warning: ChaosRank Engine at {engine_url} is offline. "
            "Proceeding with data parsing and preprocessing...[/yellow]"
        )
    return client, engine_online


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
