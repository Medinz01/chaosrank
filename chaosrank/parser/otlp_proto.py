"""Binary OTLP protobuf trace parser.
Handles resource mapping and call graph construction from binary-encoded
OpenTelemetry exports. Requires the 'protobuf' extra.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

from chaosrank.parser.normalize import normalize
from chaosrank.parser.otlp import _build_edge_map

logger = logging.getLogger(__name__)

_STREAMING_THRESHOLD_BYTES = 100 * 1024 * 1024  # 100 MB

# Magic bytes that indicate JSON content.
# Used for secondary format mismatch check only — never blocks parsing.
_JSON_START_BYTES = (b"{", b"[", b" ", b"\t", b"\n", b"\r")


def _import_proto():
    """Import opentelemetry-proto bindings, raising ImportError with install hint."""
    try:
        from opentelemetry.proto.trace.v1 import trace_pb2  # type: ignore
        return trace_pb2
    except ImportError as exc:
        raise ImportError(
            "OTel protobuf support requires the 'protobuf' extra:\n"
            "    pip install chaosrank-cli[protobuf]\n"
            "or:\n"
            "    pip install opentelemetry-proto\n"
        ) from exc

def parse_otlp_proto(
    path: Path,
    min_call_frequency: int = 10,
) -> dict[tuple[str, str], int]:
    """Parse a binary OTLP protobuf trace export and return a weighted edge map.

    Args:
        path:                Path to the .pb / .binpb trace file.
        min_call_frequency:  Drop edges with fewer calls than this. Default 10.

    Returns:
        dict mapping (caller_service, callee_service) -> call_count.
        Only edges with call_count >= min_call_frequency are included.

    Raises:
        ImportError:  If opentelemetry-proto is not installed.
        FileNotFoundError: If path does not exist.
        ValueError:   If the file cannot be decoded as OTLP protobuf.
    """
    trace_pb2 = _import_proto()

    _check_format_mismatch(path)

    file_size = os.path.getsize(path)
    if file_size > _STREAMING_THRESHOLD_BYTES:
        logger.warning(
            "Protobuf file is %.1f MB (> 100 MB). Streaming is not yet supported "
            "for binary protobuf — loading fully into memory. "
            "Track issue #16 for protobuf streaming support.",
            file_size / (1024 * 1024),
        )

    with open(path, "rb") as f:
        raw = f.read()

    traces = trace_pb2.TracesData()
    try:
        traces.ParseFromString(raw)
    except Exception as exc:
        raise ValueError(
            f"Failed to decode {path} as OTLP protobuf (TracesData). "
            f"Ensure the file was exported with --otlp-format protobuf. "
            f"Underlying error: {exc}"
        ) from exc

    if not traces.resource_spans:
        logger.warning(
            "Protobuf file %s decoded successfully but contains no resource_spans. "
            "The file may be empty or from an unsupported OTel SDK version.",
            path,
        )
        return {}

    span_service: dict[str, str] = {}
    all_spans: list[tuple[dict, str]] = []

    for resource_span in traces.resource_spans:
        service = _extract_service_proto(resource_span.resource)
        for scope_span in resource_span.scope_spans:
            for span in scope_span.spans:
                span_dict = _span_to_dict(span)
                span_id = span_dict.get("spanId", "")
                if span_id:
                    span_service[span_id] = service
                all_spans.append((span_dict, service))

    logger.debug(
        "Parsed %d spans across %d resource_spans from %s",
        len(all_spans),
        len(traces.resource_spans),
        path,
    )

    return _build_edge_map(all_spans, span_service, min_call_frequency)

def _extract_service_proto(resource) -> str:
    """Extract and normalize service.name from a protobuf Resource message.

    Iterates resource.attributes (repeated KeyValue). Returns normalized name
    or 'unknown-service' with a warning if service.name is absent.
    """
    for attr in resource.attributes:
        if attr.key == "service.name":
            # AnyValue is a oneof — string_value is the standard field
            raw = attr.value.string_value
            if not raw:
                # Defensive: handle numeric or bool service names (unusual but valid)
                raw = (
                    str(int(attr.value.int_value))   if attr.value.HasField("int_value")
                    else str(attr.value.double_value) if attr.value.HasField("double_value")
                    else str(attr.value.bool_value)   if attr.value.HasField("bool_value")
                    else None
                )
            if raw:
                return normalize(raw)

    logger.warning(
        "resource_span missing service.name attribute — using 'unknown-service'. "
        "Ensure your OTel SDK sets resource.attributes['service.name']."
    )
    return "unknown-service"


def _span_to_dict(span) -> dict:
    """Convert a protobuf Span message to the dict shape expected by _build_edge_map.

    _build_edge_map was written for JSON-parsed spans and reads:
        span.get("spanId")        — hex string
        span.get("parentSpanId")  — hex string, empty string if root span

    Protobuf Span stores span_id and parent_span_id as bytes.
    We convert to lowercase hex to match the JSON parser's format.
    """
    span_id    = span.span_id.hex()    if span.span_id    else ""
    parent_id  = span.parent_span_id.hex() if span.parent_span_id else ""

    return {
        "spanId":       span_id,
        "parentSpanId": parent_id,
        "name":         span.name,
    }


def _check_format_mismatch(path: Path) -> None:
    """Warn (never error) if the file header looks like JSON rather than binary protobuf.

    This is a safety-net for users who accidentally pass --otlp-format protobuf
    on a JSON file. The parse will proceed and fail with a clear ValueError
    from ParseFromString if the mismatch is real.
    """
    try:
        with open(path, "rb") as f:
            header = f.read(4)
    except OSError:
        return  # Let parse_otlp_proto surface the real error

    if header[:1] in _JSON_START_BYTES:
        logger.warning(
            "%s looks like a JSON file (starts with %r) but "
            "--otlp-format protobuf was specified. "
            "If this is a JSON OTLP export, use --otlp-format json instead.",
            path,
            header[:1].decode("utf-8", errors="replace"),
        )
