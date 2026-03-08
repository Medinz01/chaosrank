"""AsyncAPI 2.x adapter for ChaosRank.

Converts a directory of single-service AsyncAPI 2.x specs (or a single
multi-service spec) into async-deps.yaml format.

Extraction rules:
  info.title                 → service name
  channel with publish:      → this service PRODUCES to this channel
  channel with subscribe:    → this service CONSUMES from this channel
  bindings.kafka.topic       → explicit topic name (overrides channel key)
  bindings.amqp.queue        → queue name for RabbitMQ/AMQP channels

Bindings are checked at both channel level and operation level (publish/subscribe).
Operation-level bindings take precedence over channel-level bindings.

For single-service specs, a full producer→consumer mapping requires all
specs together. This adapter builds a channel map across all files in the
input directory, then cross-references to emit (producer, consumer) pairs.

Channels with only a producer or only a consumer are emitted with a warning
— the missing side is unknown from the available specs.
"""
import json
import logging
from collections import defaultdict
from pathlib import Path

import yaml

from chaosrank.adapters.base import AsyncDepsAdapter

logger = logging.getLogger(__name__)

# Channels with these bindings map to these channel types
_BINDING_TO_CHANNEL = {
    "kafka":   "kafka",
    "amqp":    "rabbitmq",
    "sqs":     "sqs",
    "sns":     "sns",
    "nats":    "nats",
    "mqtt":    "mqtt",
}

_YAML_EXTENSIONS = {".yaml", ".yml", ".json"}


class AsyncAPIAdapter(AsyncDepsAdapter):

    def source_format(self) -> str:
        return "asyncapi"

    def convert(self, input_path: Path) -> list[dict]:
        """Parse AsyncAPI 2.x spec(s) and return dependency dicts.

        Accepts either:
          - A directory: walks all .yaml/.yml/.json files, treats each as
            a single-service spec, cross-references channels across files.
          - A single file: parses as-is (works for multi-service specs or
            single-service specs where both publish and subscribe appear).
        """
        spec_files = _collect_spec_files(input_path)
        if not spec_files:
            raise ValueError(f"No AsyncAPI spec files found in {input_path}")

        logger.debug("Found %d spec file(s): %s", len(spec_files), spec_files)

        # channel_map[channel_name] = {"producers": [...], "consumers": [...], "meta": {...}}
        channel_map: dict[str, dict] = defaultdict(lambda: {"producers": [], "consumers": [], "meta": {}})

        for spec_file in spec_files:
            _parse_spec_file(spec_file, channel_map)

        return _build_dependencies(channel_map)


def _collect_spec_files(input_path: Path) -> list[Path]:
    if input_path.is_file():
        return [input_path]
    return sorted(
        p for p in input_path.rglob("*")
        if p.is_file() and p.suffix.lower() in _YAML_EXTENSIONS
    )


def _parse_spec_file(path: Path, channel_map: dict) -> None:
    try:
        raw = path.read_text(encoding="utf-8")
        spec = yaml.safe_load(raw) if path.suffix.lower() in {".yaml", ".yml"} else json.loads(raw)
    except Exception as e:
        logger.warning("Skipping %s — failed to parse: %s", path, e)
        return

    if not isinstance(spec, dict):
        logger.warning("Skipping %s — not a valid YAML/JSON object", path)
        return

    version = str(spec.get("asyncapi", ""))
    if not version.startswith("2."):
        logger.warning("Skipping %s — asyncapi version %r is not 2.x", path, version)
        return

    service_name = _extract_service_name(spec, path)
    if not service_name:
        logger.warning("Skipping %s — could not determine service name (no info.title)", path)
        return

    channels = spec.get("channels", {})
    if not isinstance(channels, dict):
        logger.warning("Skipping %s — channels is not a mapping", path)
        return

    for channel_key, channel_def in channels.items():
        if not isinstance(channel_def, dict):
            continue

        topic = _extract_topic(channel_key, channel_def)
        channel_type = _extract_channel_type(channel_def)

        # publish: this service PRODUCES to the channel
        if "publish" in channel_def:
            channel_map[topic]["producers"].append(service_name)
            channel_map[topic]["meta"].setdefault("channel", channel_type)
            channel_map[topic]["meta"].setdefault("topic", topic)

        # subscribe: this service CONSUMES from the channel
        if "subscribe" in channel_def:
            channel_map[topic]["consumers"].append(service_name)
            channel_map[topic]["meta"].setdefault("channel", channel_type)
            channel_map[topic]["meta"].setdefault("topic", topic)


def _extract_service_name(spec: dict, path: Path) -> str | None:
    info = spec.get("info", {})
    if isinstance(info, dict) and info.get("title"):
        return str(info["title"])
    # Fall back to filename without extension
    return path.stem or None


def _collect_bindings(channel_def: dict) -> dict:
    """Collect bindings from channel level and operation level (publish/subscribe).

    AsyncAPI 2.x allows bindings at both the channel level:
      channels:
        order/placed:
          bindings: { kafka: { topic: ... } }   <- channel-level

    and the operation level:
      channels:
        order/placed:
          publish:
            bindings: { kafka: { topic: ... } } <- operation-level

    Both are valid. We check channel-level first, then merge in operation-level
    bindings so that operation-level values take precedence.
    """
    merged: dict = {}

    # Channel-level bindings
    channel_bindings = channel_def.get("bindings", {})
    if isinstance(channel_bindings, dict):
        merged.update(channel_bindings)

    # Operation-level bindings (publish / subscribe)
    for operation in ("publish", "subscribe"):
        op = channel_def.get(operation, {})
        if not isinstance(op, dict):
            continue
        op_bindings = op.get("bindings", {})
        if isinstance(op_bindings, dict):
            merged.update(op_bindings)

    return merged


def _extract_topic(channel_key: str, channel_def: dict) -> str:
    """Return explicit topic/queue name from bindings, or fall back to channel key."""
    bindings = _collect_bindings(channel_def)

    for binding_name in _BINDING_TO_CHANNEL:
        binding = bindings.get(binding_name, {})
        if not isinstance(binding, dict):
            continue
        for field in ("topic", "queue", "subject", "channel"):
            if binding.get(field):
                return str(binding[field])

    return channel_key


def _extract_channel_type(channel_def: dict) -> str:
    """Infer channel type from bindings, default to 'unknown'."""
    bindings = _collect_bindings(channel_def)

    for binding_name, channel_type in _BINDING_TO_CHANNEL.items():
        if binding_name in bindings:
            return channel_type

    return "unknown"


def _build_dependencies(channel_map: dict) -> list[dict]:
    deps = []

    for topic, data in channel_map.items():
        producers = data["producers"]
        consumers = data["consumers"]
        meta      = data["meta"]

        channel_type = meta.get("channel", "unknown")

        if not producers:
            logger.warning(
                "Channel %r has consumer(s) %s but no known producer — skipping. "
                "Add the producer's spec to the input directory.",
                topic, consumers,
            )
            continue

        if not consumers:
            logger.warning(
                "Channel %r has producer(s) %s but no known consumer — skipping. "
                "Add the consumer's spec to the input directory.",
                topic, producers,
            )
            continue

        for producer in producers:
            for consumer in consumers:
                if producer == consumer:
                    logger.warning(
                        "Skipping self-referential dependency: %r -> %r via %r",
                        producer, consumer, topic,
                    )
                    continue
                deps.append({
                    "producer": producer,
                    "consumer": consumer,
                    "channel":  channel_type,
                    "topic":    topic,
                })

    logger.debug("Extracted %d dependencies from AsyncAPI specs", len(deps))
    return deps