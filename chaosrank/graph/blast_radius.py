import logging

import networkx as nx

logger = logging.getLogger(__name__)

DEFAULT_W_PR = 0.5
DEFAULT_W_OD = 0.5
DEFAULT_ASYNC_WEIGHT_FACTOR = 0.5

ASYNC_SERVICE_PATTERNS = ("kafka", "sqs", "rabbitmq", "pubsub", "nats", "kinesis")


def compute_blast_radius(
    G: nx.DiGraph,
    w_pr: float = DEFAULT_W_PR,
    w_od: float = DEFAULT_W_OD,
    async_deps_provided: bool = False,
    async_weight_factor: float = DEFAULT_ASYNC_WEIGHT_FACTOR,
) -> dict[str, float]:
    """Compute a blended blast radius score per service.

    blast_radius(v) = w_pr * PageRank(G) + w_od * in_degree_centrality(G)

    Both components normalized to [0, 1] before blending.

    async_weight_factor: multiplier applied to async edge weights before scoring.
    Default 0.5 — reflects lower failure propagation probability for async channels
    vs synchronous calls. Set to 1.0 to treat async edges identically to sync edges.
    Only applied when async edges are present (edge_type='async').
    """
    if abs(w_pr + w_od - 1.0) > 1e-6:
        raise ValueError(f"w_pr + w_od must equal 1.0, got {w_pr + w_od}")

    if not 0.0 < async_weight_factor <= 1.0:
        raise ValueError(
            f"async_weight_factor must be in (0.0, 1.0], got {async_weight_factor}"
        )

    if G.number_of_nodes() == 0:
        logger.warning("Empty graph — no blast radius scores to compute")
        return {}

    _warn_async_blindspot(G, async_deps_provided)

    # Apply async_weight_factor to async edges before centrality computation.
    # We work on a view copy so the caller's graph is never mutated.
    G_scored = _apply_async_weight(G, async_weight_factor)

    try:
        pr = nx.pagerank(G_scored, weight="weight")
    except nx.PowerIterationFailedConvergence:
        logger.warning("PageRank failed to converge — falling back to uniform scores")
        pr = {n: 1.0 / G_scored.number_of_nodes() for n in G_scored.nodes()}

    if G_scored.number_of_nodes() > 1:
        od = nx.in_degree_centrality(G_scored)
    else:
        od = {n: 0.0 for n in G_scored.nodes()}

    pr_values = list(pr.values())
    pr_min, pr_max = min(pr_values), max(pr_values)
    pr_range = pr_max - pr_min
    pr_norm = (
        {n: (v - pr_min) / pr_range for n, v in pr.items()}
        if pr_range > 0
        else {n: 0.5 for n in pr}
    )

    od_values = list(od.values())
    od_min, od_max = min(od_values), max(od_values)
    od_range = od_max - od_min
    od_norm = (
        {n: (v - od_min) / od_range for n, v in od.items()}
        if od_range > 0
        else {n: 0.5 for n in od}
    )

    scores: dict[str, float] = {
        node: w_pr * pr_norm.get(node, 0.0) + w_od * od_norm.get(node, 0.0)
        for node in G_scored.nodes()
    }

    async_edge_count = sum(
        1 for _, _, d in G.edges(data=True) if d.get("edge_type") == "async"
    )
    if async_edge_count:
        logger.info(
            "Blast radius computed: %d async edges scaled by async_weight_factor=%.2f",
            async_edge_count, async_weight_factor,
        )

    logger.info(
        "Blast radius computed for %d services (w_pr=%.2f, w_od=%.2f)",
        len(scores), w_pr, w_od,
    )

    return scores


def _apply_async_weight(G: nx.DiGraph, factor: float) -> nx.DiGraph:
    """Return a copy of G with async edge weights scaled by factor.

    If factor == 1.0 or no async edges exist, returns G unchanged (no copy needed).
    """
    has_async = any(
        d.get("edge_type") == "async" for _, _, d in G.edges(data=True)
    )
    if not has_async or abs(factor - 1.0) < 1e-9:
        return G

    G_copy = G.copy()
    for u, v, data in G_copy.edges(data=True):
        if data.get("edge_type") == "async":
            G_copy[u][v]["weight"] = data.get("weight", 1) * factor
    return G_copy


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