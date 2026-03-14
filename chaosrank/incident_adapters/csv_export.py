import csv
import io
import logging
import sys
from pathlib import Path

from chaosrank.parser.incidents import Incident

logger = logging.getLogger(__name__)

_FIELDNAMES = ["timestamp", "service", "type", "severity", "request_volume"]


def incidents_to_csv(incidents: list[Incident], path: Path | None) -> int:
    """Write incidents to a CSV file or stdout.

    Args:
        incidents: List of Incident objects to write.
        path:      Output path. If None, writes to stdout.

    Returns:
        Number of rows written.
    """
    if not incidents:
        logger.warning("No incidents to write.")
        return 0

    if path:
        with open(path, "w", newline="", encoding="utf-8") as f:
            _write_csv(f, incidents)
    else:
        _write_csv(sys.stdout, incidents)

    return len(incidents)


def _write_csv(stream: io.TextIOBase, incidents: list[Incident]) -> None:
    writer = csv.DictWriter(stream, fieldnames=_FIELDNAMES)
    writer.writeheader()
    for inc in incidents:
        writer.writerow({
            "timestamp":      inc.timestamp.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "service":        inc.service,
            "type":           inc.type,
            "severity":       inc.severity,
            "request_volume": "" if inc.request_volume is None else inc.request_volume,
        })
