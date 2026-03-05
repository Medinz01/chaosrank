import csv
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from chaosrank.graph.blast_radius import compute_blast_radius
from chaosrank.graph.builder import build_graph
from chaosrank.parser.incidents import parse_incidents
from chaosrank.scorer.ranker import rank_services

TRACES_PATH    = Path("benchmarks/real_traces/social_network.json")
INCIDENTS_PATH = Path("benchmarks/real_traces/social_network_incidents.csv")
RESULTS_DIR    = Path("benchmarks/results")
RESULTS_CSV    = RESULTS_DIR / "trial_results.csv"

N_TRIALS    = 20
RANDOM_SEED = 42

WEAKNESSES = {
    "composepost-uploadcreator": "latency-injection",
    "composepost-uploadmedia":   "latency-injection",
    "urlservice-upload":         "latency-injection",
}

ALPHA = 0.6
BETA  = 0.4


def get_chaosrank_order() -> list[str]:
    """Return the deterministic ChaosRank-ranked service list."""
    G = build_graph(TRACES_PATH, min_call_frequency=1)
    blast = compute_blast_radius(G)
    ranked = rank_services(
        blast_radius=blast,
        service_incidents=parse_incidents(INCIDENTS_PATH),
        alpha=ALPHA,
        beta=BETA,
    )
    return [r["service"] for r in ranked]


def simulate_trial(order: list[str], weaknesses: set[str]) -> dict[str, int]:
    """Return {weakness: experiment_number_when_found} for one trial."""
    found = {}
    remaining = set(weaknesses)

    for i, service in enumerate(order, start=1):
        if service in remaining:
            found[service] = i
            remaining.discard(service)
        if not remaining:
            break

    for w in remaining:
        found[w] = len(order) + 1

    return found


def run_benchmark() -> dict:
    """Run N_TRIALS trials for ChaosRank and random selection and write results to CSV."""
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    weaknesses = set(WEAKNESSES.keys())
    chaosrank_order = get_chaosrank_order()
    all_services = chaosrank_order[:]

    print(f"\nChaosRank order: {chaosrank_order}")
    print(f"Seeded weaknesses: {weaknesses}")
    print(f"Services in graph: {len(all_services)}")
    print(f"\nRunning {N_TRIALS} trials per strategy...\n")

    rng = random.Random(RANDOM_SEED)

    rows = []
    chaosrank_totals = []
    random_totals    = []

    for trial in range(1, N_TRIALS + 1):
        cr_found = simulate_trial(chaosrank_order, weaknesses)
        cr_experiments_to_first = min(cr_found.values())
        cr_experiments_to_all   = max(cr_found.values())

        random_order = all_services[:]
        rng.shuffle(random_order)
        rnd_found = simulate_trial(random_order, weaknesses)
        rnd_experiments_to_first = min(rnd_found.values())
        rnd_experiments_to_all   = max(rnd_found.values())

        chaosrank_totals.append(cr_experiments_to_all)
        random_totals.append(rnd_experiments_to_all)

        rows.append({
            "trial":                  trial,
            "strategy":               "chaosrank",
            "experiments_to_first":   cr_experiments_to_first,
            "experiments_to_all":     cr_experiments_to_all,
            "uploadcreator_found_at": cr_found.get("composepost-uploadcreator", -1),
            "uploadmedia_found_at":   cr_found.get("composepost-uploadmedia", -1),
            "urlservice_found_at":    cr_found.get("urlservice-upload", -1),
        })
        rows.append({
            "trial":                  trial,
            "strategy":               "random",
            "experiments_to_first":   rnd_experiments_to_first,
            "experiments_to_all":     rnd_experiments_to_all,
            "uploadcreator_found_at": rnd_found.get("composepost-uploadcreator", -1),
            "uploadmedia_found_at":   rnd_found.get("composepost-uploadmedia", -1),
            "urlservice_found_at":    rnd_found.get("urlservice-upload", -1),
        })

        print(
            f"Trial {trial:2d} | "
            f"ChaosRank: first={cr_experiments_to_first}, all={cr_experiments_to_all} | "
            f"Random:    first={rnd_experiments_to_first}, all={rnd_experiments_to_all}"
        )

    with open(RESULTS_CSV, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "trial", "strategy",
            "experiments_to_first", "experiments_to_all",
            "uploadcreator_found_at", "uploadmedia_found_at", "urlservice_found_at",
        ])
        writer.writeheader()
        writer.writerows(rows)

    cr_mean  = sum(chaosrank_totals) / N_TRIALS
    rnd_mean = sum(random_totals)    / N_TRIALS
    cr_first_mean  = sum(r["experiments_to_first"] for r in rows if r["strategy"] == "chaosrank") / N_TRIALS
    rnd_first_mean = sum(r["experiments_to_first"] for r in rows if r["strategy"] == "random")    / N_TRIALS

    print(f"\n{'='*60}")
    print(f"RESULTS — {N_TRIALS} trials each")
    print(f"{'='*60}")
    print(f"{'Metric':<40} {'ChaosRank':>10} {'Random':>10}")
    print(f"{'-'*60}")
    print(f"{'Mean experiments to FIRST weakness':<40} {cr_first_mean:>10.1f} {rnd_first_mean:>10.1f}")
    print(f"{'Mean experiments to ALL weaknesses':<40} {cr_mean:>10.1f} {rnd_mean:>10.1f}")
    print(f"{'Improvement (first weakness)':<40} {rnd_first_mean/cr_first_mean:>10.1f}x {'':>10}")
    print(f"{'Improvement (all weaknesses)':<40} {rnd_mean/cr_mean:>10.1f}x {'':>10}")
    print(f"{'='*60}")
    print(f"\nResults written to: {RESULTS_CSV}")

    return {
        "cr_mean_to_all":    cr_mean,
        "rnd_mean_to_all":   rnd_mean,
        "cr_mean_to_first":  cr_first_mean,
        "rnd_mean_to_first": rnd_first_mean,
        "chaosrank_totals":  chaosrank_totals,
        "random_totals":     random_totals,
        "rows":              rows,
        "n_services":        len(all_services),
    }


if __name__ == "__main__":
    run_benchmark()