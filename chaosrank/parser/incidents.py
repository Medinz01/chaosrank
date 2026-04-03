import csv
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_SEVERITY_WEIGHTS: dict[str, float] = {
    "critical": 1.000,
    "high":     0.602,
    "medium":   0.301,
    "low":      0.100,
}

ASYNC_SERVICE_PATTERNS = ("kafka", "sqs", "rabbitmq", "pubsub", "nats", "kinesis")


@dataclass
class Incident:
    timestamp: datetime
    service: str
    type: str
    severity: str
    request_volume: float | None

    @property
    def age_days(self) -> float:
        return (datetime.utcnow() - self.timestamp).total_seconds() / 86400

    def severity_weight(self, weights: dict[str, float] = DEFAULT_SEVERITY_WEIGHTS) -> float:
        return weights.get(self.severity.lower(), DEFAULT_SEVERITY_WEIGHTS["low"])


@dataclass
class ServiceIncidents:
    service: str
    incidents: list[Incident] = field(default_factory=list)

    @property
    def mean_request_volume(self) -> float | None:
        vols = [i.request_volume for i in self.incidents if i.request_volume is not None]
        return sum(vols) / len(vols) if vols else None


def parse_incidents(
    path: Path,
) -> dict[str, ServiceIncidents]:
    results: dict[str, ServiceIncidents] = {}
    skipped = 0

    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row_num, row in enumerate(reader, start=2):
            try:
                service = row["service"].strip().lower()
                if not service:
                    skipped += 1
                    continue

                timestamp = _parse_timestamp(row["timestamp"].strip())
                incident_type = row.get("type", "unknown").strip().lower()
                severity = row.get("severity", "low").strip().lower()

                raw_vol = row.get("request_volume", "").strip()
                request_volume: float | None = None
                if raw_vol:
                    try:
                        request_volume = float(raw_vol)
                    except ValueError:
                        logger.warning("Row %d: invalid request_volume '%s', treating as None", row_num, raw_vol)

                incident = Incident(
                    timestamp=timestamp,
                    service=service,
                    type=incident_type,
                    severity=severity,
                    request_volume=request_volume,
                )

                if service not in results:
                    results[service] = ServiceIncidents(service=service)
                results[service].incidents.append(incident)

            except (KeyError, ValueError) as e:
                logger.warning("Row %d: skipping malformed row — %s", row_num, e)
                skipped += 1

    if skipped:
        logger.warning("Skipped %d malformed rows during incident parsing", skipped)

    async_detected = [s for s in results if any(p in s for p in ASYNC_SERVICE_PATTERNS)]
    if async_detected:
        logger.warning(
            "Async messaging services detected — blast radius scores may be incomplete "
            "for event-driven dependencies. Verify against known async dependency maps. "
            "Detected: %s",
            ", ".join(async_detected),
        )

    logger.info("Parsed incidents for %d services (%d rows skipped)", len(results), skipped)
    return results


def _parse_timestamp(raw: str) -> datetime:
    formats = [
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S.%fZ",
        "%Y-%m-%dT%H:%M:%S.%f",
    ]
    for fmt in formats:
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            continue
    raise ValueError(f"Unrecognised timestamp format: '{raw}'")
