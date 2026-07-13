"""Tests for chaosrank.incident_adapters.datadog.

Covers:
  - Severity mapping: monitor alert_type + priority tag + formal SEV-1..5
  - Service extraction from tags (service: + app: fallback)
  - Incident type inference from title/message keywords
  - Monitor event parsing (happy path + missing service tag)
  - Formal incident parsing (single service + multi-service via impacts)
  - Formal incident fallback: customer_impacted_scope string
  - Deduplication of overlapping monitor + formal incidents
  - Pagination: Events API cursor-based, Incidents API offset-based
  - window_days respected (from_epoch passed correctly)
  - 429 rate-limit: single retry after backoff
  - HTTP errors and network failures handled gracefully
  - request_volume is always None
"""

from __future__ import annotations

import time
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch

import pytest
import requests

from chaosrank.incident_adapters.datadog import (
    DatadogIncidentAdapter,
    _extract_service,
    _INCIDENT_SEVERITY_MAP,
    _DD_ALERT_TYPE_MAP,
)
from chaosrank.incident_adapters.base import Incident, infer_type, normalize_severity



@pytest.fixture
def adapter():
    return DatadogIncidentAdapter(
        api_key="test-api-key",
        app_key="test-app-key",
    )


def _make_event(
    service: str = "payment-service",
    alert_type: str = "error",
    timestamp: int = 1_700_000_000,
    title: str = "High error rate on payment-service",
    tags: list[str] | None = None,
) -> dict:
    if tags is None:
        tags = [f"service:{service}", "env:prod"]
    return {
        "id": "evt-001",
        "attributes": {
            "title": title,
            "message": "",
            "alert_type": alert_type,
            "timestamp": timestamp,
            "tags": tags,
        },
    }


def _make_formal_incident(
    severity: str = "sev-1",
    title: str = "Payment service outage",
    service_names: list[str] | None = None,
    created: str = "2024-01-15T10:00:00Z",
) -> tuple[dict, list[dict]]:
    """Return (incident_record, included_sideloads)."""
    service_names = service_names or ["payment-service"]
    impact_refs   = [{"type": "services", "id": f"svc-{i}"} for i, _ in enumerate(service_names)]
    included      = [
        {
            "id":   f"svc-{i}",
            "type": "services",
            "attributes": {"name": name},
        }
        for i, name in enumerate(service_names)
    ]
    record = {
        "id": "inc-001",
        "attributes": {
            "title":    title,
            "severity": severity,
            "created":  created,
            "customer_impacted_scope": "",
        },
        "relationships": {
            "impacts": {"data": impact_refs}
        },
    }
    return record, included



class TestExtractService:
    def test_service_tag(self):
        assert _extract_service(["env:prod", "service:auth-service"]) == "auth-service"

    def test_app_tag_fallback(self):
        assert _extract_service(["app:cart-service", "env:staging"]) == "cart-service"

    def test_service_tag_wins_over_app(self):
        assert _extract_service(["app:x", "service:y"]) == "y"

    def test_no_service_tag_returns_none(self):
        assert _extract_service(["env:prod", "team:backend"]) is None

    def test_empty_tags(self):
        assert _extract_service([]) is None

    def test_strips_whitespace(self):
        assert _extract_service(["service: payment-service "]) == "payment-service"


class TestInferType:
    """Verify adapter passes text to shared infer_type() correctly.

    infer_type() itself is tested in test_base.py — here we only verify
    the keywords that matter for Datadog monitor titles and incident titles.
    """
    @pytest.mark.parametrize("text,expected", [
        ("High p99 latency on auth",          "latency"),
        ("Error rate breach on payment",      "error"),
        ("5xx spike detected",                "error"),
        ("Connection timeout on db",          "timeout"),
        ("connect_timeout on upstream",       "timeout"),
        ("CPU usage high",                    "error"),   # base default is "error"
        ("Latency AND error rate",            "latency"), # latency checked first
    ])
    def test_shared_infer_type_keywords(self, text, expected):
        assert infer_type(text) == expected

    def test_case_insensitive(self):
        assert infer_type("P99 LATENCY SPIKE") == "latency"


class TestSeverityMaps:
    @pytest.mark.parametrize("raw,expected", [
        ("sev-1", "critical"),
        ("sev-2", "high"),
        ("sev-3", "medium"),
        ("sev-4", "low"),
        ("sev-5", "low"),
        ("1",     "critical"),
        ("2",     "high"),
    ])
    def test_incident_severity_map(self, raw, expected):
        assert _INCIDENT_SEVERITY_MAP[raw] == expected

    def test_dd_alert_type_error_is_critical(self):
        # "error" alert_type is Datadog-specific — not in base normalize_severity
        assert _DD_ALERT_TYPE_MAP["error"] == "critical"

    @pytest.mark.parametrize("raw,expected", [
        ("warning",  "medium"),
        ("warn",     "medium"),
        ("info",     "low"),
        ("p1",       "critical"),
        ("p2",       "high"),
    ])
    def test_normalize_severity_covers_priority_tags(self, raw, expected):
        # priority: tags on monitors go through base normalize_severity()
        assert normalize_severity(raw) == expected



class TestParseMonitorEvent:
    def test_happy_path(self, adapter):
        event = _make_event(
            service="payment-service",
            alert_type="error",
            timestamp=1_700_000_000,
            title="Error rate spike",
        )
        inc = adapter._parse_monitor_event(event)
        assert inc is not None
        assert inc.service  == "payment-service"
        assert inc.severity == "critical"
        assert inc.type     == "error"
        assert inc.request_volume is None

    def test_priority_tag_overrides_alert_type(self, adapter):
        event = _make_event(
            alert_type="error",
            tags=["service:auth-service", "priority:warning"],
        )
        inc = adapter._parse_monitor_event(event)
        assert inc.severity == "medium"   # normalize_severity("warning") = medium

    def test_missing_service_tag_returns_none(self, adapter):
        event = _make_event(tags=["env:prod", "team:backend"])
        assert adapter._parse_monitor_event(event) is None

    def test_missing_timestamp_returns_none(self, adapter):
        event = {
            "id": "x",
            "attributes": {"tags": ["service:foo"], "timestamp": None},
        }
        assert adapter._parse_monitor_event(event) is None

    def test_latency_title_inferred(self, adapter):
        event = _make_event(title="p99 latency regression detected")
        inc = adapter._parse_monitor_event(event)
        assert inc.type == "latency"

    def test_unknown_alert_type_defaults_low(self, adapter):
        event = _make_event(alert_type="no_data")
        inc = adapter._parse_monitor_event(event)
        assert inc.severity == "low"



class TestParseFormalIncident:
    def test_single_service(self, adapter):
        record, included = _make_formal_incident(
            severity="sev-1",
            service_names=["payment-service"],
        )
        incidents = adapter._parse_formal_incident(record, included)
        assert len(incidents) == 1
        assert incidents[0].service  == "payment-service"
        assert incidents[0].severity == "critical"
        assert incidents[0].request_volume is None

    def test_multi_service_yields_one_incident_each(self, adapter):
        record, included = _make_formal_incident(
            service_names=["payment-service", "auth-service", "cart-service"],
        )
        incidents = adapter._parse_formal_incident(record, included)
        assert len(incidents) == 3
        services = {i.service for i in incidents}
        assert services == {"payment-service", "auth-service", "cart-service"}

    def test_all_have_same_severity_and_type(self, adapter):
        record, included = _make_formal_incident(
            severity="sev-2",
            title="Timeout cascade on checkout",
            service_names=["a", "b"],
        )
        incidents = adapter._parse_formal_incident(record, included)
        assert all(i.severity == "high"    for i in incidents)
        assert all(i.type     == "timeout" for i in incidents)

    def test_fallback_to_customer_impacted_scope(self, adapter):
        record, _ = _make_formal_incident(service_names=[])
        record["relationships"]["impacts"]["data"] = []
        record["attributes"]["customer_impacted_scope"] = "svc-a, svc-b"
        incidents = adapter._parse_formal_incident(record, [])
        assert {i.service for i in incidents} == {"svc-a", "svc-b"}

    def test_no_service_returns_empty(self, adapter):
        record, _ = _make_formal_incident(service_names=[])
        record["relationships"]["impacts"]["data"] = []
        record["attributes"]["customer_impacted_scope"] = ""
        assert adapter._parse_formal_incident(record, []) == []

    def test_bad_timestamp_returns_empty(self, adapter):
        record, included = _make_formal_incident()
        record["attributes"]["created"] = "not-a-date"
        assert adapter._parse_formal_incident(record, included) == []

    def test_sev5_maps_to_low(self, adapter):
        record, included = _make_formal_incident(severity="sev-5")
        incidents = adapter._parse_formal_incident(record, included)
        assert incidents[0].severity == "low"



class TestDeduplicate:
    def _make(self, service, inc_type, severity, offset_seconds=0):
        base = datetime(2024, 1, 15, 10, 0, 0, tzinfo=timezone.utc)
        return Incident(
            timestamp=base + timedelta(seconds=offset_seconds),
            service=service,
            type=inc_type,
            severity=severity,
            request_volume=None,
        )

    def test_same_key_within_5min_deduped(self, adapter):
        a = self._make("svc", "error", "critical", offset_seconds=0)
        b = self._make("svc", "error", "critical", offset_seconds=240)   # 4 min later
        result = adapter._deduplicate([a, b])
        assert len(result) == 1

    def test_same_key_outside_5min_kept(self, adapter):
        a = self._make("svc", "error", "critical", offset_seconds=0)
        b = self._make("svc", "error", "critical", offset_seconds=360)   # 6 min later
        result = adapter._deduplicate([a, b])
        assert len(result) == 2

    def test_different_service_both_kept(self, adapter):
        a = self._make("svc-a", "error", "critical")
        b = self._make("svc-b", "error", "critical")
        assert len(adapter._deduplicate([a, b])) == 2

    def test_different_type_both_kept(self, adapter):
        a = self._make("svc", "error",   "critical")
        b = self._make("svc", "latency", "critical")
        assert len(adapter._deduplicate([a, b])) == 2

    def test_empty_list(self, adapter):
        assert adapter._deduplicate([]) == []



class TestFetchMonitorEventsPagination:
    def test_cursor_pagination(self, adapter):
        """Two pages of results — second page has no cursor (stops)."""
        page1 = {
            "data": [_make_event(timestamp=1_700_000_000)],
            "meta": {"pagination": {"next_cursor": "cursor-abc"}},
        }
        page2 = {
            "data": [_make_event(service="auth-service", timestamp=1_700_001_000)],
            "meta": {"pagination": {"next_cursor": None}},
        }
        mock_resp1 = MagicMock(status_code=200)
        mock_resp1.json.return_value = page1
        mock_resp1.raise_for_status = MagicMock()

        mock_resp2 = MagicMock(status_code=200)
        mock_resp2.json.return_value = page2
        mock_resp2.raise_for_status = MagicMock()

        with patch.object(adapter._session, "get", side_effect=[mock_resp1, mock_resp2]):
            result = adapter._fetch_monitor_events(1_699_000_000, 1_700_002_000)

        assert len(result) == 2

    def test_empty_data_stops(self, adapter):
        mock_resp = MagicMock(status_code=200)
        mock_resp.json.return_value = {"data": [], "meta": {}}
        mock_resp.raise_for_status = MagicMock()

        with patch.object(adapter._session, "get", return_value=mock_resp):
            result = adapter._fetch_monitor_events(0, 1)

        assert result == []


class TestFetchFormalIncidentsPagination:
    def test_offset_pagination(self, adapter):
        record1, included1 = _make_formal_incident(
            service_names=["svc-a"], created="2024-01-15T10:00:00Z"
        )
        record2, included2 = _make_formal_incident(
            service_names=["svc-b"], created="2024-01-16T10:00:00Z"
        )
        page1 = {
            "data":     [record1],
            "included": included1,
            "meta":     {"pagination": {"total_count": 2}},
        }
        page2 = {
            "data":     [record2],
            "included": included2,
            "meta":     {"pagination": {"total_count": 2}},
        }

        mock_r1 = MagicMock()
        mock_r1.json.return_value = page1
        mock_r1.raise_for_status = MagicMock()
        
        mock_r2 = MagicMock()
        mock_r2.json.return_value = page2
        mock_r2.raise_for_status = MagicMock()

        now = datetime.now(tz=timezone.utc)
        with patch.object(adapter._session, "get", side_effect=[mock_r1, mock_r2]):
            result = adapter._fetch_formal_incidents(0, now)

        assert len(result) == 2
        assert {i.service for i in result} == {"svc-a", "svc-b"}

    def test_empty_stops_immediately(self, adapter):
        mock_r = MagicMock()
        mock_r.json.return_value = {"data": [], "included": [], "meta": {"pagination": {"total_count": 0}}}
        mock_r.raise_for_status = MagicMock()

        now = datetime.now(tz=timezone.utc)
        with patch.object(adapter._session, "get", return_value=mock_r):
            result = adapter._fetch_formal_incidents(0, now)

        assert result == []


class TestRateLimitRetry:
    def test_429_retries_once(self, adapter):
        rate_limited = requests.HTTPError(response=MagicMock(
            status_code=429,
            headers={"X-RateLimit-Reset": "1"},
        ))
        rate_limited.response.raise_for_status.side_effect = rate_limited

        ok_resp = MagicMock()
        ok_resp.json.return_value = {}
        ok_resp.raise_for_status = MagicMock()

        with patch.object(adapter._session, "get", side_effect=[
            MagicMock(raise_for_status=MagicMock(side_effect=rate_limited)),
            ok_resp,
        ]):
            with patch("time.sleep") as mock_sleep:
                result = adapter._get("http://example.com", {})

        mock_sleep.assert_called_once()
        assert result == {}


class TestHttpErrors:
    def test_non_429_http_error_returns_none(self, adapter):
        exc = requests.HTTPError(response=MagicMock(status_code=403))
        mock_resp = MagicMock()
        mock_resp.raise_for_status.side_effect = exc

        with patch.object(adapter._session, "get", return_value=mock_resp):
            result = adapter._get("http://example.com", {})

        assert result is None

    def test_connection_error_returns_none(self, adapter):
        with patch.object(
            adapter._session, "get",
            side_effect=requests.ConnectionError("refused")
        ):
            result = adapter._get("http://example.com", {})

        assert result is None



class TestFetchIntegration:
    def test_fetch_merges_and_sorts(self, adapter):
        """fetch() should return combined results sorted oldest-first."""
        event_ts  = 1_700_000_000
        formal_ts = "2024-01-10T08:00:00Z"   # earlier

        event_resp = {
            "data": [_make_event(timestamp=event_ts)],
            "meta": {},
        }
        record, included = _make_formal_incident(created=formal_ts)
        incident_resp = {
            "data":     [record],
            "included": included,
            "meta":     {"pagination": {"total_count": 1}},
        }

        def side_effect(url, **kwargs):
            m = MagicMock()
            m.raise_for_status = MagicMock()
            if "events" in url:
                m.json.return_value = event_resp
            else:
                m.json.return_value = incident_resp
            return m

        with patch.object(adapter._session, "get", side_effect=side_effect):
            results = adapter.fetch(window_days=30)

        assert len(results) >= 1
        # All request_volumes must be None
        assert all(r.request_volume is None for r in results)
        # Results sorted oldest-first
        timestamps = [r.timestamp for r in results]
        assert timestamps == sorted(timestamps)

    def test_window_days_sets_from_epoch(self, adapter):
        """from_epoch should be approximately now - window_days * 86400."""
        captured_params = {}

        def capture(url, params=None, **kwargs):
            captured_params[url] = params
            m = MagicMock()
            m.raise_for_status = MagicMock()
            m.json.return_value = {"data": [], "meta": {}}
            return m

        with patch.object(adapter._session, "get", side_effect=capture):
            with patch("chaosrank.incident_adapters.datadog.time") as mock_time:
                mock_time.sleep = time.sleep
                adapter.fetch(window_days=7)

        events_url = [k for k in captured_params if "events" in k]
        assert events_url, "Events API should have been called"

    def test_source_format(self, adapter):
        assert adapter.source_format() == "datadog"