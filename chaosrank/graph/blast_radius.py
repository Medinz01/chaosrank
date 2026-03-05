import logging

import networkx as nx

logger = logging.getLogger(__name__)

DEFAULT_W_PR = 0.5
DEFAULT_W_OD = 0.5


def compute_blast_radius(
    G: nx.DiGraph,
    w_pr: float = DEFAULT_W_PR,
    w_od: float = DEFAULT_W_OD,
) -> dict[str, float]:
    """Compute a blended blast radius score per service: w_pr * PageRank(G) + w_od * in_degree_centrality(G), both normalized to [0, 1]."""
    if abs(w_pr + w_od - 1.0) > 1e-6:
        raise ValueError(f"w_pr + w_od must equal 1.0, got {w_pr + w_od}")

    if G.number_of_nodes() == 0:
        logger.warning("Empty graph — no blast radius scores to compute")
        return {}

    GT = G.reverse(copy=True)

    # PageRank on G (not GT): random walk rewards frequently-called services, which is the intended signal
    try:
        pr = nx.pagerank(G, weight="weight")
    except nx.PowerIterationFailedConvergence:
        logger.warning("PageRank failed to converge — falling back to uniform scores")
        pr = {n: 1.0 / GT.number_of_nodes() for n in GT.nodes()}

    if G.number_of_nodes() > 1:
        od = nx.in_degree_centrality(G)
    else:
        od = {n: 0.0 for n in G.nodes()}

    pr_values = list(pr.values())
    pr_min, pr_max = min(pr_values), max(pr_values)
    pr_range = pr_max - pr_min
    pr_norm = {n: (v - pr_min) / pr_range for n, v in pr.items()} if pr_range > 0 else {n: 0.5 for n in pr}

    od_values = list(od.values())
    od_min, od_max = min(od_values), max(od_values)
    od_range = od_max - od_min
    od_norm = {n: (v - od_min) / od_range for n, v in od.items()} if od_range > 0 else {n: 0.5 for n in od}

    scores: dict[str, float] = {
        node: w_pr * pr_norm.get(node, 0.0) + w_od * od_norm.get(node, 0.0)
        for node in GT.nodes()
    }

    logger.info(
        "Blast radius computed for %d services (w_pr=%.2f, w_od=%.2f)",
        len(scores), w_pr, w_od,
    )

    return scores
