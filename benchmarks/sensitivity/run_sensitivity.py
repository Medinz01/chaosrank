"""
benchmarks/sensitivity/run_sensitivity.py

Sensitivity analysis for ChaosRank hyperparameters.

Sweeps two hyperparameters and measures ranking stability via Kendall tau
against the default baseline ranking:

  1. alpha — blast radius weight in risk = alpha * BR + beta * FR
             Sweep: [0.4, 0.45, 0.5, 0.55, 0.6, 0.65, 0.7, 0.75, 0.8]
             Baseline: alpha=0.6 (default)
             Expected: Kendall tau > 0.85 for alpha in [0.5, 0.7]

  2. w_pr  — PageRank weight in blast_radius = w_pr * PR + w_od * ID
             Sweep: [0.3, 0.35, 0.4, 0.45, 0.5, 0.55, 0.6, 0.65, 0.7]
             Baseline: w_pr=0.5 (default)
             Expected: Kendall tau > 0.85 for w_pr in [0.4, 0.6]

Output:
  benchmarks/sensitivity/results/alpha_sweep.csv
  benchmarks/sensitivity/results/w_pr_sweep.csv
  benchmarks/sensitivity/results/alpha_sweep.png
  benchmarks/sensitivity/results/w_pr_sweep.png
  benchmarks/sensitivity/results/summary.txt

Usage:
  python benchmarks/sensitivity/run_sensitivity.py
  python benchmarks/sensitivity/run_sensitivity.py --traces path/to/traces.json \\
      --incidents path/to/incidents.csv
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

# Allow running from repo root
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import numpy as np
from scipy.stats import kendalltau

from chaosrank.graph.blast_radius import compute_blast_radius
from chaosrank.graph.builder import build_graph
from chaosrank.parser.incidents import parse_incidents
from chaosrank.scorer.ranker import rank_services

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

DEFAULT_TRACES    = Path("benchmarks/real_traces/social_network.json")
DEFAULT_INCIDENTS = Path("benchmarks/real_traces/social_network_incidents.csv")
RESULTS_DIR       = Path("benchmarks/sensitivity/results")

ALPHA_DEFAULT = 0.6
W_PR_DEFAULT  = 0.5

ALPHA_SWEEP = [round(v, 2) for v in np.arange(0.4, 0.81, 0.05)]
W_PR_SWEEP  = [round(v, 2) for v in np.arange(0.3, 0.71, 0.05)]


# ---------------------------------------------------------------------------
# Core helpers
# ---------------------------------------------------------------------------

def _ranking(blast: dict[str, float], incidents: dict, alpha: float) -> list[str]:
    """Return service names sorted by descending risk score."""
    beta = round(1.0 - alpha, 6)
    ranked = rank_services(
        blast_radius=blast,
        service_incidents=incidents,
        alpha=alpha,
        beta=beta,
    )
    # rank_services returns list[dict] with a "service" key
    if ranked and isinstance(ranked[0], dict):
        return [r["service"] for r in ranked]
    # or list of dataclass/namedtuple with .service attribute
    return [r.service for r in ranked]


def _blast(G, w_pr: float) -> dict[str, float]:
    return compute_blast_radius(G, w_pr=w_pr, w_od=round(1.0 - w_pr, 6))


def _tau(ranking_a: list[str], ranking_b: list[str]) -> float:
    """Kendall tau between two rankings expressed as service name lists."""
    services = list(dict.fromkeys(ranking_a + ranking_b))
    pos_a = {s: i for i, s in enumerate(ranking_a)}
    pos_b = {s: i for i, s in enumerate(ranking_b)}
    a = [pos_a.get(s, len(ranking_a)) for s in services]
    b = [pos_b.get(s, len(ranking_b)) for s in services]
    tau, _ = kendalltau(a, b)
    return round(float(tau), 4)


# ---------------------------------------------------------------------------
# Sweeps
# ---------------------------------------------------------------------------

def sweep_alpha(
    G,
    service_incidents: dict,
    baseline_blast: dict[str, float],
) -> list[dict]:
    baseline_ranking = _ranking(baseline_blast, service_incidents, ALPHA_DEFAULT)
    rows = []
    for alpha in ALPHA_SWEEP:
        ranking = _ranking(baseline_blast, service_incidents, alpha)
        tau = _tau(ranking, baseline_ranking)
        rows.append({
            "alpha": alpha,
            "beta": round(1.0 - alpha, 6),
            "kendall_tau": tau,
            "is_baseline": alpha == ALPHA_DEFAULT,
            "stable": tau >= 0.85,
        })
    return rows


def sweep_w_pr(
    G,
    service_incidents: dict,
    baseline_blast: dict[str, float],
    baseline_ranking: list[str],
) -> list[dict]:
    rows = []
    for w_pr in W_PR_SWEEP:
        blast = _blast(G, w_pr)
        ranking = _ranking(blast, service_incidents, ALPHA_DEFAULT)
        tau = _tau(ranking, baseline_ranking)
        rows.append({
            "w_pr": w_pr,
            "w_od": round(1.0 - w_pr, 6),
            "kendall_tau": tau,
            "is_baseline": w_pr == W_PR_DEFAULT,
            "stable": tau >= 0.85,
        })
    return rows


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

def _write_csv(rows: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    print(f"  Written: {path}")


def _write_plot(rows: list[dict], x_key: str, title: str, path: Path) -> None:
    try:
        import matplotlib.pyplot as plt
    except ImportError:
        print("  matplotlib not available — skipping plot (pip install chaosrank[benchmark])")
        return

    xs = [r[x_key] for r in rows]
    taus = [r["kendall_tau"] for r in rows]
    baseline_x = next(r[x_key] for r in rows if r["is_baseline"])

    fig, ax = plt.subplots(figsize=(8, 4))
    ax.plot(xs, taus, marker="o", color="#2563eb", linewidth=2, label="Kendall τ")
    ax.axhline(0.85, color="#dc2626", linestyle="--", linewidth=1, label="τ = 0.85 threshold")
    ax.axvline(baseline_x, color="#16a34a", linestyle=":", linewidth=1, label=f"default ({baseline_x})")
    ax.set_xlabel(x_key)
    ax.set_ylabel("Kendall τ (vs baseline)")
    ax.set_title(title)
    ax.set_ylim(0, 1.05)
    ax.legend()
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=150)
    plt.close(fig)
    print(f"  Written: {path}")


def _write_summary(
    alpha_rows: list[dict],
    w_pr_rows: list[dict],
    path: Path,
) -> None:
    lines = [
        "ChaosRank Sensitivity Analysis",
        "=" * 60,
        "",
        "ALPHA SWEEP (w_pr=0.5 fixed, baseline alpha=0.6)",
        "-" * 40,
    ]
    for r in alpha_rows:
        marker = " ← baseline" if r["is_baseline"] else ""
        flag   = " UNSTABLE" if not r["stable"] else ""
        lines.append(
            f"  alpha={r['alpha']:.2f}  beta={r['beta']:.2f}  "
            f"tau={r['kendall_tau']:.4f}{flag}{marker}"
        )

    stable_alphas = [r["alpha"] for r in alpha_rows if r["stable"]]
    stable_range_alpha = f"[{min(stable_alphas):.2f}, {max(stable_alphas):.2f}]" if stable_alphas else "none"
    lines += [
        "",
        f"  Stable range (tau >= 0.85): alpha in {stable_range_alpha}",
        "",
        "W_PR SWEEP (alpha=0.6 fixed, baseline w_pr=0.5)",
        "-" * 40,
    ]
    for r in w_pr_rows:
        marker = " ← baseline" if r["is_baseline"] else ""
        flag   = " UNSTABLE" if not r["stable"] else ""
        lines.append(
            f"  w_pr={r['w_pr']:.2f}  w_od={r['w_od']:.2f}  "
            f"tau={r['kendall_tau']:.4f}{flag}{marker}"
        )

    stable_wprs = [r["w_pr"] for r in w_pr_rows if r["stable"]]
    stable_range_wpr = f"[{min(stable_wprs):.2f}, {max(stable_wprs):.2f}]" if stable_wprs else "none"
    lines += [
        "",
        f"  Stable range (tau >= 0.85): w_pr in {stable_range_wpr}",
        "",
        "CONCLUSION",
        "-" * 40,
    ]

    alpha_ok = all(r["stable"] for r in alpha_rows if 0.5 <= r["alpha"] <= 0.7)
    w_pr_ok  = all(r["stable"] for r in w_pr_rows  if 0.4 <= r["w_pr"]  <= 0.6)

    lines.append(
        f"  alpha in [0.5, 0.7]: {'STABLE' if alpha_ok else 'UNSTABLE — review signal alignment'}"
    )
    lines.append(
        f"  w_pr  in [0.4, 0.6]: {'STABLE' if w_pr_ok  else 'UNSTABLE — review graph topology'}"
    )

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n")
    print(f"  Written: {path}")
    print()
    print("\n".join(lines))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="ChaosRank sensitivity analysis sweep")
    parser.add_argument(
        "--traces", type=Path, default=DEFAULT_TRACES,
        help=f"Path to Jaeger JSON traces (default: {DEFAULT_TRACES})",
    )
    parser.add_argument(
        "--incidents", type=Path, default=DEFAULT_INCIDENTS,
        help=f"Path to incidents CSV (default: {DEFAULT_INCIDENTS})",
    )
    args = parser.parse_args()

    if not args.traces.exists():
        print(f"ERROR: traces file not found: {args.traces}")
        sys.exit(1)
    if not args.incidents.exists():
        print(f"ERROR: incidents file not found: {args.incidents}")
        sys.exit(1)

    print("ChaosRank Sensitivity Analysis")
    print("=" * 60)
    print(f"  Traces:    {args.traces}")
    print(f"  Incidents: {args.incidents}")
    print()

    print("Building graph...")
    G = build_graph(args.traces)
    print(f"  {G.number_of_nodes()} services, {G.number_of_edges()} edges")

    print("Parsing incidents...")
    service_incidents = parse_incidents(args.incidents)
    print(f"  {sum(len(v.incidents) for v in service_incidents.values())} incidents across {len(service_incidents)} services")
    print()

    # Baseline blast radius (w_pr=0.5)
    baseline_blast   = _blast(G, W_PR_DEFAULT)
    baseline_ranking = _ranking(baseline_blast, service_incidents, ALPHA_DEFAULT)

    print(f"Baseline ranking (alpha={ALPHA_DEFAULT}, w_pr={W_PR_DEFAULT}):")
    for i, s in enumerate(baseline_ranking[:10], 1):
        print(f"  {i:2d}. {s}")
    print()

    print("Running alpha sweep...")
    alpha_rows = sweep_alpha(G, service_incidents, baseline_blast)

    print("Running w_pr sweep...")
    w_pr_rows = sweep_w_pr(G, service_incidents, baseline_blast, baseline_ranking)

    print()
    print("Writing results...")
    _write_csv(alpha_rows, RESULTS_DIR / "alpha_sweep.csv")
    _write_csv(w_pr_rows,  RESULTS_DIR / "w_pr_sweep.csv")
    _write_plot(
        alpha_rows, "alpha",
        "Ranking stability vs alpha (blast radius weight)",
        RESULTS_DIR / "alpha_sweep.png",
    )
    _write_plot(
        w_pr_rows, "w_pr",
        "Ranking stability vs w_pr (PageRank blend weight)",
        RESULTS_DIR / "w_pr_sweep.png",
    )
    _write_summary(alpha_rows, w_pr_rows, RESULTS_DIR / "summary.txt")


if __name__ == "__main__":
    main()