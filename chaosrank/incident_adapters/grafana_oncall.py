import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

from chaosrank.incident_adapters.base import IncidentAdapter, infer_type, normalize_severity
from chaosrank.parser.incidents import Incident
from chaosrank.parser.normalize import normalize

logger = logging.getLogger(__name__)

_PAGE_LIMIT = 100


class GrafanaOnCallAdapter(IncidentAdapter):
    def __init__(self, url: str, token: str) -> None:
        # url: e.g. "https://oncall-prod-us-central-0.grafana.net/oncall"
        #       or  "http://localhost:8080" for self-hosted
        self._base = url.rstrip("/")
        self._token = token

    def source_format(self) -> str:
        return "grafana-oncall"

    def fetch(self, window_days: int) -> list[Incident]:
        since = datetime.now(timezone.utc) - timedelta(days=window_days)

        incidents: list[Incident] = []
        next_url: str | None = (
            f"{self._base}/api/v1/alert_groups/"
            f"?limit={_PAGE_LIMIT}&resolved=true"
        )

        while next_url:
            data = self._get(next_url)
            for group in data.get("results", []):
                incident = self._parse_group(group, since)
                if incident:
                    incidents.append(incident)
            next_url = data.get("next")

        logger.info(
            "Grafana OnCall: fetched %d incidents over %d days",
            len(incidents), window_days,
        )
        return incidents

    def _parse_group(self, group: dict, since: datetime) -> Incident | None:
        try:
            received_at_raw = group.get("received_at", "")
            if not received_at_raw:
                return None

            ts = datetime.fromisoformat(received_at_raw.replace("Z", "+00:00"))
            if ts < since:
                return None

            # Service name extracted from alert payload labels first,
            # then falls back to the integration/team name
            alerts = group.get("alerts", [])
            service_raw = None
            title = group.get("title", "")

            if alerts:
                payload = alerts[0].get("payload", {})
                labels = payload.get("labels", {})
                service_raw = (
                    labels.get("service")
                    or labels.get("job")
                    or labels.get("app")
                )
                if not title:
                    title = payload.get("title", "")

            if not service_raw:
                logger.debug(
                    "Skipping OnCall alert group with no service label: %s",
                    group.get("id"),
                )
                return None

            service = normalize(service_raw)
            # OnCall doesn't carry a normalized severity — infer from title
            severity = normalize_severity(
                _extract_label(alerts, "severity") or "low"
            )
            incident_type = infer_type(title)

            return Incident(
                timestamp=ts.replace(tzinfo=None),
                service=service,
                type=incident_type,
                severity=severity,
                request_volume=None,
            )
        except (KeyError, ValueError) as e:
            logger.warning("Skipping malformed Grafana OnCall alert group: %s", e)
            return None

    def _get(self, url: str) -> dict:
        req = urllib.request.Request(
            url,
            headers={
                "Authorization": f"Token {self._token}",
                "Accept": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            raise RuntimeError(
                f"Grafana OnCall API error {e.code}: {e.reason}. "
                f"Check your token and --url value."
            ) from e


def _extract_label(alerts: list[dict], key: str) -> str | None:
    """Extract a label value from the first alert's payload labels."""
    if not alerts:
        return None
    payload = alerts[0].get("payload", {})
    return payload.get("labels", {}).get(key)
