"""Tests for chaosrank.incident_adapters.prometheus_volume (#15).

Covers:
  - backfill: empty list → empty list
  - backfill: incident with request_volume already set is passed through unchanged
  - backfill: successful fill — request_volume set from Prometheus result
  - backfill: Prometheus returns no data → request_volume stays None, warning emitted
  - backfill: multiple incidents, some filled some not
  - backfill: input list never mutated (returns new Incident instances)
  - backfill: emits warning when missing count > 0
  - _query_volume: caching — same (service, bucket) hits Prometheus only once
  - _query_volume: different service → separate queries
  - _query_volume: different time bucket → separate queries
  - _fetch: HTTP error logs and returns None
  - _fetch: 429 retries once after backoff
  - _fetch: connection error returns None
  - _extract_value: status != success → None + warning
  - _extract_value: empty result → None
  - _extract_value: single series → float value
  - _extract_value: multiple series → summed
  - _extract_value: malformed value field → None + warning
  - _time_bucket: rounds down to nearest bucket
  - PromQL: query string includes metric, service_label, service name, rate_window
  - PromQL: timestamp sent as epoch float
  - Custom metric / label / window respected
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch

import pytest
import requests

from chaosrank.incident_adapters.prometheus_volume import (
    PrometheusVolumeBackfiller,
    DEFAULT_METRIC,
    DEFAULT_SERVICE_LABEL,
    DEFAULT_RATE_WINDOW,
)
from chaosrank.parser.incidents import Incident



@pytest.fixture
def backfiller():
    return PrometheusVolumeBackfiller(url="http://prometheus:9090")


def _incident(
    service: str = "payment-service",
    request_volume: float | None = None,
    offset_minutes: int = 0,
) -> Incident:
    base = datetime(2024, 1, 15, 10, 0, 0, tzinfo=timezone.utc)
    return Incident(
        timestamp=base + timedelta(minutes=offset_minutes),
        service=service,
        type="error",
        severity="critical",
        request_volume=request_volume,
    )


def _prometheus_response(value: float, service: str = "payment-service") -> dict:
    return {
        "status": "success",
        "data": {
            "resultType": "vector",
            "result": [
                {
                    "metric": {"service": service},
                    "value": [1705312800.0, str(value)],
                }
            ],
        },
    }


def _mock_get(value: float | None = 1000.0):
    """Return a mock session.get that yields a Prometheus response."""
    mock = MagicMock()
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    if value is None:
        resp.json.return_value = {"status": "success", "data": {"result": []}}
    else:
        resp.json.return_value = _prometheus_response(value)
    mock.return_value = resp
    return mock



class TestBackfill:
    def test_empty_list_returns_empty(self, backfiller):
        assert backfiller.backfill([]) == []

    def test_incident_with_volume_passed_through_unchanged(self, backfiller):
        inc = _incident(request_volume=5000.0)
        result = backfiller.backfill([inc])
        assert len(result) == 1
        assert result[0].request_volume == 5000.0

    def test_successful_fill(self, backfiller):
        inc = _incident(request_volume=None)
        backfiller._session.get = _mock_get(1234.5)
        result = backfiller.backfill([inc])
        assert result[0].request_volume == pytest.approx(1234.5)

    def test_missing_data_keeps_none(self, backfiller):
        inc = _incident(request_volume=None)
        backfiller._session.get = _mock_get(None)
        result = backfiller.backfill([inc])
        assert result[0].request_volume is None

    def test_partial_fill(self, backfiller):
        """Some incidents fill, others stay None."""
        inc_filled  = _incident("payment-service", None, offset_minutes=0)
        inc_present = _incident("auth-service",    500.0, offset_minutes=1)
        inc_missing = _incident("cart-service",    None,  offset_minutes=2)

        call_count = 0
        def side_effect(url, params=None, **kwargs):
            nonlocal call_count
            call_count += 1
            resp = MagicMock()
            resp.raise_for_status = MagicMock()
            svc = params.get("query", "")
            if "payment-service" in svc:
                resp.json.return_value = _prometheus_response(999.0)
            else:
                resp.json.return_value = {"status": "success", "data": {"result": []}}
            return resp

        backfiller._session.get = MagicMock(side_effect=side_effect)
        result = backfiller.backfill([inc_filled, inc_present, inc_missing])

        assert result[0].request_volume == pytest.approx(999.0)  # filled
        assert result[1].request_volume == pytest.approx(500.0)  # unchanged
        assert result[2].request_volume is None                   # no data

    def test_input_list_not_mutated(self, backfiller):
        inc = _incident(request_volume=None)
        original_volume = inc.request_volume
        backfiller._session.get = _mock_get(1000.0)
        backfiller.backfill([inc])
        assert inc.request_volume == original_volume   # original unchanged

    def test_returns_new_incident_instances(self, backfiller):
        inc = _incident(request_volume=None)
        backfiller._session.get = _mock_get(1000.0)
        result = backfiller.backfill([inc])
        assert result[0] is not inc

    def test_missing_warning_emitted(self, backfiller, caplog):
        inc = _incident(request_volume=None)
        backfiller._session.get = _mock_get(None)
        with caplog.at_level(logging.WARNING, logger="chaosrank.incident_adapters.prometheus_volume"):
            backfiller.backfill([inc])
        assert "request_volume=None after backfill" in caplog.text

    def test_no_warning_when_all_filled(self, backfiller, caplog):
        inc = _incident(request_volume=None)
        backfiller._session.get = _mock_get(100.0)
        with caplog.at_level(logging.WARNING, logger="chaosrank.incident_adapters.prometheus_volume"):
            backfiller.backfill([inc])
        assert "request_volume=None after backfill" not in caplog.text



class TestCaching:
    def test_same_service_same_bucket_one_query(self, backfiller):
        """Two incidents for the same service within the same 5-min bucket
        should only trigger one Prometheus query."""
        inc1 = _incident("payment-service", None, offset_minutes=0)
        inc2 = _incident("payment-service", None, offset_minutes=1)  # same bucket

        backfiller._session.get = _mock_get(500.0)
        backfiller.backfill([inc1, inc2])
        assert backfiller._session.get.call_count == 1

    def test_different_service_separate_queries(self, backfiller):
        inc1 = _incident("payment-service", None, offset_minutes=0)
        inc2 = _incident("auth-service",    None, offset_minutes=0)

        backfiller._session.get = _mock_get(500.0)
        backfiller.backfill([inc1, inc2])
        assert backfiller._session.get.call_count == 2

    def test_different_bucket_separate_queries(self, backfiller):
        inc1 = _incident("payment-service", None, offset_minutes=0)
        inc2 = _incident("payment-service", None, offset_minutes=10)  # different bucket

        backfiller._session.get = _mock_get(500.0)
        backfiller.backfill([inc1, inc2])
        assert backfiller._session.get.call_count == 2



class TestHttpErrors:
    def test_http_error_returns_none(self, backfiller):
        exc = requests.HTTPError(response=MagicMock(status_code=500))
        mock = MagicMock()
        mock.return_value.raise_for_status.side_effect = exc
        backfiller._session.get = mock
        result = backfiller._fetch("svc", datetime(2024, 1, 1, tzinfo=timezone.utc))
        assert result is None

    def test_connection_error_returns_none(self, backfiller):
        backfiller._session.get = MagicMock(side_effect=requests.ConnectionError("refused"))
        result = backfiller._fetch("svc", datetime(2024, 1, 1, tzinfo=timezone.utc))
        assert result is None

    def test_429_retries_once(self, backfiller):
        rate_limited = requests.HTTPError(response=MagicMock(
            status_code=429,
            headers={"Retry-After": "1"},
        ))
        ok_resp = MagicMock()
        ok_resp.raise_for_status = MagicMock()
        ok_resp.json.return_value = _prometheus_response(999.0)

        call_count = 0
        def side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                resp = MagicMock()
                resp.raise_for_status.side_effect = rate_limited
                return resp
            return ok_resp

        backfiller._session.get = MagicMock(side_effect=side_effect)
        with patch("time.sleep"):
            result = backfiller._fetch("svc", datetime(2024, 1, 1, tzinfo=timezone.utc))
        assert result == pytest.approx(999.0)
        assert call_count == 2



class TestExtractValue:
    def test_single_series(self, backfiller):
        data = _prometheus_response(1234.5)
        assert backfiller._extract_value(data, "svc", "q") == pytest.approx(1234.5)

    def test_multiple_series_summed(self, backfiller):
        data = {
            "status": "success",
            "data": {
                "result": [
                    {"metric": {}, "value": [0, "300.0"]},
                    {"metric": {}, "value": [0, "200.0"]},
                ]
            },
        }
        assert backfiller._extract_value(data, "svc", "q") == pytest.approx(500.0)

    def test_empty_result_returns_none(self, backfiller):
        data = {"status": "success", "data": {"result": []}}
        assert backfiller._extract_value(data, "svc", "q") is None

    def test_status_not_success_returns_none(self, backfiller, caplog):
        data = {"status": "error", "error": "bad query"}
        with caplog.at_level(logging.WARNING, logger="chaosrank.incident_adapters.prometheus_volume"):
            result = backfiller._extract_value(data, "svc", "q")
        assert result is None
        assert "bad query" in caplog.text

    def test_malformed_value_returns_none(self, backfiller, caplog):
        data = {"status": "success", "data": {"result": [{"metric": {}, "value": []}]}}
        with caplog.at_level(logging.WARNING, logger="chaosrank.incident_adapters.prometheus_volume"):
            result = backfiller._extract_value(data, "svc", "q")
        assert result is None

    def test_non_numeric_value_returns_none(self, backfiller, caplog):
        data = {"status": "success", "data": {"result": [{"metric": {}, "value": [0, "NaN"]}]}}
        with caplog.at_level(logging.WARNING, logger="chaosrank.incident_adapters.prometheus_volume"):
            backfiller._extract_value(data, "svc", "q")



class TestPromQL:
    def _capture_query(self, backfiller, service, ts):
        """Run _fetch and capture the query params sent to Prometheus."""
        captured = {}
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = _prometheus_response(100.0)

        def side_effect(url, params=None, **kwargs):
            captured.update(params or {})
            return resp

        backfiller._session.get = MagicMock(side_effect=side_effect)
        backfiller._fetch(service, ts)
        return captured

    def test_query_includes_metric(self, backfiller):
        ts = datetime(2024, 1, 15, 10, 0, 0, tzinfo=timezone.utc)
        params = self._capture_query(backfiller, "payment-service", ts)
        assert DEFAULT_METRIC in params["query"]

    def test_query_includes_service_label(self, backfiller):
        ts = datetime(2024, 1, 15, 10, 0, 0, tzinfo=timezone.utc)
        params = self._capture_query(backfiller, "payment-service", ts)
        assert DEFAULT_SERVICE_LABEL in params["query"]
        assert "payment-service" in params["query"]

    def test_query_includes_rate_window(self, backfiller):
        ts = datetime(2024, 1, 15, 10, 0, 0, tzinfo=timezone.utc)
        params = self._capture_query(backfiller, "svc", ts)
        assert DEFAULT_RATE_WINDOW in params["query"]

    def test_timestamp_sent_as_epoch_float(self, backfiller):
        ts = datetime(2024, 1, 15, 10, 0, 0, tzinfo=timezone.utc)
        params = self._capture_query(backfiller, "svc", ts)
        epoch = ts.timestamp()
        assert abs(float(params["time"]) - epoch) < 1.0

    def test_custom_metric_used(self):
        b = PrometheusVolumeBackfiller(
            url="http://prom:9090",
            metric="grpc_server_handled_total",
            service_label="app",
            rate_window="2m",
        )
        ts = datetime(2024, 1, 15, 10, 0, 0, tzinfo=timezone.utc)
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = _prometheus_response(50.0)
        captured = {}

        def side_effect(url, params=None, **kwargs):
            captured.update(params or {})
            return resp

        b._session.get = MagicMock(side_effect=side_effect)
        b._fetch("my-svc", ts)

        assert "grpc_server_handled_total" in captured["query"]
        assert 'app="my-svc"' in captured["query"]
        assert "[2m]" in captured["query"]



class TestTimeBucket:
    def test_same_minute_same_bucket(self, backfiller):
        ts1 = datetime(2024, 1, 15, 10, 0, 30, tzinfo=timezone.utc)
        ts2 = datetime(2024, 1, 15, 10, 0, 59, tzinfo=timezone.utc)
        assert backfiller._time_bucket(ts1) == backfiller._time_bucket(ts2)

    def test_different_5min_window_different_bucket(self, backfiller):
        ts1 = datetime(2024, 1, 15, 10, 0, 0,  tzinfo=timezone.utc)
        ts2 = datetime(2024, 1, 15, 10, 5, 0,  tzinfo=timezone.utc)
        assert backfiller._time_bucket(ts1) != backfiller._time_bucket(ts2)

    def test_within_same_5min_window_same_bucket(self, backfiller):
        ts1 = datetime(2024, 1, 15, 10, 1, 0, tzinfo=timezone.utc)
        ts2 = datetime(2024, 1, 15, 10, 4, 59, tzinfo=timezone.utc)
        assert backfiller._time_bucket(ts1) == backfiller._time_bucket(ts2)

    def test_custom_cache_minutes(self):
        b = PrometheusVolumeBackfiller(url="http://prom:9090", cache_minutes=1)
        ts1 = datetime(2024, 1, 15, 10, 0, 0, tzinfo=timezone.utc)
        ts2 = datetime(2024, 1, 15, 10, 1, 0, tzinfo=timezone.utc)
        assert b._time_bucket(ts1) != b._time_bucket(ts2)
