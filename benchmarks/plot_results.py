import csv
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

RESULTS_CSV  = Path("benchmarks/results/trial_results.csv")
OUTPUT_PNG   = Path("benchmarks/results/discovery_curve.png")
N_WEAKNESSES = 3

WEAKNESS_KEYS = ["uploadcreator_found_at", "uploadmedia_found_at", "urlservice_found_at"]


def load_results(path: Path) -> tuple[list, list]:
    """Load trial results CSV and return (chaosrank_rows, random_rows)."""
    chaosrank_rows = []
    random_rows    = []

    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            row = {k: int(v) if v.lstrip("-").isdigit() else v for k, v in row.items()}
            (chaosrank_rows if row["strategy"] == "chaosrank" else random_rows).append(row)

    return chaosrank_rows, random_rows


def discovery_curve(
    rows: list[dict],
    n_services: int,
    weakness_keys: list[str],
) -> tuple[list, list, list, list]:
    """Return (x, mean_y, lower_ci, upper_ci) for cumulative weakness discovery across trials."""
    n_trials = len(rows)

    per_trial_curves = []
    for row in rows:
        found_at = sorted(row[k] for k in weakness_keys if row[k] > 0)
        per_trial_curves.append([
            sum(1 for f in found_at if f <= x)
            for x in range(1, n_services + 1)
        ])

    x        = list(range(1, n_services + 1))
    mean_y   = []
    lower_ci = []
    upper_ci = []

    for step in range(n_services):
        values = [curve[step] for curve in per_trial_curves]
        mean   = sum(values) / n_trials
        if n_trials > 1:
            std = math.sqrt(sum((v - mean) ** 2 for v in values) / (n_trials - 1))
            ci  = 1.96 * std / math.sqrt(n_trials)
        else:
            ci = 0.0
        mean_y.append(mean)
        lower_ci.append(max(0, mean - ci))
        upper_ci.append(min(N_WEAKNESSES, mean + ci))

    return x, mean_y, lower_ci, upper_ci


def plot(chaosrank_rows: list, random_rows: list, n_services: int) -> None:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        print("matplotlib not installed. Run: pip install matplotlib")
        sys.exit(1)

    x, cr_mean,  cr_lo,  cr_hi  = discovery_curve(chaosrank_rows, n_services, WEAKNESS_KEYS)
    x, rnd_mean, rnd_lo, rnd_hi = discovery_curve(random_rows,    n_services, WEAKNESS_KEYS)

    fig, ax = plt.subplots(figsize=(10, 6))

    ax.plot(x, rnd_mean, color="#e74c3c", linewidth=2.5, label="Random selection (baseline)", zorder=3)
    ax.fill_between(x, rnd_lo, rnd_hi, color="#e74c3c", alpha=0.15, label="_nolegend_")

    ax.plot(x, cr_mean, color="#2ecc71", linewidth=2.5, label="ChaosRank (risk-ranked)", zorder=4)
    ax.fill_between(x, cr_lo, cr_hi, color="#2ecc71", alpha=0.15, label="_nolegend_")

    cr_all_found      = next((i + 1 for i, v in enumerate(cr_mean)  if v >= N_WEAKNESSES),       n_services)
    rnd_all_found_mean = next((i + 1 for i, v in enumerate(rnd_mean) if v >= N_WEAKNESSES - 0.1), n_services)

    ax.axvline(x=cr_all_found,       color="#2ecc71", linestyle="--", linewidth=1.2, alpha=0.7)
    ax.axvline(x=rnd_all_found_mean, color="#e74c3c", linestyle="--", linewidth=1.2, alpha=0.7)

    ax.annotate(
        f"ChaosRank: all found\nat experiment {cr_all_found}",
        xy=(cr_all_found, N_WEAKNESSES),
        xytext=(cr_all_found + 0.4, N_WEAKNESSES - 0.5),
        fontsize=9, color="#27ae60",
        arrowprops=dict(arrowstyle="->", color="#27ae60", lw=1.2),
    )

    ax.axhline(y=N_WEAKNESSES, color="gray", linestyle=":", linewidth=1, alpha=0.6)
    ax.text(n_services - 0.5, N_WEAKNESSES + 0.05, "All weaknesses found",
            ha="right", fontsize=8, color="gray")

    ax.set_xlabel("Experiments run", fontsize=12)
    ax.set_ylabel("Cumulative weaknesses discovered", fontsize=12)
    ax.set_title(
        "ChaosRank vs Random Selection\n"
        "Cumulative weakness discovery — 20 trials, 3 seeded weaknesses, 31 services\n"
        "Real topology: DeathStarBench social-network (UIUC/FIRM dataset, OSDI 2020)",
        fontsize=11, fontweight="bold",
    )
    ax.set_xlim(1, n_services)
    ax.set_ylim(0, N_WEAKNESSES + 0.3)
    ax.set_xticks(range(1, n_services + 1, 2))
    ax.set_yticks(range(0, N_WEAKNESSES + 1))
    ax.legend(fontsize=11, loc="lower right")
    ax.grid(True, alpha=0.3, linestyle="--")

    cr_mean_all    = sum(r["experiments_to_all"]   for r in chaosrank_rows) / len(chaosrank_rows)
    rnd_mean_all   = sum(r["experiments_to_all"]   for r in random_rows)    / len(random_rows)
    cr_mean_first  = sum(r["experiments_to_first"] for r in chaosrank_rows) / len(chaosrank_rows)
    rnd_mean_first = sum(r["experiments_to_first"] for r in random_rows)    / len(random_rows)

    stats_text = (
        f"Mean expts to first weakness:\n"
        f"  ChaosRank: {cr_mean_first:.1f}  |  Random: {rnd_mean_first:.1f}  "
        f"({rnd_mean_first/cr_mean_first:.1f}x)\n"
        f"Mean expts to all weaknesses:\n"
        f"  ChaosRank: {cr_mean_all:.1f}  |  Random: {rnd_mean_all:.1f}  "
        f"({rnd_mean_all/cr_mean_all:.1f}x)"
    )
    ax.text(
        0.02, 0.60, stats_text,
        transform=ax.transAxes,
        fontsize=8.5,
        verticalalignment="bottom",
        horizontalalignment="left",
        bbox=dict(boxstyle="round,pad=0.4", facecolor="lightyellow", edgecolor="gray", alpha=0.8),
    )

    plt.tight_layout()
    OUTPUT_PNG.parent.mkdir(parents=True, exist_ok=True)
    plt.savefig(OUTPUT_PNG, dpi=150, bbox_inches="tight")
    print(f"Chart saved: {OUTPUT_PNG}")


if __name__ == "__main__":
    cr_rows, rnd_rows = load_results(RESULTS_CSV)
    plot(cr_rows, rnd_rows, n_services=31)