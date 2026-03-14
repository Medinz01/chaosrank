"""
parser/otlp.py — OTel OTLP JSON trace parser

Supports two OTLP JSON envelope shapes:

  Shape A — OTel Collector JSON export (pass 1):
    {
      "resourceSpans": [
        {
          "resource": {"attributes": [{"key": "service.name", ...}]},
          "scopeSpans": [{"spans": [...]}]
        }
      ]
    }

  Shape B — Tempo / Jaeger v2 JSON export (pass 2):
    {
      "batches": [
        {
          "resource": {"attributes": [{"key": "service.name", ...}]},
          "instrumentationLibrarySpans": [{"spans": [...]}]
        }
      ]
    }

Auto-detection: checks root keys at parse time. No --format subtype needed.

Service identity: resource.attributes["service.name"] stringValue.
Falls back to "unknown-service" if absent; emits warning.
Uses ijson streaming for files > 100MB.
"""

from __future__ import annotations

import json
import logging
import os
from collections import defaultdict
from pathlib import Path

from chaosrank.parser.normalize import normalize

logger = logging.getLogger(__name__)

try:
    import ijson  # type: ignore
    _IJSON_AVAILABLE = True
except ImportError:  # pragma: no cover
    _IJSON_AVAILABLE = False

_STREAMING_THRESHOLD_BYTES = 100 * 1024 * 1024  # 100 MB


def parse_otlp(
    path: Path,
    min_call_frequency: int = 10,
) -> dict[tuple[str, str], int]:
    """
    Parse an OTLP JSON trace export and return a weighted edge map.

    Auto-detects envelope shape (Collector JSON vs Tempo/Jaeger v2).

    Returns:
        dict mapping (caller_service, callee_service) -> call_count
        Only edges with call_count >= min_call_frequency are included.
    """
    file_size = os.path.getsize(path)
    if file_size > _STREAMING_THRESHOLD_BYTES and _IJSON_AVAILABLE:
        logger.debug("Large OTLP file (%d bytes) — using streaming parser", file_size)
        return _parse_streaming(path, min_call_frequency)

    return _parse_full(path, min_call_frequency)


# ---------------------------------------------------------------------------
# Full (in-memory) parser
# ---------------------------------------------------------------------------

def _parse_full(
    path: Path,
    min_call_frequency: int,
) -> dict[tuple[str, str], int]:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)

    envelope = _detect_envelope(data, path)

    if envelope == "collector":
        return _extract_collector(data, min_call_frequency)
    else:
        return _extract_tempo(data, min_call_frequency)


def _detect_envelope(data: dict, path: Path) -> str:
    """Return 'collector' for resourceSpans, 'tempo' for batches envelope."""
    if "resourceSpans" in data:
        return "collector"
    if "batches" in data:
        logger.debug("Detected Tempo/Jaeger v2 envelope (batches) in %s", path)
        return "tempo"
    # Neither key found — warn and attempt collector path (may produce empty result)
    logger.warning(
        "OTLP file has neither 'resourceSpans' nor 'batches' root key: %s. "
        "Supported envelopes: OTel Collector JSON (resourceSpans) and "
        "Tempo/Jaeger v2 JSON (batches).",
        path,
    )
    return "collector"


def _extract_collector(
    data: dict,
    min_call_frequency: int,
) -> dict[tuple[str, str], int]:
    """Extract edges from OTel Collector JSON (resourceSpans envelope)."""
    resource_spans = data.get("resourceSpans", [])
    if not resource_spans:
        logger.warning("OTLP Collector JSON has no resourceSpans")
        return {}

    span_service: dict[str, str] = {}
    all_spans: list[tuple[dict, str]] = []

    for resource_span in resource_spans:
        service = _extract_service_name(resource_span)
        for scope_span in resource_span.get("scopeSpans", []):
            for span in scope_span.get("spans", []):
                span_id = span.get("spanId", "")
                if span_id:
                    span_service[span_id] = service
                all_spans.append((span, service))

    return _build_edge_map(all_spans, span_service, min_call_frequency)


def _extract_tempo(
    data: dict,
    min_call_frequency: int,
) -> dict[tuple[str, str], int]:
    """Extract edges from Tempo/Jaeger v2 JSON (batches envelope).

    Tempo uses 'instrumentationLibrarySpans' instead of 'scopeSpans'.
    The resource and span structure is otherwise identical to Collector JSON.
    """
    batches = data.get("batches", [])
    if not batches:
        logger.warning("OTLP Tempo/Jaeger v2 JSON has no batches")
        return {}

    span_service: dict[str, str] = {}
    all_spans: list[tuple[dict, str]] = []

    for batch in batches:
        service = _extract_service_name(batch)
        # Tempo uses instrumentationLibrarySpans; fall back to scopeSpans
        # in case a future version normalises the key name
        scope_groups = (
            batch.get("instrumentationLibrarySpans")
            or batch.get("scopeSpans")
            or []
        )
        for scope_group in scope_groups:
            for span in scope_group.get("spans", []):
                span_id = span.get("spanId", "")
                if span_id:
                    span_service[span_id] = service
                all_spans.append((span, service))

    return _build_edge_map(all_spans, span_service, min_call_frequency)


# ---------------------------------------------------------------------------
# Streaming parser (ijson, files > 100 MB)
# ---------------------------------------------------------------------------

def _parse_streaming(
    path: Path,
    min_call_frequency: int,
) -> dict[tuple[str, str], int]:  # pragma: no cover
    """Streaming parser — peeks at root keys to pick the right path."""
    # Read just enough to detect the envelope without loading the whole file
    with open(path, "rb") as f:
        prefix = f.read(512).decode("utf-8", errors="ignore")

    if '"batches"' in prefix:
        logger.debug("Streaming: detected Tempo/Jaeger v2 envelope")
        return _parse_streaming_tempo(path, min_call_frequency)
    else:
        return _parse_streaming_collector(path, min_call_frequency)


def _parse_streaming_collector(
    path: Path,
    min_call_frequency: int,
) -> dict[tuple[str, str], int]:  # pragma: no cover
    span_service: dict[str, str] = {}
    all_spans: list[tuple[dict, str]] = []

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


def _parse_streaming_tempo(
    path: Path,
    min_call_frequency: int,
) -> dict[tuple[str, str], int]:  # pragma: no cover
    span_service: dict[str, str] = {}
    all_spans: list[tuple[dict, str]] = []

    with open(path, "rb") as f:
        for batch in ijson.items(f, "batches.item"):
            service = _extract_service_name(batch)
            scope_groups = (
                batch.get("instrumentationLibrarySpans")
                or batch.get("scopeSpans")
                or []
            )
            for scope_group in scope_groups:
                for span in scope_group.get("spans", []):
                    span_id = span.get("spanId", "")
                    if span_id:
                        span_service[span_id] = service
                    all_spans.append((span, service))

    return _build_edge_map(all_spans, span_service, min_call_frequency)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

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
            dropped, total, min_call_frequency,
        )

    return filtered


def _extract_service_name(resource_span: dict) -> str:
    """Extract and normalize service.name from a resource span or batch."""
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