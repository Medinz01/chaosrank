import networkx as nx
import pytest

from chaosrank.graph.blast_radius import compute_blast_radius
from chaosrank.graph.builder import reverse_graph


def make_graph(edges: list[tuple[str, str]], weight: int = 20) -> nx.DiGraph:
    """Build a DiGraph from an edge list with uniform weight (caller -> callee)."""
    G = nx.DiGraph()
    for u, v in edges:
        G.add_edge(u, v, weight=weight)
    return G


class TestCalleeModel:

    def test_service_with_more_callers_scores_higher(self):
        G = make_graph([
            ("frontend", "payment-service"),
            ("mobile",   "payment-service"),
            ("api-gw",   "payment-service"),
            ("frontend", "auth-service"),
        ])
        scores = compute_blast_radius(G)
        assert scores["payment-service"] > scores["auth-service"], (
            f"payment-service (3 callers) should outscore auth-service (1 caller). "
            f"Scores: {scores}"
        )

    def test_shared_dependency_scores_highest(self):
        G = make_graph([
            ("frontend", "payment-service"),
            ("mobile",   "payment-service"),
            ("api-gw",   "payment-service"),
            ("checkout", "payment-service"),
            ("cart",     "payment-service"),
        ])
        scores = compute_blast_radius(G)
        assert scores["payment-service"] == max(scores.values()), (
            f"payment-service (5 callers) should have highest blast radius. "
            f"Scores: {scores}"
        )

    def test_fan_out_source_scores_low(self):
        """hub has zero callers; its callees each have one — they should outscore it."""
        G = make_graph([
            ("hub", "A"), ("hub", "B"), ("hub", "C"),
            ("hub", "D"), ("hub", "E"),
        ])
        scores = compute_blast_radius(G)
        for callee in ["A", "B", "C", "D", "E"]:
            assert scores[callee] >= scores["hub"], (
                f"{callee} should score >= hub (hub has no callers). Scores: {scores}"
            )

    def test_pure_callee_beats_pure_caller(self):
        G = make_graph([
            ("frontend", "payment-service"),
            ("frontend", "auth-service"),
            ("frontend", "cart-service"),
        ])
        scores = compute_blast_radius(G)
        for callee in ["payment-service", "auth-service", "cart-service"]:
            assert scores[callee] > scores["frontend"], (
                f"{callee} (has callers) should outscore frontend (no callers). "
                f"Scores: {scores}"
            )


class TestDeepNarrowChain:

    def test_chain_root_is_not_highest(self):
        """PageRank on G flows along edges toward the terminal sink; root has no in-edges so scores lowest."""
        G = make_graph([
            ("root", "X"), ("X", "Y"), ("Y", "Z"), ("Z", "W"),
        ])
        scores = compute_blast_radius(G)

        assert scores["X"] > scores["root"], (
            f"X (called by root) should outscore root (no in-edges). Scores: {scores}"
        )
        assert scores["W"] > scores["root"], (
            f"W (terminal sink) should outscore root. Scores: {scores}"
        )

    def test_chain_intermediate_ordering(self):
        """W is the terminal sink — accumulates all PageRank flow — so W >= Z >= Y >= X > root."""
        G = make_graph([
            ("root", "X"), ("X", "Y"), ("Y", "Z"), ("Z", "W"),
        ])
        scores = compute_blast_radius(G)

        assert scores["W"] >= scores["Z"] >= scores["Y"] >= scores["X"], (
            f"Chain ordering violated: expected W>=Z>=Y>=X. Scores: {scores}"
        )
        assert scores["X"] > scores["root"], (
            f"X should outscore root (root has no in-edges). Scores: {scores}"
        )

    def test_chain_with_multiple_entry_points(self):
        """X has 2 callers (root1, root2) plus a downstream chain — should score highest."""
        G = make_graph([
            ("root1", "X"), ("root2", "X"),
            ("X", "Y"), ("Y", "Z"), ("Z", "W"),
        ])
        scores = compute_blast_radius(G)
        assert scores["X"] == max(scores.values()), (
            f"X (2 callers + chain position) should have highest blast radius. "
            f"Scores: {scores}"
        )


class TestGraphReversal:

    def test_reverse_graph_flips_edge_direction(self):
        G = make_graph([("frontend", "payment-service")])
        GT = reverse_graph(G)

        assert GT.has_edge("payment-service", "frontend")
        assert not GT.has_edge("frontend", "payment-service")

    def test_reversal_preserves_edge_weights(self):
        G = nx.DiGraph()
        G.add_edge("frontend", "payment-service", weight=42)
        GT = reverse_graph(G)
        assert GT["payment-service"]["frontend"]["weight"] == 42

    def test_callee_with_more_callers_scores_higher(self):
        G = make_graph([
            ("frontend", "payment-service"),
            ("mobile",   "payment-service"),
            ("api-gw",   "payment-service"),
            ("frontend", "auth-service"),
        ])
        scores = compute_blast_radius(G)
        assert scores["payment-service"] > scores["auth-service"], (
            f"payment-service (3 callers) should outscore auth-service (1 caller). "
            f"Scores: {scores}"
        )

    def test_symmetric_star_callees_all_equal(self):
        """Three callees each with exactly one caller should score identically; their caller scores lower."""
        G = make_graph([
            ("frontend", "payment-service"),
            ("frontend", "auth-service"),
            ("frontend", "cart-service"),
        ])
        scores = compute_blast_radius(G)

        assert scores["payment-service"] == pytest.approx(scores["auth-service"], abs=1e-6)
        assert scores["payment-service"] == pytest.approx(scores["cart-service"],  abs=1e-6)
        assert scores["payment-service"] > scores["frontend"]


class TestBlendWeights:

    def test_invalid_weights_raise(self):
        G = make_graph([("a", "b")])
        with pytest.raises(ValueError, match="w_pr.*w_od"):
            compute_blast_radius(G, w_pr=0.7, w_od=0.7)

    def test_all_scores_in_unit_interval(self):
        G = make_graph([
            ("frontend", "payment"), ("frontend", "auth"),
            ("payment",  "db"),      ("auth",     "db"),
        ])
        scores = compute_blast_radius(G)
        for svc, score in scores.items():
            assert 0.0 <= score <= 1.0, f"{svc} score {score} out of [0,1]"

    def test_pagerank_heavy_surfaces_terminal_sink(self):
        """w_pr=0.8 dominates; PageRank on G flows toward the terminal sink W."""
        G = make_graph([
            ("root", "X"), ("X", "Y"), ("Y", "Z"), ("Z", "W"),
        ])
        scores = compute_blast_radius(G, w_pr=0.8, w_od=0.2)
        assert scores["W"] == max(scores.values()), (
            f"W (terminal sink) should have highest score with w_pr=0.8. Scores: {scores}"
        )

    def test_outdegree_heavy_surfaces_most_called_service(self):
        """w_od=0.8 dominates; in-degree on G (= number of callers) determines ranking."""
        G = make_graph([
            ("c1", "payment-service"),
            ("c2", "payment-service"),
            ("c3", "payment-service"),
            ("c4", "payment-service"),
            ("c5", "payment-service"),
        ])
        scores = compute_blast_radius(G, w_pr=0.2, w_od=0.8)
        assert scores["payment-service"] == max(scores.values()), (
            f"payment-service (5 callers) should score highest with w_od=0.8. "
            f"Scores: {scores}"
        )

    def test_pure_blast_radius_weight(self):
        G = make_graph([
            ("a", "shared"), ("b", "shared"), ("c", "shared"),
            ("a", "leaf"),
        ])
        scores = compute_blast_radius(G, w_pr=1.0, w_od=0.0)
        assert scores["shared"] == max(scores.values())


class TestEdgeCases:

    def test_single_node_no_crash(self):
        G = nx.DiGraph()
        G.add_node("lonely-service")
        scores = compute_blast_radius(G)
        assert "lonely-service" in scores
        assert 0.0 <= scores["lonely-service"] <= 1.0

    def test_empty_graph_returns_empty(self):
        G = nx.DiGraph()
        assert compute_blast_radius(G) == {}

    def test_isolated_node_scores_at_most_connected_nodes(self):
        G = make_graph([("frontend", "payment-service")])
        G.add_node("isolated")
        scores = compute_blast_radius(G)
        assert scores["payment-service"] > scores["isolated"], (
            f"payment-service (has callers) should outscore isolated node. "
            f"Scores: {scores}"
        )

    def test_two_node_graph_callee_beats_caller(self):
        G = make_graph([("caller", "callee")])
        scores = compute_blast_radius(G)
        assert scores["callee"] > scores["caller"], (
            f"callee should outscore caller in 2-node graph. Scores: {scores}"
        )

    def test_diamond_topology(self):
        """D has 2 callers and is the terminal node; A has 0 callers and is the entry point."""
        G = make_graph([
            ("A", "B"), ("A", "C"),
            ("B", "D"), ("C", "D"),
        ])
        scores = compute_blast_radius(G)
        assert scores["D"] == max(scores.values()), (
            f"D (2 callers, terminal) should score highest. Scores: {scores}"
        )
        assert scores["A"] == min(scores.values()), (
            f"A (0 callers, entry point) should score lowest. Scores: {scores}"
        )