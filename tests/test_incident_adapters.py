"""
Tests for chaosrank/incident_adapters/ — all adapters tested via HTTP mocking.
No network calls are made; urllib.request.urlopen is patched throughout.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch


from chaosrank.incident_adapters.base import infer_type, normalize_severity
from chaosrank.incident_adapters.pagerduty import PagerDutyAdapter
from chaosrank.incident_adapters.alertmanager import AlertmanagerAdapter
from chaosrank.incident_adapters.grafana_oncall import GrafanaOnCallAdapter
from chaosrank.incident_adapters.opsgenie import OpsgenieAdapter
from chaosrank.incident_adapters.csv_export import incidents_to_csv
from chaosrank.parser.incidents import Incident

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

FIXTURES = Path(__file__).parent / "fixtures"


def _mock_urlopen(payload: dict | list):
    """Return a context manager mock whose .read() returns JSON bytes."""
    body = json.dumps(payload).encode("utf-8")
    resp = MagicMock()
    resp.read.return_value = body
    resp.__enter__ = lambda s: s
    resp.__exit__ = MagicMock(return_value=False)
    return resp


def _load_fixture(name: str) -> dict | list:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# TestHelpers — shared functions in base.py
# ---------------------------------------------------------------------------

class TestInferType:
    def test_latency_keyword(self):
        assert infer_type("High latency on payment service") == "latency"

    def test_timeout_keyword(self):
        assert infer_type("Connection timeout exceeded") == "timeout"

    def test_error_keyword(self):
        assert infer_type("Error rate spike") == "error"

    def test_fail_keyword(self):
        assert infer_type("Failed health check") == "error"

    def test_no_match_defaults_to_error(self):
        assert infer_type("Disk space low") == "error"

    def test_case_insensitive(self):
        assert infer_type("LATENCY ALERT") == "latency"


class TestNormalizeSeverity:
    def test_critical(self):
        assert normalize_severity("critical") == "critical"

    def test_warning_maps_to_medium(self):
        assert normalize_severity("warning") == "medium"

    def test_info_maps_to_low(self):
        assert normalize_severity("info") == "low"

    def test_p1_maps_to_critical(self):
        assert normalize_severity("P1") == "critical"

    def test_p3_maps_to_medium(self):
        assert normalize_severity("P3") == "medium"

    def test_unknown_defaults_to_low(self):
        assert normalize_severity("banana") == "low"


# ---------------------------------------------------------------------------
# TestPagerDutyAdapter
# ---------------------------------------------------------------------------

class TestPagerDutyAdapter:
    def _adapter(self):
        return PagerDutyAdapter(api_key="test-token")

    def test_source_format(self):
        assert self._adapter().source_format() == "pagerduty"

    @patch("urllib.request.urlopen")
    def test_fetch_from_fixture(self, mock_urlopen):
        fixture = _load_fixture("pagerduty_incidents.json")
        mock_urlopen.return_value = _mock_urlopen(fixture)
        incidents = self._adapter().fetch(window_days=30)
        # Fixture has 4 items; 1 has no service and should be skipped
        assert len(incidents) == 3

    @patch("urllib.request.urlopen")
    def test_high_unacknowledged_maps_to_critical(self, mock_urlopen):
        fixture = _load_fixture("pagerduty_incidents.json")
        mock_urlopen.return_value = _mock_urlopen(fixture)
        incidents = self._adapter().fetch(window_days=30)
        # First incident: high urgency, no acknowledgements → critical
        payment = next(i for i in incidents if i.service == "payment-service")
        assert payment.severity == "critical"

    @patch("urllib.request.urlopen")
    def test_high_acknowledged_maps_to_high(self, mock_urlopen):
        fixture = _load_fixture("pagerduty_incidents.json")
        mock_urlopen.return_value = _mock_urlopen(fixture)
        incidents = self._adapter().fetch(window_days=30)
        cart = next(i for i in incidents if i.service == "cart-service")
        assert cart.severity == "high"

    @patch("urllib.request.urlopen")
    def test_low_urgency_maps_to_low(self, mock_urlopen):
        fixture = _load_fixture("pagerduty_incidents.json")
        mock_urlopen.return_value = _mock_urlopen(fixture)
        incidents = self._adapter().fetch(window_days=30)
        auth = next(i for i in incidents if i.service == "auth-service")
        assert auth.severity == "low"

    @patch("urllib.request.urlopen")
    def test_type_inferred_from_title(self, mock_urlopen):
        fixture = _load_fixture("pagerduty_incidents.json")
        mock_urlopen.return_value = _mock_urlopen(fixture)
        incidents = self._adapter().fetch(window_days=30)
        payment = next(i for i in incidents if i.service == "payment-service")
        assert payment.type == "latency"
        auth = next(i for i in incidents if i.service == "auth-service")
        assert auth.type == "timeout"

    @patch("urllib.request.urlopen")
    def test_empty_response_returns_empty_list(self, mock_urlopen):
        mock_urlopen.return_value = _mock_urlopen({"incidents": [], "more": False})
        incidents = self._adapter().fetch(window_days=7)
        assert incidents == []

    @patch("urllib.request.urlopen")
    def test_request_volume_is_none(self, mock_urlopen):
        fixture = _load_fixture("pagerduty_incidents.json")
        mock_urlopen.return_value = _mock_urlopen(fixture)
        incidents = self._adapter().fetch(window_days=30)
        assert all(i.request_volume is None for i in incidents)

    @patch("urllib.request.urlopen")
    def test_pagination_follows_more_flag(self, mock_urlopen):
        page1 = {"incidents": [
            {"id": "A", "created_at": "2026-02-10T08:00:00Z",
             "title": "err", "urgency": "low", "acknowledgements": [],
             "service": {"summary": "svc-a"}}
        ], "more": True}
        page2 = {"incidents": [
            {"id": "B", "created_at": "2026-02-11T08:00:00Z",
             "title": "timeout", "urgency": "low", "acknowledgements": [],
             "service": {"summary": "svc-b"}}
        ], "more": False}
        mock_urlopen.side_effect = [_mock_urlopen(page1), _mock_urlopen(page2)]
        incidents = self._adapter().fetch(window_days=30)
        assert len(incidents) == 2
        assert {i.service for i in incidents} == {"svc-a", "svc-b"}


# ---------------------------------------------------------------------------
# TestAlertmanagerAdapter
# ---------------------------------------------------------------------------

class TestAlertmanagerAdapter:
    def _adapter(self):
        return AlertmanagerAdapter(url="http://alertmanager:9093")

    def test_source_format(self):
        assert self._adapter().source_format() == "alertmanager"

    @patch("urllib.request.urlopen")
    def test_fetch_from_fixture(self, mock_urlopen):
        fixture = _load_fixture("alertmanager_alerts.json")
        mock_urlopen.return_value = _mock_urlopen(fixture)
        incidents = self._adapter().fetch(window_days=9999)
        # 4 alerts, 1 has no service label → 3 incidents
        assert len(incidents) == 3

    @patch("urllib.request.urlopen")
    def test_service_label_priority(self, mock_urlopen):
        fixture = _load_fixture("alertmanager_alerts.json")
        mock_urlopen.return_value = _mock_urlopen(fixture)
        incidents = self._adapter().fetch(window_days=9999)
        services = {i.service for i in incidents}
        # payment-service via "service", cart-service via "job", auth-service via "app"
        assert "payment-service" in services
        assert "cart-service" in services
        assert "auth-service" in services

    @patch("urllib.request.urlopen")
    def test_warning_severity_maps_to_medium(self, mock_urlopen):
        fixture = _load_fixture("alertmanager_alerts.json")
        mock_urlopen.return_value = _mock_urlopen(fixture)
        incidents = self._adapter().fetch(window_days=9999)
        payment = next(i for i in incidents if i.service == "payment-service")
        assert payment.severity == "medium"

    @patch("urllib.request.urlopen")
    def test_critical_severity_preserved(self, mock_urlopen):
        fixture = _load_fixture("alertmanager_alerts.json")
        mock_urlopen.return_value = _mock_urlopen(fixture)
        incidents = self._adapter().fetch(window_days=9999)
        cart = next(i for i in incidents if i.service == "cart-service")
        assert cart.severity == "critical"

    @patch("urllib.request.urlopen")
    def test_type_inferred_from_alertname(self, mock_urlopen):
        fixture = _load_fixture("alertmanager_alerts.json")
        mock_urlopen.return_value = _mock_urlopen(fixture)
        incidents = self._adapter().fetch(window_days=9999)
        payment = next(i for i in incidents if i.service == "payment-service")
        assert payment.type == "latency"
        auth = next(i for i in incidents if i.service == "auth-service")
        assert auth.type == "timeout"

    @patch("urllib.request.urlopen")
    def test_window_filters_old_alerts(self, mock_urlopen):
        old_alert = [{
            "labels": {"alertname": "OldError", "service": "old-svc", "severity": "low"},
            "startsAt": "2020-01-01T00:00:00Z",
            "status": {"state": "resolved"}
        }]
        mock_urlopen.return_value = _mock_urlopen(old_alert)
        incidents = self._adapter().fetch(window_days=7)
        assert incidents == []

    @patch("urllib.request.urlopen")
    def test_empty_response_returns_empty_list(self, mock_urlopen):
        mock_urlopen.return_value = _mock_urlopen([])
        incidents = self._adapter().fetch(window_days=7)
        assert incidents == []


# ---------------------------------------------------------------------------
# TestGrafanaOnCallAdapter
# ---------------------------------------------------------------------------

class TestGrafanaOnCallAdapter:
    def _adapter(self):
        return GrafanaOnCallAdapter(url="https://oncall.example.com/oncall", token="test-token")

    def test_source_format(self):
        assert self._adapter().source_format() == "grafana-oncall"

    @patch("urllib.request.urlopen")
    def test_fetch_from_fixture(self, mock_urlopen):
        fixture = _load_fixture("grafana_oncall_alerts.json")
        mock_urlopen.return_value = _mock_urlopen(fixture)
        incidents = self._adapter().fetch(window_days=9999)
        # 3 groups, 1 has no service label → 2 incidents
        assert len(incidents) == 2

    @patch("urllib.request.urlopen")
    def test_service_from_payload_labels(self, mock_urlopen):
        fixture = _load_fixture("grafana_oncall_alerts.json")
        mock_urlopen.return_value = _mock_urlopen(fixture)
        incidents = self._adapter().fetch(window_days=9999)
        services = {i.service for i in incidents}
        assert "payment-service" in services
        assert "cart-service" in services

    @patch("urllib.request.urlopen")
    def test_severity_from_payload_labels(self, mock_urlopen):
        fixture = _load_fixture("grafana_oncall_alerts.json")
        mock_urlopen.return_value = _mock_urlopen(fixture)
        incidents = self._adapter().fetch(window_days=9999)
        payment = next(i for i in incidents if i.service == "payment-service")
        assert payment.severity == "critical"

    @patch("urllib.request.urlopen")
    def test_pagination_follows_next_url(self, mock_urlopen):
        page1 = {"count": 2, "next": "http://oncall/api/v1/alert_groups/?page=2", "results": [
            {"id": "G1", "received_at": "2026-02-10T08:00:00Z", "title": "err",
             "alerts": [{"payload": {"title": "err", "labels": {"service": "svc-a", "severity": "high"}}}]}
        ]}
        page2 = {"count": 2, "next": None, "results": [
            {"id": "G2", "received_at": "2026-02-11T08:00:00Z", "title": "timeout",
             "alerts": [{"payload": {"title": "timeout", "labels": {"service": "svc-b", "severity": "low"}}}]}
        ]}
        mock_urlopen.side_effect = [_mock_urlopen(page1), _mock_urlopen(page2)]
        incidents = self._adapter().fetch(window_days=9999)
        assert len(incidents) == 2

    @patch("urllib.request.urlopen")
    def test_empty_results_returns_empty_list(self, mock_urlopen):
        mock_urlopen.return_value = _mock_urlopen({"count": 0, "next": None, "results": []})
        incidents = self._adapter().fetch(window_days=7)
        assert incidents == []


# ---------------------------------------------------------------------------
# TestOpsgenieAdapter
# ---------------------------------------------------------------------------

class TestOpsgenieAdapter:
    def _adapter(self):
        return OpsgenieAdapter(api_key="test-key")

    def test_source_format(self):
        assert self._adapter().source_format() == "opsgenie"

    @patch("urllib.request.urlopen")
    def test_fetch_from_fixture(self, mock_urlopen):
        fixture = _load_fixture("opsgenie_alerts.json")
        mock_urlopen.return_value = _mock_urlopen(fixture)
        incidents = self._adapter().fetch(window_days=9999)
        # 4 alerts, 1 has no service → 3 incidents
        assert len(incidents) == 3

    @patch("urllib.request.urlopen")
    def test_service_from_tag(self, mock_urlopen):
        fixture = _load_fixture("opsgenie_alerts.json")
        mock_urlopen.return_value = _mock_urlopen(fixture)
        incidents = self._adapter().fetch(window_days=9999)
        payment = next(i for i in incidents if i.service == "payment-service")
        assert payment is not None

    @patch("urllib.request.urlopen")
    def test_service_from_details_fallback(self, mock_urlopen):
        fixture = _load_fixture("opsgenie_alerts.json")
        mock_urlopen.return_value = _mock_urlopen(fixture)
        incidents = self._adapter().fetch(window_days=9999)
        # cart-service comes from details dict, not tags
        cart = next(i for i in incidents if i.service == "cart-service")
        assert cart is not None

    @patch("urllib.request.urlopen")
    def test_p1_maps_to_critical(self, mock_urlopen):
        fixture = _load_fixture("opsgenie_alerts.json")
        mock_urlopen.return_value = _mock_urlopen(fixture)
        incidents = self._adapter().fetch(window_days=9999)
        payment = next(i for i in incidents if i.service == "payment-service")
        assert payment.severity == "critical"

    @patch("urllib.request.urlopen")
    def test_p4_maps_to_low(self, mock_urlopen):
        fixture = _load_fixture("opsgenie_alerts.json")
        mock_urlopen.return_value = _mock_urlopen(fixture)
        incidents = self._adapter().fetch(window_days=9999)
        storage = next(i for i in incidents if i.service == "storage-service")
        assert storage.severity == "low"

    @patch("urllib.request.urlopen")
    def test_type_inferred_from_message(self, mock_urlopen):
        fixture = _load_fixture("opsgenie_alerts.json")
        mock_urlopen.return_value = _mock_urlopen(fixture)
        incidents = self._adapter().fetch(window_days=9999)
        payment = next(i for i in incidents if i.service == "payment-service")
        assert payment.type == "latency"

    @patch("urllib.request.urlopen")
    def test_empty_data_returns_empty_list(self, mock_urlopen):
        mock_urlopen.return_value = _mock_urlopen({"data": [], "paging": {}})
        incidents = self._adapter().fetch(window_days=7)
        assert incidents == []


# ---------------------------------------------------------------------------
# TestCsvExport
# ---------------------------------------------------------------------------

class TestCsvExport:
    def _make_incident(self, service: str = "payment-service", severity: str = "high",
                       incident_type: str = "latency") -> Incident:
        return Incident(
            timestamp=datetime(2026, 2, 10, 8, 0, 0),
            service=service,
            type=incident_type,
            severity=severity,
            request_volume=None,
        )

    def test_writes_csv_to_file(self, tmp_path):
        incidents = [self._make_incident()]
        out = tmp_path / "out.csv"
        count = incidents_to_csv(incidents, out)
        assert count == 1
        content = out.read_text(encoding="utf-8")
        assert "payment-service" in content
        assert "timestamp,service,type,severity,request_volume" in content

    def test_csv_has_correct_columns(self, tmp_path):
        incidents = [self._make_incident()]
        out = tmp_path / "out.csv"
        incidents_to_csv(incidents, out)
        lines = out.read_text().splitlines()
        header = lines[0]
        assert header == "timestamp,service,type,severity,request_volume"

    def test_empty_incidents_returns_zero(self, tmp_path):
        out = tmp_path / "out.csv"
        count = incidents_to_csv([], out)
        assert count == 0

    def test_none_request_volume_writes_empty_string(self, tmp_path):
        incidents = [self._make_incident()]
        out = tmp_path / "out.csv"
        incidents_to_csv(incidents, out)
        row = out.read_text().splitlines()[1]
        assert row.endswith(",")  # empty request_volume at end

    def test_multiple_incidents_all_written(self, tmp_path):
        incidents = [
            self._make_incident("svc-a"),
            self._make_incident("svc-b"),
            self._make_incident("svc-c"),
        ]
        out = tmp_path / "out.csv"
        count = incidents_to_csv(incidents, out)
        assert count == 3
        lines = out.read_text().splitlines()
        assert len(lines) == 4  # header + 3 rows
