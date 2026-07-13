"""CLI command for converting async topology sources to async-deps.yaml."""

from __future__ import annotations


import sys
from pathlib import Path
import typer
import yaml

from chaosrank.cli_utils import (
    console, _setup_logging, _SUPPORTED_FORMATS, _NAMING_STRATEGIES
)

def convert_cmd(
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
