"""Datadog incident adapter.
Integrates monitor alert transitions and formal incident records into
ChaosRank's fragility scoring window.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

import requests

from chaosrank.incident_adapters.base import IncidentAdapter, Incident, infer_type, normalize_severity

logger = logging.getLogger(__name__)

# Incident mappings
_INCIDENT_SEVERITY_MAP: dict[str, str] = {
    "sev-1": "critical",
    "sev-2": "high",
    "sev-3": "medium",
    "sev-4": "low",
    "sev-5": "low",
    # Datadog also uses plain integers in some API versions
    "1": "critical",
    "2": "high",
    "3": "medium",
    "4": "low",
    "5": "low",
}

# Monitor alert_type values are Datadog-specific ("error", "warning", "info").
# "error" means the monitor fired at critical level — not covered by base map.
# All other values are passed through normalize_severity() from base.
_DD_ALERT_TYPE_MAP: dict[str, str] = {
    "error": "critical",   # Datadog alert_type "error" = monitor in alert state
}


def _extract_service(tags: list[str]) -> str | None:
    """Extract service name from a list of Datadog tags.

    Looks for ``service:<name>`` tags first, then ``app:<name>``.
    Returns None if no service tag is found.
    """
    for tag in tags:
        if tag.startswith("service:"):
            return tag.split(":", 1)[1].strip()
    for tag in tags:
        if tag.startswith("app:"):
            return tag.split(":", 1)[1].strip()
    return None


class DatadogIncidentAdapter(IncidentAdapter):
    """Fetch incidents from Datadog Events API v2 and Incidents API.

    Args:
        api_key:        Datadog API key (DD-API-KEY header).
        app_key:        Datadog Application key (DD-APPLICATION-KEY header).
        site:           Datadog site hostname. Default ``datadoghq.com``.
                        Use ``datadoghq.eu`` for EU region.
        timeout:        HTTP request timeout in seconds. Default 30.
        page_limit:     Max events/incidents per page request. Default 1000.
    """

    def __init__(
        self,
        api_key: str,
        app_key: str,
        site: str = "datadoghq.com",
        timeout: int = 30,
        page_limit: int = 1000,
    ) -> None:
        self._base_url = f"https://api.{site}"
        self._timeout = timeout
        self._page_limit = page_limit
        self._session = requests.Session()
        self._session.headers.update(
            {
                "DD-API-KEY": api_key,
                "DD-APPLICATION-KEY": app_key,
                "Content-Type": "application/json",
            }
        )

    # IncidentAdapter contract

    def source_format(self) -> str:
        return "datadog"

    def fetch(self, window_days: int) -> list[Incident]:
        """Return merged, deduplicated incidents from both APIs.

        Args:
            window_days: How far back to look (from now).

        Returns:
            List of Incident dataclass instances, sorted oldest-first.
        """
        now = datetime.now(tz=timezone.utc)
        from_ts = int(now.timestamp()) - window_days * 86400

        events    = self._fetch_monitor_events(from_ts, int(now.timestamp()))
        incidents = self._fetch_formal_incidents(from_ts, now)

        merged = self._deduplicate(events + incidents)
        merged.sort(key=lambda i: i.timestamp)

        logger.info(
            "datadog adapter: %d monitor events + %d formal incidents → %d merged",
            len(events),
            len(incidents),
            len(merged),
        )
        return merged

    # Events API v2 (monitor alerts)

    def _fetch_monitor_events(self, from_epoch: int, to_epoch: int) -> list[Incident]:
        """Fetch monitor alert transitions from /api/v2/events."""
        url = f"{self._base_url}/api/v2/events"
        incidents: list[Incident] = []
        cursor: str | None = None

        while True:
            params: dict = {
                "filter[from]":  from_epoch,
                "filter[to]":    to_epoch,
                "filter[query]": "source:monitor @alert_transition:Triggered",
                "page[limit]":   self._page_limit,
            }
            if cursor:
                params["page[cursor]"] = cursor

            resp = self._get(url, params)
            if resp is None:
                break

            for event in resp.get("data", []):
                incident = self._parse_monitor_event(event)
                if incident is not None:
                    incidents.append(incident)

            # Cursor-based pagination
            cursor = (
                resp.get("meta", {})
                    .get("pagination", {})
                    .get("next_cursor")
            )
            if not cursor:
                break

        return incidents

    def _parse_monitor_event(self, event: dict) -> Incident | None:
        """Parse a single Events API v2 event dict into an Incident."""
        attrs = event.get("attributes", {})
        tags: list[str] = attrs.get("tags", [])

        service = _extract_service(tags)
        if not service:
            logger.debug(
                "datadog: skipping event %s — no service tag", event.get("id")
            )
            return None

        # Timestamp: Events API v2 returns epoch seconds in attributes.timestamp
        raw_ts = attrs.get("timestamp")
        if raw_ts is None:
            return None
        timestamp = datetime.fromtimestamp(raw_ts, tz=timezone.utc)

        # Severity from priority tag or alert_type
        severity = self._monitor_severity(attrs, tags)

        # Type from title + message — uses shared infer_type() from base
        title   = attrs.get("title", "")
        message = attrs.get("message", "")
        inc_type = infer_type(f"{title} {message}")

        return Incident(
            timestamp=timestamp,
            service=service,
            type=inc_type,
            severity=severity,
            request_volume=None,  # populated by #15 (Prometheus adapter)
        )

    def _monitor_severity(self, attrs: dict, tags: list[str]) -> str:
        """Resolve severity for a monitor event.

        Priority order:
          1. ``priority:<level>`` tag on the monitor → normalize_severity() from base
          2. alert_type field — "error" maps to critical (DD-specific);
             all others through normalize_severity()
          3. Default to "medium"
        """
        for tag in tags:
            if tag.startswith("priority:"):
                level = tag.split(":", 1)[1].strip()
                return normalize_severity(level)

        alert_type = attrs.get("alert_type", "").lower().strip()
        if alert_type in _DD_ALERT_TYPE_MAP:
            return _DD_ALERT_TYPE_MAP[alert_type]
        if alert_type:
            return normalize_severity(alert_type)
        return "medium"

    # Incidents API (formal records)

    def _fetch_formal_incidents(
        self, from_epoch: int, now: datetime
    ) -> list[Incident]:
        """Fetch formal incident records from /api/v2/incidents."""
        url = f"{self._base_url}/api/v2/incidents"
        incidents: list[Incident] = []
        offset = 0

        from_iso = datetime.fromtimestamp(from_epoch, tz=timezone.utc).isoformat()
        to_iso   = now.isoformat()

        while True:
            params: dict = {
                "filter[created][start]": from_iso,
                "filter[created][end]":   to_iso,
                "page[size]":             self._page_limit,
                "page[offset]":           offset,
                "include":                "impacts",   # service impact relationships
            }

            resp = self._get(url, params)
            if resp is None:
                break

            data = resp.get("data", [])
            if not data:
                break

            for record in data:
                parsed = self._parse_formal_incident(record, resp.get("included", []))
                incidents.extend(parsed)

            # Offset-based pagination
            total = resp.get("meta", {}).get("pagination", {}).get("total_count", 0)
            offset += len(data)
            if offset >= total:
                break

        return incidents

    def _parse_formal_incident(
        self, record: dict, included: list[dict]
    ) -> list[Incident]:
        """Parse a formal incident record.

        One incident record may impact multiple services — yields one
        Incident per affected service.
        """
        attrs = record.get("attributes", {})

        raw_ts = attrs.get("created")
        if not raw_ts:
            return []
        try:
            timestamp = datetime.fromisoformat(raw_ts.replace("Z", "+00:00"))
        except ValueError:
            logger.debug("datadog: could not parse timestamp %r", raw_ts)
            return []

        severity_raw = attrs.get("severity", "sev-5").lower()
        severity = _INCIDENT_SEVERITY_MAP.get(severity_raw, "low")

        title    = attrs.get("title", "")
        inc_type = infer_type(title)

        services = self._extract_services_from_impacts(record, included)
        if not services:
            logger.debug(
                "datadog: formal incident %s has no impacted services",
                record.get("id"),
            )
            return []

        return [
            Incident(
                timestamp=timestamp,
                service=svc,
                type=inc_type,
                severity=severity,
                request_volume=None,
            )
            for svc in services
        ]

    def _extract_services_from_impacts(
        self, record: dict, included: list[dict]
    ) -> list[str]:
        """Resolve impacted service names from incident relationships.

        The Incidents API returns service impacts as a relationship list.
        We look them up in the ``included`` sideloaded resources.
        Falls back to parsing ``customer_impacted_scope`` string.
        """
        services: list[str] = []

        # Primary path: relationships.impacts → included services
        impact_refs = (
            record.get("relationships", {})
                  .get("impacts", {})
                  .get("data", [])
        )
        if impact_refs:
            included_by_id = {
                item["id"]: item
                for item in included
                if item.get("type") == "services"
            }
            for ref in impact_refs:
                if ref.get("type") == "services":
                    svc_record = included_by_id.get(ref["id"], {})
                    name = (
                        svc_record.get("attributes", {}).get("name")
                        or svc_record.get("attributes", {}).get("display_name")
                    )
                    if name:
                        services.append(name)

        # Fallback: customer_impacted_scope is a free-text field but often
        # contains a comma-separated service list in practice
        if not services:
            scope = record.get("attributes", {}).get("customer_impacted_scope", "")
            if scope:
                services = [s.strip() for s in scope.split(",") if s.strip()]

        return services

    # ------------------------------------------------------------------
    # Deduplication
    # ------------------------------------------------------------------

    def _deduplicate(self, incidents: list[Incident]) -> list[Incident]:
        """Remove duplicate incidents.

        Two incidents are considered duplicates if they share the same
        (service, type, severity) and fall within a 5-minute window.
        Formal incidents (from Incidents API, request_volume=None) take
        precedence over monitor events for the same window.

        This is a best-effort dedup — the burst dedup in fragility.py
        is the authoritative one. This just prevents obvious double-counting
        when a P1 incident also fires monitor alerts.
        """
        seen: dict[tuple, datetime] = {}
        result: list[Incident] = []

        # Sort: formal incidents first so they win dedup ties
        sorted_incidents = sorted(
            incidents,
            key=lambda i: (i.request_volume is not None, i.timestamp),
        )

        for inc in sorted_incidents:
            key = (inc.service, inc.type, inc.severity)
            last_seen = seen.get(key)
            if last_seen is not None:
                delta = abs((inc.timestamp - last_seen).total_seconds())
                if delta <= 300:  # 5-minute window
                    continue
            seen[key] = inc.timestamp
            result.append(inc)

        return result

    # HTTP helper

    def _get(self, url: str, params: dict) -> dict | None:
        """Execute a GET request, return parsed JSON or None on error."""
        try:
            resp = self._session.get(url, params=params, timeout=self._timeout)
            resp.raise_for_status()
            return resp.json()
        except requests.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else "?"
            if status == 429:
                retry_after = int(
                    exc.response.headers.get("X-RateLimit-Reset", 60)
                    if exc.response is not None else 60
                )
                logger.warning(
                    "datadog: rate limited — sleeping %ds", retry_after
                )
                time.sleep(retry_after)
                return self._get(url, params)   # single retry after backoff
            logger.error("datadog: HTTP %s on %s — %s", status, url, exc)
            return None
        except requests.RequestException as exc:
            logger.error("datadog: request failed on %s — %s", url, exc)
            return None