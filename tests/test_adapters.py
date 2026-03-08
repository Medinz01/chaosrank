import json
from pathlib import Path

import pytest
import yaml

from chaosrank.adapters.asyncapi import AsyncAPIAdapter
from chaosrank.adapters.kafka import KafkaAdapter


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def write_yaml(path: Path, data: dict) -> Path:
    path.write_text(yaml.dump(data, default_flow_style=False))
    return path


def write_json(path: Path, data) -> Path:
    path.write_text(json.dumps(data, indent=2))
    return path


def make_asyncapi_spec(
    title: str,
    channels: dict,
    version: str = "2.6.0",
) -> dict:
    return {
        "asyncapi": version,
        "info": {"title": title, "version": "1.0.0"},
        "channels": channels,
    }


def kafka_export(*topics: dict) -> dict:
    return {"topics": list(topics)}


def kafka_topic(name: str, producer: str, consumers: list[str]) -> dict:
    return {"name": name, "producer": producer, "consumers": consumers}


# ---------------------------------------------------------------------------
# AsyncAPI adapter — edge extraction
# ---------------------------------------------------------------------------

class TestAsyncAPIEdgeExtraction:

    def test_publish_makes_producer(self, tmp_path):
        spec = make_asyncapi_spec(
            title="order-service",
            channels={"order/placed": {"publish": {}}},
        )
        write_yaml(tmp_path / "order.yaml", spec)

        spec2 = make_asyncapi_spec(
            title="inventory-service",
            channels={"order/placed": {"subscribe": {}}},
        )
        write_yaml(tmp_path / "inventory.yaml", spec2)

        deps = AsyncAPIAdapter().convert(tmp_path)
        assert any(
            d["producer"] == "order-service" and d["consumer"] == "inventory-service"
            for d in deps
        )

    def test_subscribe_makes_consumer(self, tmp_path):
        write_yaml(tmp_path / "order.yaml", make_asyncapi_spec(
            title="order-service",
            channels={"order/placed": {"publish": {}}},
        ))
        write_yaml(tmp_path / "basket.yaml", make_asyncapi_spec(
            title="basket-service",
            channels={"order/placed": {"subscribe": {}}},
        ))

        deps = AsyncAPIAdapter().convert(tmp_path)
        assert any(d["consumer"] == "basket-service" for d in deps)

    def test_one_producer_many_consumers(self, tmp_path):
        write_yaml(tmp_path / "order.yaml", make_asyncapi_spec(
            title="order-service",
            channels={"order/placed": {"publish": {}}},
        ))
        for svc in ["inventory-service", "notification-service", "audit-service"]:
            write_yaml(tmp_path / f"{svc}.yaml", make_asyncapi_spec(
                title=svc,
                channels={"order/placed": {"subscribe": {}}},
            ))

        deps = AsyncAPIAdapter().convert(tmp_path)
        consumers = {d["consumer"] for d in deps if d["producer"] == "order-service"}
        assert consumers == {"inventory-service", "notification-service", "audit-service"}

    def test_multiple_channels_across_specs(self, tmp_path):
        write_yaml(tmp_path / "order.yaml", make_asyncapi_spec(
            title="order-service",
            channels={
                "order/placed": {"publish": {}},
                "basket/checkout": {"subscribe": {}},
            },
        ))
        write_yaml(tmp_path / "basket.yaml", make_asyncapi_spec(
            title="basket-service",
            channels={
                "basket/checkout": {"publish": {}},
                "order/placed": {"subscribe": {}},
            },
        ))

        deps = AsyncAPIAdapter().convert(tmp_path)
        pairs = {(d["producer"], d["consumer"]) for d in deps}
        assert ("order-service", "basket-service") in pairs
        assert ("basket-service", "order-service") in pairs

    def test_self_loop_skipped(self, tmp_path):
        write_yaml(tmp_path / "order.yaml", make_asyncapi_spec(
            title="order-service",
            channels={
                "order/placed": {"publish": {}, "subscribe": {}},
            },
        ))

        deps = AsyncAPIAdapter().convert(tmp_path)
        assert not any(d["producer"] == d["consumer"] for d in deps)

    def test_single_file_with_both_publish_and_subscribe(self, tmp_path):
        spec = {
            "asyncapi": "2.6.0",
            "info": {"title": "multi-service-spec", "version": "1.0.0"},
            "channels": {
                "order/placed": {
                    "publish":   {"x-service": "order-service"},
                    "subscribe": {"x-service": "inventory-service"},
                },
            },
        }
        f = tmp_path / "multi.yaml"
        write_yaml(f, spec)

        # Single file accepted without error
        deps = AsyncAPIAdapter().convert(f)
        assert isinstance(deps, list)


class TestAsyncAPITopicExtraction:

    def test_explicit_kafka_topic_binding_used(self, tmp_path):
        spec = make_asyncapi_spec(
            title="order-service",
            channels={
                "orders/v1/placed": {
                    "publish": {
                        "bindings": {
                            "kafka": {"topic": "order-placed-v1"}
                        }
                    }
                }
            },
        )
        write_yaml(tmp_path / "order.yaml", spec)
        write_yaml(tmp_path / "inventory.yaml", make_asyncapi_spec(
            title="inventory-service",
            channels={
                "orders/v1/placed": {
                    "subscribe": {
                        "bindings": {
                            "kafka": {"topic": "order-placed-v1"}
                        }
                    }
                }
            },
        ))

        deps = AsyncAPIAdapter().convert(tmp_path)
        assert any(d["topic"] == "order-placed-v1" for d in deps)

    def test_channel_key_used_when_no_binding(self, tmp_path):
        write_yaml(tmp_path / "order.yaml", make_asyncapi_spec(
            title="order-service",
            channels={"order-placed": {"publish": {}}},
        ))
        write_yaml(tmp_path / "inventory.yaml", make_asyncapi_spec(
            title="inventory-service",
            channels={"order-placed": {"subscribe": {}}},
        ))

        deps = AsyncAPIAdapter().convert(tmp_path)
        assert any(d["topic"] == "order-placed" for d in deps)

    def test_amqp_binding_sets_channel_type_rabbitmq(self, tmp_path):
        write_yaml(tmp_path / "order.yaml", make_asyncapi_spec(
            title="order-service",
            channels={
                "order-placed": {
                    "publish": {"bindings": {"amqp": {"queue": "order-placed-queue"}}}
                }
            },
        ))
        write_yaml(tmp_path / "inventory.yaml", make_asyncapi_spec(
            title="inventory-service",
            channels={
                "order-placed": {
                    "subscribe": {"bindings": {"amqp": {"queue": "order-placed-queue"}}}
                }
            },
        ))

        deps = AsyncAPIAdapter().convert(tmp_path)
        assert any(d["channel"] == "rabbitmq" for d in deps)

    def test_kafka_binding_sets_channel_type_kafka(self, tmp_path):
        write_yaml(tmp_path / "order.yaml", make_asyncapi_spec(
            title="order-service",
            channels={
                "order-placed": {
                    "publish": {"bindings": {"kafka": {"topic": "order-placed"}}}
                }
            },
        ))
        write_yaml(tmp_path / "inventory.yaml", make_asyncapi_spec(
            title="inventory-service",
            channels={
                "order-placed": {
                    "subscribe": {"bindings": {"kafka": {"topic": "order-placed"}}}
                }
            },
        ))

        deps = AsyncAPIAdapter().convert(tmp_path)
        assert any(d["channel"] == "kafka" for d in deps)


class TestAsyncAPIServiceNameExtraction:

    def test_info_title_used_as_service_name(self, tmp_path):
        write_yaml(tmp_path / "order.yaml", make_asyncapi_spec(
            title="Order Service",
            channels={"order/placed": {"publish": {}}},
        ))
        write_yaml(tmp_path / "inventory.yaml", make_asyncapi_spec(
            title="Inventory Service",
            channels={"order/placed": {"subscribe": {}}},
        ))

        deps = AsyncAPIAdapter().convert(tmp_path)
        assert any(d["producer"] == "Order Service" for d in deps)

    def test_filename_used_when_no_title(self, tmp_path):
        spec = {
            "asyncapi": "2.6.0",
            "channels": {"order/placed": {"publish": {}}},
        }
        write_yaml(tmp_path / "order-service.yaml", spec)
        write_yaml(tmp_path / "inventory.yaml", make_asyncapi_spec(
            title="inventory-service",
            channels={"order/placed": {"subscribe": {}}},
        ))

        deps = AsyncAPIAdapter().convert(tmp_path)
        assert any(d["producer"] == "order-service" for d in deps)


class TestAsyncAPIMalformedInput:

    def test_empty_directory_raises(self, tmp_path):
        with pytest.raises(ValueError, match="No AsyncAPI spec files found"):
            AsyncAPIAdapter().convert(tmp_path)

    def test_non_2x_version_skipped(self, tmp_path):
        spec = make_asyncapi_spec(
            title="order-service",
            channels={"order/placed": {"publish": {}}},
            version="3.0.0",
        )
        write_yaml(tmp_path / "order.yaml", spec)
        # No consumer — would warn and return empty regardless,
        # but version check fires first
        deps = AsyncAPIAdapter().convert(tmp_path)
        assert deps == []

    def test_unparseable_file_skipped(self, tmp_path):
        (tmp_path / "broken.yaml").write_text("{{{{ not valid yaml :")
        write_yaml(tmp_path / "order.yaml", make_asyncapi_spec(
            title="order-service",
            channels={"order/placed": {"publish": {}}},
        ))
        write_yaml(tmp_path / "inventory.yaml", make_asyncapi_spec(
            title="inventory-service",
            channels={"order/placed": {"subscribe": {}}},
        ))

        # Should not raise — broken file is skipped, valid files processed
        deps = AsyncAPIAdapter().convert(tmp_path)
        assert len(deps) == 1

    def test_channel_with_producer_only_skipped(self, tmp_path):
        write_yaml(tmp_path / "order.yaml", make_asyncapi_spec(
            title="order-service",
            channels={"order/placed": {"publish": {}}},
        ))
        deps = AsyncAPIAdapter().convert(tmp_path)
        assert deps == []

    def test_channel_with_consumer_only_skipped(self, tmp_path):
        write_yaml(tmp_path / "inventory.yaml", make_asyncapi_spec(
            title="inventory-service",
            channels={"order/placed": {"subscribe": {}}},
        ))
        deps = AsyncAPIAdapter().convert(tmp_path)
        assert deps == []

    def test_json_spec_file_accepted(self, tmp_path):
        spec = make_asyncapi_spec(
            title="order-service",
            channels={"order/placed": {"publish": {}}},
        )
        write_json(tmp_path / "order.json", spec)
        write_yaml(tmp_path / "inventory.yaml", make_asyncapi_spec(
            title="inventory-service",
            channels={"order/placed": {"subscribe": {}}},
        ))

        deps = AsyncAPIAdapter().convert(tmp_path)
        assert len(deps) == 1


# ---------------------------------------------------------------------------
# Kafka adapter — edge extraction
# ---------------------------------------------------------------------------

class TestKafkaEdgeExtraction:

    def test_single_topic_single_consumer(self, tmp_path):
        f = write_json(tmp_path / "kafka.json", kafka_export(
            kafka_topic("order-placed", "order-service", ["inventory-service"])
        ))
        deps = KafkaAdapter().convert(f)
        assert len(deps) == 1
        assert deps[0]["producer"] == "order-service"
        assert deps[0]["consumer"] == "inventory-service"
        assert deps[0]["topic"]    == "order-placed"
        assert deps[0]["channel"]  == "kafka"

    def test_single_topic_multiple_consumers(self, tmp_path):
        f = write_json(tmp_path / "kafka.json", kafka_export(
            kafka_topic("order-placed", "order-service",
                        ["inventory-service", "notification-service", "audit-service"])
        ))
        deps = KafkaAdapter().convert(f)
        assert len(deps) == 3
        consumers = {d["consumer"] for d in deps}
        assert consumers == {"inventory-service", "notification-service", "audit-service"}

    def test_multiple_topics(self, tmp_path):
        f = write_json(tmp_path / "kafka.json", kafka_export(
            kafka_topic("order-placed",   "order-service",   ["inventory-service"]),
            kafka_topic("payment-events", "payment-service", ["notification-service", "fraud-service"]),
        ))
        deps = KafkaAdapter().convert(f)
        assert len(deps) == 3
        producers = {d["producer"] for d in deps}
        assert producers == {"order-service", "payment-service"}

    def test_all_deps_have_channel_kafka(self, tmp_path):
        f = write_json(tmp_path / "kafka.json", kafka_export(
            kafka_topic("order-placed", "order-service", ["inventory-service"]),
            kafka_topic("payment-events", "payment-service", ["notification-service"]),
        ))
        deps = KafkaAdapter().convert(f)
        assert all(d["channel"] == "kafka" for d in deps)

    def test_self_referential_consumer_skipped(self, tmp_path):
        f = write_json(tmp_path / "kafka.json", kafka_export(
            kafka_topic("order-placed", "order-service", ["order-service", "inventory-service"])
        ))
        deps = KafkaAdapter().convert(f)
        assert not any(d["producer"] == d["consumer"] for d in deps)
        assert len(deps) == 1


class TestKafkaMalformedInput:

    def test_missing_topics_key_raises(self, tmp_path):
        f = write_json(tmp_path / "kafka.json", {"something_else": []})
        with pytest.raises(ValueError, match="Missing required key 'topics'"):
            KafkaAdapter().convert(f)

    def test_bare_list_raises(self, tmp_path):
        f = write_json(tmp_path / "kafka.json", [
            {"name": "order-placed", "producer": "order-service", "consumers": ["inventory-service"]}
        ])
        with pytest.raises(ValueError, match="wrap it"):
            KafkaAdapter().convert(f)

    def test_invalid_json_raises(self, tmp_path):
        f = tmp_path / "kafka.json"
        f.write_text("{ not valid json }")
        with pytest.raises(ValueError, match="Failed to parse JSON"):
            KafkaAdapter().convert(f)

    def test_directory_input_raises(self, tmp_path):
        with pytest.raises(ValueError, match="must be a file"):
            KafkaAdapter().convert(tmp_path)

    def test_missing_producer_skipped(self, tmp_path):
        f = write_json(tmp_path / "kafka.json", {"topics": [
            {"name": "order-placed", "consumers": ["inventory-service"]},
            {"name": "payment-events", "producer": "payment-service", "consumers": ["notification-service"]},
        ]})
        deps = KafkaAdapter().convert(f)
        assert len(deps) == 1
        assert deps[0]["topic"] == "payment-events"

    def test_missing_name_skipped(self, tmp_path):
        f = write_json(tmp_path / "kafka.json", {"topics": [
            {"producer": "order-service", "consumers": ["inventory-service"]},
            {"name": "payment-events", "producer": "payment-service", "consumers": ["notification-service"]},
        ]})
        deps = KafkaAdapter().convert(f)
        assert len(deps) == 1

    def test_empty_consumers_skipped(self, tmp_path):
        f = write_json(tmp_path / "kafka.json", kafka_export(
            kafka_topic("order-placed", "order-service", []),
        ))
        deps = KafkaAdapter().convert(f)
        assert deps == []

    def test_empty_topics_list_returns_empty(self, tmp_path):
        f = write_json(tmp_path / "kafka.json", {"topics": []})
        deps = KafkaAdapter().convert(f)
        assert deps == []

    def test_mixed_valid_and_invalid_topics(self, tmp_path):
        f = write_json(tmp_path / "kafka.json", {"topics": [
            {"name": "order-placed", "producer": "order-service", "consumers": ["inventory-service"]},
            {"consumers": ["orphan-consumer"]},
            {"name": "payment-events", "producer": "payment-service", "consumers": ["notification-service"]},
        ]})
        deps = KafkaAdapter().convert(f)
        assert len(deps) == 2
