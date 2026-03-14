import logging
import math
from collections import Counter
from datetime import datetime

from chaosrank.parser.incidents import Incident, ServiceIncidents

logger = logging.getLogger(__name__)

DEFAULT_LAMBDA = 0.10

FAULT_MAP = {
    "latency": "latency-injection",
    "error":   "partial-response",
    "timeout": "connection-timeout",
}
DEFAULT_FAULT = "pod-failure"

PURITY_HIGH   = 0.70
PURITY_MEDIUM = 0.50
N_HIGH        = 5
N_MEDIUM      = 2


def _effective_incidents(
    incidents: list[Incident],
    decay_lambda: float,
) -> list[Incident]:
    threshold_days = -math.log(0.05) / decay_lambda
    now_ts = datetime.utcnow()
    return [
        i for i in incidents
        if (now_ts - i.timestamp).total_seconds() / 86400 < threshold_days
    ]


def _confidence(effective_n: int, purity: float) -> str:
    if effective_n >= N_HIGH:
        if purity > PURITY_HIGH:
            return "high"
        elif purity >= PURITY_MEDIUM:
            return "medium"
        else:
            return "low"
    elif effective_n >= N_MEDIUM:
        return "medium" if purity > PURITY_HIGH else "low"
    return "low"


def suggest_fault(
    service: str,
    service_incidents: dict[str, ServiceIncidents],
    decay_lambda: float = DEFAULT_LAMBDA,
) -> tuple[str, str]:
    si = service_incidents.get(service)
    if si is None or not si.incidents:
        return DEFAULT_FAULT, "low"

    effective = _effective_incidents(si.incidents, decay_lambda)
    if not effective:
        return DEFAULT_FAULT, "low"

    effective_n = len(effective)
    most_common_type, most_common_count = Counter(i.type for i in effective).most_common(1)[0]
    purity = most_common_count / effective_n

    fault = FAULT_MAP.get(most_common_type, DEFAULT_FAULT)
    confidence = _confidence(effective_n, purity)

    logger.debug(
        "%s: effective_n=%d, dominant=%s (%.0f%%), fault=%s, confidence=%s",
        service, effective_n, most_common_type, purity * 100, fault, confidence,
    )

    return fault, confidence
