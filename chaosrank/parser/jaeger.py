import json
import logging
from pathlib import Path
from typing import Generator
import ijson
from chaosrank.parser.normalize import normalize

logger = logging.getLogger(__name__)

_STREAMING_THRESHOLD = 100 * 1024 * 1024


def _iter_spans(path: Path) -> Generator[dict, None, None]:
    file_size = path.stat().st_size

    if file_size > _STREAMING_THRESHOLD:
        logger.debug("Large trace file (%dMB), using ijson streaming", file_size // 1024 // 1024)
        with open(path, "rb") as f:
            for span in ijson.items(f, "data.item.spans.item"):
                yield span
    else:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        for trace in data.get("data", []):
            for span in trace.get("spans", []):
                yield span


def _get_service_name(span: dict, processes: dict) -> str | None:
    process_id = span.get("processID")
    if not process_id:
        return None
    process = processes.get(process_id, {})
    raw_name = process.get("serviceName", "")
    return normalize(raw_name)


def _process_span_tags(span: dict, edges: dict, seen_services: dict) -> None:
    tags = {t["key"]: t["value"] for t in span.get("tags", []) if "key" in t and "value" in t}
    service = normalize(tags.get("service.name", ""))
    if service:
        seen_services[service] = seen_services.get(service, 0) + 1


def parse_traces(path: Path, min_call_frequency: int = 10) -> dict[tuple[str, str], int]:
    edges: dict[tuple[str, str], int] = {}
    seen_services: dict[str, int] = {}

    file_size = path.stat().st_size

    if file_size > _STREAMING_THRESHOLD:
        logger.warning("Streaming mode: process map unavailable, using span tags for service names")
        with open(path, "rb") as f:
            for span in ijson.items(f, "data.item.spans.item"):
                _process_span_tags(span, edges, seen_services)
    else:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)

        for trace in data.get("data", []):
            processes = trace.get("processes", {})
            spans_by_id: dict[str, dict] = {span["spanID"]: span for span in trace.get("spans", [])}

            for span in trace.get("spans", []):
                callee = _get_service_name(span, processes)
                if not callee:
                    continue

                seen_services[callee] = seen_services.get(callee, 0) + 1

                for ref in span.get("references", []):
                    if ref.get("refType") != "CHILD_OF":
                        continue
                    parent_span = spans_by_id.get(ref.get("spanID"))
                    if not parent_span:
                        continue
                    caller = _get_service_name(parent_span, processes)
                    if not caller or caller == callee:
                        continue
                    edge = (caller, callee)
                    edges[edge] = edges.get(edge, 0) + 1

    filtered = {e: w for e, w in edges.items() if w >= min_call_frequency}

    phantoms = [s for s, count in seen_services.items() if count == 1]
    if phantoms:
        logger.warning(
            "Services appearing only once in trace window (possible phantom nodes): %s",
            ", ".join(sorted(phantoms)),
        )

    logger.info(
        "Parsed %d edges, %d after frequency filter (min=%d)",
        len(edges), len(filtered), min_call_frequency,
    )

    return filtered
