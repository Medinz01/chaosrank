import statistics
from pathlib import Path

import networkx as nx
# import pytest
import yaml

from chaosrank.parser.async_deps import parse_async_deps


def make_graph(edges: list[tuple[str, str]], weight: int = 20) -> nx.DiGraph:
    G = nx.DiGraph()
    for u, v in edges:
        G.add_edge(u, v, weight=weight)
    return G


def write_manifest(tmp_path: Path, dependencies: list[dict]) -> Path:
    path = tmp_path / "async-deps.yaml"
    path.write_text(yaml.dump({"dependencies": dependencies}))
    return path


class TestEdgeMerging:

    def test_async_edge_added_to_graph(self, tmp_path):
        G = make_graph([("frontend", "payment-service")])
        manifest = write_manifest(tmp_path, [
            {"producer": "order-service", "consumer": "inventory-service", "channel": "kafka", "topic": "orders"}
        ])
        G = parse_async_deps(manifest, G)
        assert G.has_edge("order-service", "inventory-service")

    def test_async_edge_type_annotated(self, tmp_path):
        G = make_graph([("frontend", "payment-service")])
        manifest = write_manifest(tmp_path, [
            {"producer": "order-service", "consumer": "inventory-service", "channel": "kafka", "topic": "orders"}
        ])
        G = parse_async_deps(manifest, G)
        assert G["order-service"]["inventory-service"]["edge_type"] == "async"

    def test_async_edge_channel_and_topic_stored(self, tmp_path):
        G = make_graph([("frontend", "payment-service")])
        manifest = write_manifest(tmp_path, [
            {"producer": "order-service", "consumer": "inventory-service", "channel": "kafka", "topic": "order-placed"}
        ])
        G = parse_async_deps(manifest, G)
        edge = G["order-service"]["inventory-service"]
        assert edge["channel"] == "kafka"
        assert edge["topic"] == "order-placed"

    def test_queue_field_used_when_topic_absent(self, tmp_path):
        G = make_graph([("frontend", "payment-service")])
        manifest = write_manifest(tmp_path, [
            {"producer": "payment-service", "consumer": "notification-service", "channel": "sqs", "queue": "payment-events"}
        ])
        G = parse_async_deps(manifest, G)
        assert G["payment-service"]["notification-service"]["topic"] == "payment-events"

    def test_multiple_async_edges_all_added(self, tmp_path):
        G = make_graph([("frontend", "payment-service")])
        manifest = write_manifest(tmp_path, [
            {"producer": "order-service",   "consumer": "inventory-service",   "channel": "kafka"},
            {"producer": "payment-service", "consumer": "notification-service", "channel": "sqs"},
            {"producer": "order-service",   "consumer": "audit-service",        "channel": "kafka"},
        ])
        G = parse_async_deps(manifest, G)
        assert G.has_edge("order-service", "inventory-service")
        assert G.has_edge("payment-service", "notification-service")
        assert G.has_edge("order-service", "audit-service")

    def test_new_services_added_as_nodes(self, tmp_path):
        G = make_graph([("frontend", "payment-service")])
        manifest = write_manifest(tmp_path, [
            {"producer": "brand-new-producer", "consumer": "brand-new-consumer", "channel": "kafka"}
        ])
        G = parse_async_deps(manifest, G)
        assert "brand-new-producer" in G.nodes()
        assert "brand-new-consumer" in G.nodes()


class TestEdgeWeight:

    def test_async_edge_weight_equals_median_trace_weight(self, tmp_path):
        G = nx.DiGraph()
        G.add_edge("a", "b", weight=10)
        G.add_edge("b", "c", weight=20)
        G.add_edge("c", "d", weight=30)
        expected_weight = int(statistics.median([10, 20, 30]))

        manifest = write_manifest(tmp_path, [
            {"producer": "order-service", "consumer": "inventory-service", "channel": "kafka"}
        ])
        G = parse_async_deps(manifest, G)
        assert G["order-service"]["inventory-service"]["weight"] == expected_weight

    def test_async_edge_weight_is_one_when_graph_empty(self, tmp_path):
        G = nx.DiGraph()
        manifest = write_manifest(tmp_path, [
            {"producer": "order-service", "consumer": "inventory-service", "channel": "kafka"}
        ])
        G = parse_async_deps(manifest, G)
        assert G["order-service"]["inventory-service"]["weight"] == 1

    def test_all_async_edges_get_same_weight(self, tmp_path):
        G = make_graph([("frontend", "payment-service")], weight=40)
        manifest = write_manifest(tmp_path, [
            {"producer": "a", "consumer": "b", "channel": "kafka"},
            {"producer": "c", "consumer": "d", "channel": "sqs"},
        ])
        G = parse_async_deps(manifest, G)
        assert G["a"]["b"]["weight"] == G["c"]["d"]["weight"]


class TestServiceNameNormalization:

    def test_producer_name_normalized(self, tmp_path):
        G = make_graph([("frontend", "payment-service")])
        manifest = write_manifest(tmp_path, [
            {"producer": "Order-Service-v2-abc123", "consumer": "inventory-service", "channel": "kafka"}
        ])
        G = parse_async_deps(manifest, G)
        assert G.has_edge("order-service", "inventory-service")

    def test_consumer_name_normalized(self, tmp_path):
        G = make_graph([("frontend", "payment-service")])
        manifest = write_manifest(tmp_path, [
            {"producer": "order-service", "consumer": "Inventory-Service-1.2.3", "channel": "kafka"}
        ])
        G = parse_async_deps(manifest, G)
        assert G.has_edge("order-service", "inventory-service")


class TestDuplicateAndConflict:

    def test_sync_edge_not_overwritten_by_async(self, tmp_path):
        G = nx.DiGraph()
        G.add_edge("frontend", "payment-service", weight=100, edge_type="sync")
        manifest = write_manifest(tmp_path, [
            {"producer": "frontend", "consumer": "payment-service", "channel": "kafka"}
        ])
        G = parse_async_deps(manifest, G)
        assert G["frontend"]["payment-service"]["edge_type"] == "sync"
        assert G["frontend"]["payment-service"]["weight"] == 100

    def test_existing_async_edge_not_duplicated(self, tmp_path):
        G = nx.DiGraph()
        G.add_edge("order-service", "inventory-service", weight=20, edge_type="async")
        manifest = write_manifest(tmp_path, [
            {"producer": "order-service", "consumer": "inventory-service", "channel": "kafka"}
        ])
        initial_edge_count = G.number_of_edges()
        G = parse_async_deps(manifest, G)
        assert G.number_of_edges() == initial_edge_count


class TestMalformedManifest:

    def test_missing_producer_skipped(self, tmp_path):
        G = make_graph([("frontend", "payment-service")])
        manifest = write_manifest(tmp_path, [
            {"consumer": "inventory-service", "channel": "kafka"}
        ])
        G = parse_async_deps(manifest, G)
        assert G.number_of_edges() == 1

    def test_missing_consumer_skipped(self, tmp_path):
        G = make_graph([("frontend", "payment-service")])
        manifest = write_manifest(tmp_path, [
            {"producer": "order-service", "channel": "kafka"}
        ])
        G = parse_async_deps(manifest, G)
        assert G.number_of_edges() == 1

    def test_self_loop_skipped(self, tmp_path):
        G = make_graph([("frontend", "payment-service")])
        manifest = write_manifest(tmp_path, [
            {"producer": "order-service", "consumer": "order-service", "channel": "kafka"}
        ])
        G = parse_async_deps(manifest, G)
        assert not G.has_edge("order-service", "order-service")

    def test_empty_manifest_returns_graph_unchanged(self, tmp_path):
        G = make_graph([("frontend", "payment-service")])
        path = tmp_path / "empty.yaml"
        path.write_text(yaml.dump({"dependencies": []}))
        original_edges = set(G.edges())
        G = parse_async_deps(path, G)
        assert set(G.edges()) == original_edges

    def test_missing_dependencies_key_returns_graph_unchanged(self, tmp_path):
        G = make_graph([("frontend", "payment-service")])
        path = tmp_path / "bad.yaml"
        path.write_text(yaml.dump({"something_else": []}))
        original_edges = set(G.edges())
        G = parse_async_deps(path, G)
        assert set(G.edges()) == original_edges

    def test_mixed_valid_and_invalid_entries(self, tmp_path):
        G = make_graph([("frontend", "payment-service")])
        manifest = write_manifest(tmp_path, [
            {"producer": "order-service", "consumer": "inventory-service", "channel": "kafka"},
            {"consumer": "orphan-consumer", "channel": "sqs"},
            {"producer": "payment-service", "consumer": "notification-service", "channel": "sqs"},
        ])
        G = parse_async_deps(manifest, G)
        assert G.has_edge("order-service", "inventory-service")
        assert G.has_edge("payment-service", "notification-service")
        assert G.number_of_edges() == 3