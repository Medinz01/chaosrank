from __future__ import annotations

import logging
import time
from dataclasses import replace
from datetime import datetime, timezone

import requests

from chaosrank.parser.incidents import Incident

logger = logging.getLogger(__name__)

# Default Prometheus metric and label — most common OTel/Prometheus conventions
DEFAULT_METRIC        = "http_requests_total"
DEFAULT_SERVICE_LABEL = "service"
DEFAULT_RATE_WINDOW   = "5m"


class PrometheusVolumeBackfiller:
    """Backfill request_volume into Incident records via Prometheus instant queries.

    Args:
        url:            Prometheus base URL (e.g. http://prometheus:9090).
        metric:         Counter metric name. Default: http_requests_total.
        service_label:  Label that identifies the service. Default: service.
        rate_window:    Rate window for rate(). Default: 5m.
        timeout:        HTTP timeout in seconds. Default: 30.
        cache_minutes:  Bucket size for caching queries (minutes). Default: 5.
                        Incidents within the same bucket+service share one query.
    """

    def __init__(
        self,
        url: str,
        metric: str = DEFAULT_METRIC,
        service_label: str = DEFAULT_SERVICE_LABEL,
        rate_window: str = DEFAULT_RATE_WINDOW,
        timeout: int = 30,
        cache_minutes: int = 5,
    ) -> None:
        self.url           = url.rstrip("/")
        self.metric        = metric
        self.service_label = service_label
        self.rate_window   = rate_window
        self._timeout      = timeout
        self._cache_minutes = cache_minutes
        self._session      = requests.Session()
        self._cache: dict[tuple[str, int], float | None] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def backfill(self, incidents: list[Incident]) -> list[Incident]:
        """Return a new list with request_volume filled in from Prometheus.

        Incidents that already have request_volume set are passed through unchanged.
        Incidents where Prometheus returns no data keep request_volume=None.

        Args:
            incidents: List of Incident dataclass instances.

        Returns:
            New list of Incident instances (input is never mutated).
        """
        if not incidents:
            return []

        needs_fill = sum(1 for i in incidents if i.request_volume is None)
        logger.info(
            "Prometheus backfill: %d/%d incidents need request_volume",
            needs_fill, len(incidents),
        )

        result = []
        filled = 0
        missing = 0

        for inc in incidents:
            if inc.request_volume is not None:
                result.append(inc)
                continue

            volume = self._query_volume(inc.service, inc.timestamp)
            if volume is not None:
                result.append(replace(inc, request_volume=volume))
                filled += 1
            else:
                result.append(inc)
                missing += 1

        logger.info(
            "Prometheus backfill complete: %d filled, %d not found in Prometheus",
            filled, missing,
        )
        if missing > 0:
            logger.warning(
                "%d incidents still have request_volume=None after backfill. "
                "Check that --prometheus-metric and --prometheus-service-label "
                "match your instrumentation. Fragility scoring will fall back "
                "to window-average for these incidents.",
                missing,
            )

        return result

    # ------------------------------------------------------------------
    # Query logic
    # ------------------------------------------------------------------

    def _query_volume(self, service: str, ts: datetime) -> float | None:
        """Query Prometheus for request rate for service at timestamp ts.

        Results are cached per (service, minute-bucket) to avoid hammering
        Prometheus for incidents that cluster in the same time window.
        """
        bucket = self._time_bucket(ts)
        cache_key = (service, bucket)

        if cache_key in self._cache:
            return self._cache[cache_key]

        volume = self._fetch(service, ts)
        self._cache[cache_key] = volume
        return volume

    def _fetch(self, service: str, ts: datetime) -> float | None:
        """Execute a Prometheus instant query and extract the scalar result."""
        # Build PromQL: rate(http_requests_total{service="svc"}[5m])
        query = (
            f'rate({self.metric}{{{self.service_label}="{service}"}}'
            f"[{self.rate_window}])"
        )
        epoch = ts.astimezone(timezone.utc).timestamp()

        try:
            resp = self._session.get(
                f"{self.url}/api/v1/query",
                params={"query": query, "time": f"{epoch:.3f}"},
                timeout=self._timeout,
            )
            resp.raise_for_status()
            data = resp.json()
        except requests.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else "?"
            if status == 429:
                retry = int(
                    exc.response.headers.get("Retry-After", 5)
                    if exc.response is not None else 5
                )
                logger.warning("Prometheus rate-limited — sleeping %ds", retry)
                time.sleep(retry)
                return self._fetch(service, ts)
            logger.error(
                "Prometheus HTTP %s querying %r at %s", status, service, ts.isoformat()
            )
            return None
        except requests.RequestException as exc:
            logger.error("Prometheus request failed for %r: %s", service, exc)
            return None

        return self._extract_value(data, service, query)

    def _extract_value(
        self, data: dict, service: str, query: str
    ) -> float | None:
        """Extract the first scalar value from a Prometheus instant query response."""
        if data.get("status") != "success":
            logger.warning(
                "Prometheus query returned status=%r for service %r. "
                "Error: %s",
                data.get("status"), service, data.get("error", "unknown"),
            )
            return None

        result = data.get("data", {}).get("result", [])
        if not result:
            logger.debug(
                "Prometheus: no data for service %r. "
                "Query: %s. "
                "Check that the service label matches your instrumentation.",
                service, query,
            )
            return None

        if len(result) > 1:
            logger.debug(
                "Prometheus returned %d series for service %r — "
                "summing across all label combinations.",
                len(result), service,
            )
            total = 0.0
            for series in result:
                try:
                    total += float(series["value"][1])
                except (KeyError, IndexError, ValueError):
                    pass
            return total if total > 0 else None

        try:
            return float(result[0]["value"][1])
        except (KeyError, IndexError, ValueError) as exc:
            logger.warning(
                "Could not parse Prometheus value for service %r: %s", service, exc
            )
            return None

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _time_bucket(self, ts: datetime) -> int:
        """Round timestamp down to nearest cache_minutes bucket (Unix minutes)."""
        epoch_minutes = int(ts.astimezone(timezone.utc).timestamp()) // 60
        return epoch_minutes - (epoch_minutes % self._cache_minutes)
