import json
import logging
from collections import defaultdict
from pathlib import Path

import yaml

from chaosrank.adapters.base import AsyncDepsAdapter

logger = logging.getLogger(__name__)

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
        spec_files = _collect_spec_files(input_path)
        if not spec_files:
            raise ValueError(f"No AsyncAPI spec files found in {input_path}")

        logger.debug("Found %d spec file(s): %s", len(spec_files), spec_files)

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

        if "publish" in channel_def:
            channel_map[topic]["producers"].append(service_name)
            channel_map[topic]["meta"].setdefault("channel", channel_type)
            channel_map[topic]["meta"].setdefault("topic", topic)

        if "subscribe" in channel_def:
            channel_map[topic]["consumers"].append(service_name)
            channel_map[topic]["meta"].setdefault("channel", channel_type)
            channel_map[topic]["meta"].setdefault("topic", topic)


def _extract_service_name(spec: dict, path: Path) -> str | None:
    info = spec.get("info", {})
    if isinstance(info, dict) and info.get("title"):
        return str(info["title"])
    return path.stem or None


def _collect_bindings(channel_def: dict) -> dict:
    merged: dict = {}

    channel_bindings = channel_def.get("bindings", {})
    if isinstance(channel_bindings, dict):
        merged.update(channel_bindings)

    for operation in ("publish", "subscribe"):
        op = channel_def.get(operation, {})
        if not isinstance(op, dict):
            continue
        op_bindings = op.get("bindings", {})
        if isinstance(op_bindings, dict):
            merged.update(op_bindings)

    return merged


def _extract_topic(channel_key: str, channel_def: dict) -> str:
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