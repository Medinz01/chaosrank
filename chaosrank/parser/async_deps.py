import logging
import statistics
from pathlib import Path

import networkx as nx
import yaml

from chaosrank.parser.normalize import normalize

logger = logging.getLogger(__name__)


def parse_async_deps(path: Path, G: nx.DiGraph) -> nx.DiGraph:
    """Parse an async dependency manifest and merge edges into G.

    Async edges are assigned weight equal to median(trace_edge_weights).
    Edge type is annotated as 'async' for downstream consumers.
    """
    with open(path, "r", encoding="utf-8") as f:
        manifest = yaml.safe_load(f) or {}

    dependencies = manifest.get("dependencies", [])
    if not dependencies:
        logger.warning("Async deps manifest is empty or missing 'dependencies' key: %s", path)
        return G

    trace_weights = [data.get("weight", 1) for _, _, data in G.edges(data=True)]
    async_weight = int(statistics.median(trace_weights)) if trace_weights else 1

    logger.debug("Async edge weight set to median trace weight: %d", async_weight)

    added = 0
    skipped = 0

    for entry_num, entry in enumerate(dependencies, start=1):
        raw_producer = entry.get("producer", "")
        raw_consumer = entry.get("consumer", "")

        producer = normalize(raw_producer)
        consumer = normalize(raw_consumer)

        if not producer or not consumer:
            logger.warning(
                "Entry %d: missing or invalid producer/consumer — skipping (producer=%r, consumer=%r)",
                entry_num, raw_producer, raw_consumer,
            )
            skipped += 1
            continue

        if producer == consumer:
            logger.warning("Entry %d: producer and consumer are the same service '%s' — skipping", entry_num, producer)
            skipped += 1
            continue

        channel = entry.get("channel", "unknown")
        topic   = entry.get("topic") or entry.get("queue") or entry.get("subject", "unknown")

        if G.has_edge(producer, consumer):
            existing_type = G[producer][consumer].get("edge_type", "sync")
            if existing_type == "sync":
                logger.debug(
                    "Async edge %s -> %s already exists as sync trace edge — skipping to preserve trace data",
                    producer, consumer,
                )
                skipped += 1
                continue

        G.add_node(producer)
        G.add_node(consumer)
        G.add_edge(
            producer,
            consumer,
            weight=async_weight,
            edge_type="async",
            channel=channel,
            topic=topic,
        )
        added += 1
        logger.debug("Added async edge: %s -> %s (channel=%s, topic=%s)", producer, consumer, channel, topic)

    if skipped:
        logger.warning("Skipped %d async dep entries (malformed or duplicate)", skipped)

    logger.info("Merged %d async edges into dependency graph (weight=%d)", added, async_weight)
    return G