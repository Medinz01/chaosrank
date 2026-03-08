"""Kafka adapter for ChaosRank.

Converts a Kafka topic export JSON file into async-deps.yaml format.

Expected input schema:
    {
      "topics": [
        {
          "name":      "order-placed",       # required — topic name
          "producer":  "order-service",      # required — service that publishes to this topic
          "consumers": [                     # required — services that consume from this topic
            "inventory-service",
            "notification-service"
          ]
        },
        ...
      ]
    }

How to produce this file:
    Option A — from your Kafka UI (AKHQ, Kafdrop, Kafka UI):
      Export topic list, add producer/consumer fields from your service registry.

    Option B — from kafka-python (one-off script):
      from kafka.admin import KafkaAdminClient
      client = KafkaAdminClient(bootstrap_servers="localhost:9092")
      topics = client.list_topics()
      # Add producer/consumer knowledge from your team's runbook or code

    Option C — manually:
      List your topics from: kafka-topics.sh --list --bootstrap-server <host>
      Fill in producers and consumers from your service documentation.

Output: list of dependency dicts compatible with async-deps.yaml schema.
"""
import json
import logging
from pathlib import Path

from chaosrank.adapters.base import AsyncDepsAdapter

logger = logging.getLogger(__name__)


class KafkaAdapter(AsyncDepsAdapter):

    def source_format(self) -> str:
        return "kafka"

    def convert(self, input_path: Path) -> list[dict]:
        """Parse Kafka topic export JSON and return dependency dicts."""
        if input_path.is_dir():
            raise ValueError(
                "--input must be a file for --from kafka. "
                "Pass the path to your kafka-topics.json export."
            )

        try:
            raw = input_path.read_text(encoding="utf-8")
            data = json.loads(raw)
        except json.JSONDecodeError as e:
            raise ValueError(f"Failed to parse JSON: {e}") from e
        except Exception as e:
            raise ValueError(f"Failed to read {input_path}: {e}") from e

        if not isinstance(data, dict):
            raise ValueError(
                "Expected a JSON object with a 'topics' key. "
                "Got a bare list — wrap it: {\"topics\": [...]}"
            )

        topics = data.get("topics")
        if topics is None:
            raise ValueError("Missing required key 'topics' in JSON input.")
        if not isinstance(topics, list):
            raise ValueError("'topics' must be a list.")

        if not topics:
            logger.warning("'topics' list is empty — no dependencies to extract.")
            return []

        return _build_dependencies(topics)


def _build_dependencies(topics: list) -> list[dict]:
    deps = []

    for i, entry in enumerate(topics):
        if not isinstance(entry, dict):
            logger.warning("Skipping topics[%d] — not an object: %r", i, entry)
            continue

        topic_name = entry.get("name")
        producer   = entry.get("producer")
        consumers  = entry.get("consumers", [])

        # Validate required fields
        if not topic_name:
            logger.warning("Skipping topics[%d] — missing required field 'name'.", i)
            continue
        if not producer:
            logger.warning(
                "Skipping topic %r — missing required field 'producer'. "
                "Add the service name that publishes to this topic.",
                topic_name,
            )
            continue
        if not isinstance(consumers, list):
            logger.warning(
                "Skipping topic %r — 'consumers' must be a list, got %r.",
                topic_name, type(consumers).__name__,
            )
            continue
        if not consumers:
            logger.warning(
                "Topic %r has producer %r but no consumers — skipping. "
                "Add consuming services or remove the topic from the export.",
                topic_name, producer,
            )
            continue

        for consumer in consumers:
            if not isinstance(consumer, str) or not consumer.strip():
                logger.warning(
                    "Skipping malformed consumer entry in topic %r: %r",
                    topic_name, consumer,
                )
                continue

            consumer = consumer.strip()

            if consumer == producer:
                logger.warning(
                    "Skipping self-referential dependency: %r → %r via topic %r",
                    producer, consumer, topic_name,
                )
                continue

            deps.append({
                "producer": producer,
                "consumer": consumer,
                "channel":  "kafka",
                "topic":    topic_name,
            })

    logger.debug("Extracted %d dependencies from Kafka topic export", len(deps))
    return deps