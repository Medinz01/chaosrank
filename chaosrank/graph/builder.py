import logging
from pathlib import Path

import networkx as nx

from chaosrank.parser.jaeger import parse_traces

logger = logging.getLogger(__name__)


def build_graph(
    traces_path: Path,
    min_call_frequency: int = 10,
    trace_format: str = "jaeger",
    otlp_format: str = "json",
) -> nx.DiGraph:
    """Build a NetworkX DiGraph from a trace export file.

    Args:
        traces_path:        Path to trace export file.
        min_call_frequency: Drop edges with fewer calls. Default 10.
        trace_format:       "jaeger" | "otlp". Default "jaeger".
        otlp_format:        "json" | "protobuf". Only used when trace_format="otlp".
                            Default "json" (existing behaviour, no change).
    """
    if trace_format == "jaeger":
        edges = parse_traces(traces_path, min_call_frequency=min_call_frequency)

    elif trace_format == "otlp":
        if otlp_format == "protobuf":
            from chaosrank.parser.otlp_proto import parse_otlp_proto
            edges = parse_otlp_proto(traces_path, min_call_frequency=min_call_frequency)
        else:
            from chaosrank.parser.otlp import parse_otlp
            edges = parse_otlp(traces_path, min_call_frequency=min_call_frequency)

    else:
        raise ValueError(
            f"Unknown trace format: {trace_format!r}. Supported: jaeger, otlp"
        )

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
    return G.reverse(copy=True)