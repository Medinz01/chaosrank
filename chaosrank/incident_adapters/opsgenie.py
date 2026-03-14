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

_OPSGENIE_BASE = "https://api.opsgenie.com"
_PAGE_LIMIT = 100


class OpsgenieAdapter(IncidentAdapter):
    def __init__(self, api_key: str) -> None:
        self._api_key = api_key

    def source_format(self) -> str:
        return "opsgenie"

    def fetch(self, window_days: int) -> list[Incident]:
        since = datetime.now(timezone.utc) - timedelta(days=window_days)
        # Opsgenie createdAt filter uses epoch milliseconds in lucene query
        since_ms = int(since.timestamp() * 1000)

        incidents: list[Incident] = []
        offset = 0

        while True:
            params = urllib.parse.urlencode({
                "limit": _PAGE_LIMIT,
                "offset": offset,
                "query": f"createdAt > {since_ms}",
                "order": "desc",
            })
            url = f"{_OPSGENIE_BASE}/v2/alerts?{params}"
            data = self._get(url)

            alerts = data.get("data", [])
            for alert in alerts:
                incident = self._parse_alert(alert)
                if incident:
                    incidents.append(incident)

            # Stop if we got fewer than a full page
            if len(alerts) < _PAGE_LIMIT:
                break
            offset += _PAGE_LIMIT

        logger.info(
            "Opsgenie: fetched %d incidents over %d days",
            len(incidents), window_days,
        )
        return incidents

    def _parse_alert(self, alert: dict) -> Incident | None:
        try:
            created_at_raw = alert.get("createdAt", "")
            if not created_at_raw:
                return None

            ts = datetime.fromisoformat(created_at_raw.replace("Z", "+00:00"))

            service_raw = self._extract_service(alert)
            if not service_raw:
                logger.debug(
                    "Skipping Opsgenie alert with no service tag/detail: %s",
                    alert.get("id"),
                )
                return None

            service = normalize(service_raw)
            priority = alert.get("priority", "P3")
            severity = normalize_severity(priority)
            message = alert.get("message", "")
            incident_type = infer_type(message)

            return Incident(
                timestamp=ts.replace(tzinfo=None),
                service=service,
                type=incident_type,
                severity=severity,
                request_volume=None,
            )
        except (KeyError, ValueError) as e:
            logger.warning("Skipping malformed Opsgenie alert: %s", e)
            return None

    @staticmethod
    def _extract_service(alert: dict) -> str | None:
        # 1. Check tags for "service:<name>" convention
        for tag in alert.get("tags", []):
            if tag.startswith("service:"):
                return tag[len("service:"):]

        # 2. Check details dict
        details = alert.get("details", {})
        return details.get("service") or details.get("job") or None

    def _get(self, url: str) -> dict:
        req = urllib.request.Request(
            url,
            headers={
                "Authorization": f"GenieKey {self._api_key}",
                "Accept": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            raise RuntimeError(
                f"Opsgenie API error {e.code}: {e.reason}. "
                f"Check your API key."
            ) from e
