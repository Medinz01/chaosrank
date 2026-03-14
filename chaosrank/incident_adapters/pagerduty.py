import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

from chaosrank.incident_adapters.base import IncidentAdapter, infer_type
from chaosrank.parser.incidents import Incident
from chaosrank.parser.normalize import normalize

logger = logging.getLogger(__name__)

_PD_BASE = "https://api.pagerduty.com"
_PAGE_LIMIT = 100


class PagerDutyAdapter(IncidentAdapter):
    def __init__(self, api_key: str) -> None:
        self._api_key = api_key

    def source_format(self) -> str:
        return "pagerduty"

    def fetch(self, window_days: int) -> list[Incident]:
        until = datetime.now(timezone.utc)
        since = until - timedelta(days=window_days)

        incidents: list[Incident] = []
        offset = 0

        while True:
            params = urllib.parse.urlencode({
                "since": since.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "until": until.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "limit": _PAGE_LIMIT,
                "offset": offset,
                "time_zone": "UTC",
            })
            url = f"{_PD_BASE}/incidents?{params}"
            data = self._get(url)

            for item in data.get("incidents", []):
                incident = self._parse_incident(item)
                if incident:
                    incidents.append(incident)

            if not data.get("more", False):
                break
            offset += _PAGE_LIMIT

        logger.info("PagerDuty: fetched %d incidents over %d days", len(incidents), window_days)
        return incidents

    def _parse_incident(self, item: dict) -> Incident | None:
        try:
            service_raw = item.get("service", {}).get("summary", "")
            if not service_raw:
                logger.debug("Skipping PD incident with no service name: %s", item.get("id"))
                return None

            service = normalize(service_raw)
            timestamp = datetime.strptime(
                item["created_at"], "%Y-%m-%dT%H:%M:%SZ"
            )

            urgency = item.get("urgency", "low")
            # Escalate to critical if high urgency and no acknowledgements yet
            if urgency == "high" and not item.get("acknowledgements"):
                severity = "critical"
            elif urgency == "high":
                severity = "high"
            else:
                severity = "low"

            incident_type = infer_type(item.get("title", ""))

            return Incident(
                timestamp=timestamp,
                service=service,
                type=incident_type,
                severity=severity,
                request_volume=None,
            )
        except (KeyError, ValueError) as e:
            logger.warning("Skipping malformed PagerDuty incident: %s", e)
            return None

    def _get(self, url: str) -> dict:
        req = urllib.request.Request(
            url,
            headers={
                "Authorization": f"Token token={self._api_key}",
                "Accept": "application/vnd.pagerduty+json;version=2",
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            raise RuntimeError(
                f"PagerDuty API error {e.code}: {e.reason}. "
                f"Check your API token and permissions."
            ) from e
