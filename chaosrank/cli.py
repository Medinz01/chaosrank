"""Main entrypoint for the ChaosRank CLI, registering all commands."""
from __future__ import annotations


import typer
from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from chaosrank.commands.rank import rank_cmd
from chaosrank.commands.graph import graph_cmd
from chaosrank.commands.convert import convert_cmd
from chaosrank.commands.incidents import incidents_cmd
from chaosrank.commands.adaptive import adaptive_rank, adaptive_outcome, adaptive_summary
from chaosrank.commands.federation import federation_rank
from chaosrank.commands.orchestration import orchestration_merge
from chaosrank.commands.init import init_cmd
from chaosrank.commands.info import info_cmd
from chaosrank.commands.dashboard import dashboard_cmd
app = typer.Typer(
    name="chaosrank",
    help="Risk-driven chaos experiment scheduler.",
    add_completion=False,
    invoke_without_command=True,
)

adaptive_app = typer.Typer(help="Adaptive Reinforcement Learning ranking commands")
app.add_typer(adaptive_app, name="adaptive")

federation_app = typer.Typer(help="Multi-Domain Federation ranking commands")
app.add_typer(federation_app, name="federation")

orchestration_app = typer.Typer(help="Multi-Agent Orchestration commands")
app.add_typer(orchestration_app, name="orchestration")

console = Console()

def _version_callback(value: bool) -> None:
    if value:
        from importlib.metadata import version
        try:
            v = version("chaosrank-cli")
        except Exception:
            v = "1.0.0"
        typer.echo(f"ChaosRank CLI version {v}")
        raise typer.Exit()

@app.callback(invoke_without_command=True)
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
    if ctx.invoked_subcommand is None:
        title = Text("ChaosRank CLI", justify="center", style="bold magenta")
        subtitle = Text("Risk-driven chaos experiment scheduler", justify="center", style="cyan")
        
        content = Text.assemble(
            title, "\n", subtitle, "\n\n",
            "Get started:\n",
            ("  chaosrank init\n", "bold green"),
            ("  chaosrank info\n", "bold green"),
            ("  chaosrank rank --traces traces.json\n", "bold green"),
            ("\nType ", ""),
            ("chaosrank --help", "bold yellow"),
            (" for a full list of commands.", "")
        )
        
        console.print(Panel(content, border_style="bright_magenta", padding=(1, 2)))

# Register main commands
app.command(name="rank")(rank_cmd)
app.command(name="graph")(graph_cmd)
app.command(name="convert")(convert_cmd)
app.command(name="incidents")(incidents_cmd)
app.command(name="init")(init_cmd)
app.command(name="info")(info_cmd)
app.command(name="dashboard")(dashboard_cmd)

# Register Adaptive commands
adaptive_app.command(name="rank")(adaptive_rank)
adaptive_app.command(name="outcome")(adaptive_outcome)
adaptive_app.command(name="summary")(adaptive_summary)

# Register Federation commands
federation_app.command(name="rank")(federation_rank)

# Register Orchestration commands
orchestration_app.command(name="merge")(orchestration_merge)

if __name__ == "__main__":
    app()