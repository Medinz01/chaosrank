from rich.console import Console
from rich.table import Table
from rich import box

CONFIDENCE_COLORS = {
    "high":   "green",
    "medium": "yellow",
    "low":    "red",
}

RISK_THRESHOLDS = {
    0.75: "bold red",
    0.50: "yellow",
    0.00: "white",
}


def _risk_color(score: float) -> str:
    for threshold, color in RISK_THRESHOLDS.items():
        if score >= threshold:
            return color
    return "white"


def render_table(
    ranked: list[dict],
    top_n: int | None = None,
    title: str = "ChaosRank — Risk-Driven Experiment Schedule",
) -> None:
    """Render ranked services as a Rich table to stdout."""
    console = Console()

    table = Table(
        title=title,
        box=box.ROUNDED,
        show_header=True,
        header_style="bold cyan",
        title_style="bold white",
        border_style="bright_black",
    )

    table.add_column("Rank",            justify="center", style="bold", width=6)
    table.add_column("Service",         justify="left",   min_width=20)
    table.add_column("Risk",            justify="center", width=8)
    table.add_column("Blast Radius",    justify="center", width=13)
    table.add_column("Fragility",       justify="center", width=11)
    table.add_column("Suggested Fault", justify="left",   min_width=20)
    table.add_column("Confidence",      justify="center", width=12)

    rows = ranked[:top_n] if top_n else ranked

    for row in rows:
        color      = _risk_color(row["risk"])
        conf_color = CONFIDENCE_COLORS.get(row["confidence"], "white")

        table.add_row(
            f"[{color}]{row['rank']}[/{color}]",
            f"[{color}]{row['service']}[/{color}]",
            f"[{color}]{row['risk']:.3f}[/{color}]",
            f"{row['blast_radius']:.3f}",
            f"{row['fragility']:.3f}",
            row["suggested_fault"],
            f"[{conf_color}]{row['confidence']}[/{conf_color}]",
        )

    console.print()
    console.print(table)
    console.print()

    total = len(ranked)
    shown = len(rows)
    if top_n and total > top_n:
        console.print(
            f"  [bright_black]Showing top {shown} of {total} services. "
            f"Use --top-n {total} to show all.[/bright_black]"
        )
        console.print()
