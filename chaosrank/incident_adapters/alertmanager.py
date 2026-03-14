import json
import logging
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

from chaosrank.incident_adapters.base import IncidentAdapter, infer_type, normalize_severity
from chaosrank.parser.incidents import Incident
from chaosrank.parser.normalize import normalize

logger = logging.getLogger(__name__)

# Label keys checked in priority order for service name
_SERVICE_LABELS = ("service", "job", "app", "application")


class AlertmanagerAdapter(IncidentAdapter):
    def __init__(self, url: str, token: str | None = None) -> None:
        self._base = url.rstrip("/")
        self._token = token

    def source_format(self) -> str:
        return "alertmanager"

    def fetch(self, window_days: int) -> list[Incident]:
        cutoff = datetime.now(timezone.utc) - timedelta(days=window_days)
        api_url = f"{self._base}/api/v2/alerts"

        alerts = self._get(api_url)

        incidents: list[Incident] = []
        for alert in alerts:
            incident = self._parse_alert(alert, cutoff)
            if incident:
                incidents.append(incident)

        logger.info(
            "Alertmanager: fetched %d incidents over %d days",
            len(incidents), window_days,
        )
        return incidents

    def _parse_alert(self, alert: dict, cutoff: datetime) -> Incident | None:
        try:
            starts_at_raw = alert.get("startsAt", "")
            if not starts_at_raw:
                return None

            ts = datetime.fromisoformat(starts_at_raw.replace("Z", "+00:00"))
            if ts < cutoff:
                return None

            labels = alert.get("labels", {})

            service_raw = None
            for label in _SERVICE_LABELS:
                if label in labels:
                    service_raw = labels[label]
                    break

            if not service_raw:
                logger.debug(
                    "Skipping alert with no service label: %s",
                    labels.get("alertname", "<unknown>"),
                )
                return None

            service = normalize(service_raw)
            severity = normalize_severity(labels.get("severity", "low"))
            alertname = labels.get("alertname", "")
            incident_type = infer_type(alertname)

            return Incident(
                timestamp=ts.replace(tzinfo=None),
                service=service,
                type=incident_type,
                severity=severity,
                request_volume=None,
            )
        except (KeyError, ValueError) as e:
            logger.warning("Skipping malformed Alertmanager alert: %s", e)
            return None

    def _get(self, url: str) -> list:
        headers: dict[str, str] = {"Accept": "application/json"}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"

        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            raise RuntimeError(
                f"Alertmanager API error {e.code}: {e.reason}. "
                f"Check the --url value and network access."
            ) from e
