import logging
import math
from collections import defaultdict
from datetime import datetime

from chaosrank.parser.incidents import Incident, ServiceIncidents

logger = logging.getLogger(__name__)

DEFAULT_LAMBDA = 0.10
DEFAULT_BASE_WINDOW = 5.0
DEFAULT_SEVERITY_WEIGHTS = {
    "critical": 1.000,
    "high":     0.602,
    "medium":   0.301,
    "low":      0.100,
}


def _baseline_volume(service_incidents: dict[str, ServiceIncidents]) -> float:
    means = []
    for si in service_incidents.values():
        mv = si.mean_request_volume
        if mv is not None and mv > 0:
            means.append(mv)

    if not means:
        return 1.0

    means.sort()
    mid = len(means) // 2
    if len(means) % 2 == 0:
        return (means[mid - 1] + means[mid]) / 2.0
    return means[mid]


def _burst_window_minutes(
    request_volume: float,
    baseline: float,
    base_window: float,
) -> float:
    return base_window * math.log1p(request_volume / baseline)


def _deduplicate(
    incidents: list[Incident],
    baseline: float,
    base_window: float,
) -> list[Incident]:
    if not incidents:
        return []

    by_type: dict[str, list[Incident]] = defaultdict(list)
    for inc in incidents:
        by_type[inc.type].append(inc)

    deduplicated: list[Incident] = []

    for inc_type, group in by_type.items():
        sorted_group = sorted(group, key=lambda i: i.timestamp)
        kept = [sorted_group[0]]

        for current in sorted_group[1:]:
            last = kept[-1]
            vol = current.request_volume if current.request_volume is not None else baseline
            window_seconds = _burst_window_minutes(vol, baseline, base_window) * 60
            gap = (current.timestamp - last.timestamp).total_seconds()
            if gap > window_seconds:
                kept.append(current)

        deduplicated.extend(kept)

    return deduplicated


def _weighted_incident(
    incident: Incident,
    severity_weights: dict[str, float],
    window_avg_volume: float | None,
) -> float:
    sw = severity_weights.get(incident.severity.lower(), DEFAULT_SEVERITY_WEIGHTS["low"])

    vol = incident.request_volume
    if vol is None:
        if window_avg_volume is not None:
            logger.debug(
                "Incident at %s for %s: using window avg volume (%.0f)",
                incident.timestamp, incident.service, window_avg_volume,
            )
            vol = window_avg_volume
        else:
            logger.warning(
                "Incident at %s for %s: no request_volume available, skipping normalization",
                incident.timestamp, incident.service,
            )
            return sw

    denom = math.log1p(vol)
    if denom == 0:
        return sw

    return sw / denom


def compute_fragility(
    service_incidents: dict[str, ServiceIncidents],
    all_service_names: list[str],
    decay_lambda: float = DEFAULT_LAMBDA,
    base_window: float = DEFAULT_BASE_WINDOW,
    severity_weights: dict[str, float] | None = None,
) -> dict[str, float]:
    """Compute a normalized [0, 1] fragility score per service using burst deduplication, traffic normalization, exponential decay, and z-score normalization."""
    weights = severity_weights or DEFAULT_SEVERITY_WEIGHTS
    baseline = _baseline_volume(service_incidents)
    now = datetime.utcnow()

    raw_scores: dict[str, float] = {}

    for service in all_service_names:
        si = service_incidents.get(service)
        if si is None or not si.incidents:
            raw_scores[service] = 0.0
            continue

        logical = _deduplicate(si.incidents, baseline, base_window)
        window_avg = si.mean_request_volume
        total = 0.0

        for inc in logical:
            w = _weighted_incident(inc, weights, window_avg)
            age = (now - inc.timestamp).total_seconds() / 86400
            total += w * math.exp(-decay_lambda * age)

        raw_scores[service] = total

    normalized = _zscore_normalize(raw_scores)

    logger.info("Fragility computed for %d services", len(normalized))
    return normalized


def _zscore_normalize(raw: dict[str, float]) -> dict[str, float]:
    if not raw:
        return {}

    values = list(raw.values())
    mean = sum(values) / len(values)
    stddev = math.sqrt(sum((v - mean) ** 2 for v in values) / len(values))

    if stddev == 0:
        logger.warning("Fragility scores are uniform. Incident data may be insufficient.")
        return {k: 0.5 for k in raw}

    return {
        service: (max(-3.0, min(3.0, (val - mean) / stddev)) + 3.0) / 6.0
        for service, val in raw.items()
    }
