import logging

import networkx as nx

logger = logging.getLogger(__name__)

DEFAULT_W_PR = 0.5
DEFAULT_W_OD = 0.5

ASYNC_SERVICE_PATTERNS = ("kafka", "sqs", "rabbitmq", "pubsub", "nats", "kinesis")


def compute_blast_radius(
    G: nx.DiGraph,
    w_pr: float = DEFAULT_W_PR,
    w_od: float = DEFAULT_W_OD,
    async_deps_provided: bool = False,
) -> dict[str, float]:
    """Compute a blended blast radius score per service: w_pr * PageRank(G) + w_od * in_degree_centrality(G), both normalized to [0, 1]."""
    if abs(w_pr + w_od - 1.0) > 1e-6:
        raise ValueError(f"w_pr + w_od must equal 1.0, got {w_pr + w_od}")

    if G.number_of_nodes() == 0:
        logger.warning("Empty graph — no blast radius scores to compute")
        return {}

    _warn_async_blindspot(G, async_deps_provided)

    try:
        pr = nx.pagerank(G, weight="weight")
    except nx.PowerIterationFailedConvergence:
        logger.warning("PageRank failed to converge — falling back to uniform scores")
        pr = {n: 1.0 / G.number_of_nodes() for n in G.nodes()}

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
        for node in G.nodes()
    }

    logger.info(
        "Blast radius computed for %d services (w_pr=%.2f, w_od=%.2f)",
        len(scores), w_pr, w_od,
    )

    return scores


def _warn_async_blindspot(G: nx.DiGraph, async_deps_provided: bool) -> None:
    async_nodes = [n for n in G.nodes() if any(p in n for p in ASYNC_SERVICE_PATTERNS)]

    if async_deps_provided:
        async_edge_count = sum(
            1 for _, _, data in G.edges(data=True)
            if data.get("edge_type") == "async"
        )
        logger.info(
            "Async deps provided — %d async edges merged into graph. "
            "Blast radius scores include async dependencies.",
            async_edge_count,
        )
    elif async_nodes:
        logger.warning(
            "Async messaging services detected in trace data. "
            "Blast radius scores may be incomplete for event-driven dependencies. "
            "Manually verify top-ranked services against known async dependency maps. "
            "Use --async-deps to provide a manifest. Detected: %s",
            ", ".join(sorted(async_nodes)),
        )