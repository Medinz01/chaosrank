import logging
from pathlib import Path

import networkx as nx

from chaosrank.parser.jaeger import parse_traces

logger = logging.getLogger(__name__)


def build_graph(
    traces_path: Path,
    min_call_frequency: int = 10,
) -> nx.DiGraph:
    """Build a weighted directed dependency graph (caller → callee) from Jaeger traces."""
    edges = parse_traces(traces_path, min_call_frequency=min_call_frequency)

    G = nx.DiGraph()
    for (caller, callee), weight in edges.items():
        G.add_edge(caller, callee, weight=weight)

    logger.info("Built graph: %d services, %d edges", G.number_of_nodes(), G.number_of_edges())

    if G.number_of_edges() < 5:
        logger.warning(
            "Graph has fewer than 5 edges (%d). "
            "Blast radius scores may be unreliable. "
            "Check min_call_frequency or trace window size.",
            G.number_of_edges(),
        )

    return G


def reverse_graph(G: nx.DiGraph) -> nx.DiGraph:
    """Return G^T so edge direction encodes 'depended upon by' rather than 'calls'."""
    return G.reverse(copy=True)
