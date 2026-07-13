"""CLI command for fetching incidents from external systems."""

from __future__ import annotations


from pathlib import Path
import typer

from chaosrank.cli_utils import (
    console, _setup_logging, _INCIDENT_FORMATS
)

def incidents_cmd(
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
