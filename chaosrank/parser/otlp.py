from __future__ import annotations

import json
import logging
import os
from collections import defaultdict
from pathlib import Path

from chaosrank.parser.normalize import normalize

logger = logging.getLogger(__name__)

try:
    import ijson

    _IJSON_AVAILABLE = True
except ImportError:
    _IJSON_AVAILABLE = False

_STREAMING_THRESHOLD_BYTES = 100 * 1024 * 1024


def parse_otlp(
    path: Path,
    min_call_frequency: int = 10,
) -> dict[tuple[str, str], int]:
    file_size = os.path.getsize(path)
    if file_size > _STREAMING_THRESHOLD_BYTES and _IJSON_AVAILABLE:
        logger.debug("Large OTLP file (%d bytes) — using streaming parser", file_size)
        return _parse_streaming(path, min_call_frequency)

    return _parse_full(path, min_call_frequency)


def _parse_full(
    path: Path,
    min_call_frequency: int,
) -> dict[tuple[str, str], int]:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)

    resource_spans = data.get("resourceSpans", [])
    if not resource_spans:
        logger.warning("OTLP file has no resourceSpans: %s", path)
        return {}

    span_service: dict[str, str] = {}
    all_spans: list[dict] = []

    for resource_span in resource_spans:
        service = _extract_service_name(resource_span)
        for scope_span in resource_span.get("scopeSpans", []):
            for span in scope_span.get("spans", []):
                span_id = span.get("spanId", "")
                if span_id:
                    span_service[span_id] = service
                all_spans.append((span, service))

    return _build_edge_map(all_spans, span_service, min_call_frequency)


def _parse_streaming(
    path: Path,
    min_call_frequency: int,
) -> dict[tuple[str, str], int]:
    span_service: dict[str, str] = {}
    all_spans: list[dict] = []

    with open(path, "rb") as f:
        for resource_span in ijson.items(f, "resourceSpans.item"):
            service = _extract_service_name(resource_span)
            for scope_span in resource_span.get("scopeSpans", []):
                for span in scope_span.get("spans", []):
                    span_id = span.get("spanId", "")
                    if span_id:
                        span_service[span_id] = service
                    all_spans.append((span, service))

    return _build_edge_map(all_spans, span_service, min_call_frequency)


def _build_edge_map(
    all_spans: list[tuple[dict, str]],
    span_service: dict[str, str],
    min_call_frequency: int,
) -> dict[tuple[str, str], int]:
    edges: dict[tuple[str, str], int] = defaultdict(int)

    for span, child_service in all_spans:
        parent_id = span.get("parentSpanId", "")
        if not parent_id:
            continue

        parent_service = span_service.get(parent_id)
        if parent_service is None:
            continue

        if parent_service == child_service:
            continue

        edges[(parent_service, child_service)] += 1

    filtered = {
        edge: count
        for edge, count in edges.items()
        if count >= min_call_frequency
    }

    total = len(edges)
    kept = len(filtered)
    dropped = total - kept
    if dropped:
        logger.debug(
            "Filtered %d/%d edges below min_call_frequency=%d",
            dropped,
            total,
            min_call_frequency,
        )

    return filtered


def _extract_service_name(
    resource_span: dict,
) -> str:
    attributes = resource_span.get("resource", {}).get("attributes", [])
    raw_name = None

    for attr in attributes:
        if attr.get("key") == "service.name":
            value = attr.get("value", {})
            raw_name = (
                value.get("stringValue")
                or value.get("intValue")
                or value.get("doubleValue")
                or value.get("boolValue")
            )
            if raw_name is not None:
                raw_name = str(raw_name)
            break

    if raw_name is None:
        logger.warning(
            "resourceSpan missing service.name attribute — using 'unknown-service'. "
            "Ensure your OTel SDK sets resource.attributes['service.name']."
        )
        return "unknown-service"

    return normalize(raw_name)