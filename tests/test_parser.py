import json
import logging
import textwrap
from datetime import datetime
from pathlib import Path

import pytest

from chaosrank.parser.normalize import load_aliases, normalize
from chaosrank.parser.jaeger import parse_traces
from chaosrank.parser.incidents import (
    Incident,
    ServiceIncidents,
    parse_incidents,
)


def write_csv(tmp_path: Path, content: str) -> Path:
    """Write a dedented CSV string to a temp file."""
    p = tmp_path / "incidents.csv"
    p.write_text(textwrap.dedent(content).strip())
    return p


def write_jaeger(tmp_path: Path, data: dict) -> Path:
    """Write a Jaeger JSON dict to a temp file."""
    p = tmp_path / "traces.json"
    p.write_text(json.dumps(data))
    return p


def make_jaeger_trace(spans: list[dict], processes: dict, trace_id: str = "trace-001") -> dict:
    """Construct a minimal Jaeger JSON export structure."""
    return {"data": [{"traceID": trace_id, "spans": spans, "processes": processes}]}


def make_span(span_id: str, process_id: str, parent_id: str | None = None) -> dict:
    """Construct a minimal Jaeger span dict."""
    span = {"spanID": span_id, "processID": process_id, "references": []}
    if parent_id:
        span["references"] = [{"refType": "CHILD_OF", "spanID": parent_id}]
    return span


class TestNormalize:

    def setup_method(self):
        load_aliases({})

    def test_lowercase(self):
        assert normalize("Payment-Service") == "payment-service"

    def test_strips_version_suffix(self):
        assert normalize("payment-service-v2") == "payment-service"

    def test_strips_multi_part_version(self):
        assert normalize("payment-service-v1.2.3") == "payment-service"

    def test_strips_semver(self):
        assert normalize("payment-service-1.2.3") == "payment-service"

    def test_strips_pod_hash(self):
        assert normalize("payment-service-7d9f8b") == "payment-service"

    def test_strips_longer_pod_hash(self):
        assert normalize("payment-service-7d9f8b1234") == "payment-service"

    def test_combined_version_and_hash(self):
        assert normalize("payment-service-v2-7d9f8b") == "payment-service"

    def test_alias_applied_after_stripping(self):
        load_aliases({"payments": "payment-service"})
        assert normalize("payments-v2-abc123") == "payment-service"

    def test_alias_exact_match(self):
        load_aliases({"auth": "authentication-service"})
        assert normalize("auth") == "authentication-service"

    def test_alias_case_insensitive(self):
        load_aliases({"AUTH": "authentication-service"})
        assert normalize("AUTH") == "authentication-service"

    def test_empty_string_returns_none(self):
        assert normalize("") is None

    def test_whitespace_only_returns_none(self):
        assert normalize("   ") is None

    def test_no_stripping_needed(self):
        assert normalize("payment-service") == "payment-service"

    def test_normalization_round_trip(self):
        """OTel exporters emit versioned + hashed names; all variants must collapse to one canonical node."""
        canonical = normalize("payment-service")
        for variant in [
            "payment-service",
            "payment-service-v2",
            "payment-service-v2-7d9f8b",
            "payment-service-1.2.3",
            "Payment-Service-v2-abc12f",
        ]:
            assert normalize(variant) == canonical, (
                f"Variant '{variant}' did not normalize to '{canonical}': got '{normalize(variant)}'"
            )


class TestIncidentParsing:

    def test_basic_parse(self, tmp_path):
        csv = write_csv(tmp_path, """
            timestamp,service,type,severity,request_volume
            2024-01-15T10:00:00Z,payment-service,error,critical,5000
        """)
        result = parse_incidents(csv)
        assert "payment-service" in result
        inc = result["payment-service"].incidents[0]
        assert inc.type == "error"
        assert inc.severity == "critical"
        assert inc.request_volume == 5000.0

    def test_service_names_lowercased(self, tmp_path):
        csv = write_csv(tmp_path, """
            timestamp,service,type,severity,request_volume
            2024-01-15T10:00:00Z,Payment-Service,error,high,1000
        """)
        result = parse_incidents(csv)
        assert "payment-service" in result
        assert "Payment-Service" not in result

    def test_missing_request_volume_is_none(self, tmp_path):
        csv = write_csv(tmp_path, """
            timestamp,service,type,severity,request_volume
            2024-01-15T10:00:00Z,payment-service,error,high,
        """)
        result = parse_incidents(csv)
        assert result["payment-service"].incidents[0].request_volume is None

    def test_invalid_request_volume_is_none_with_warning(self, tmp_path, caplog):
        csv = write_csv(tmp_path, """
            timestamp,service,type,severity,request_volume
            2024-01-15T10:00:00Z,payment-service,error,high,not-a-number
        """)
        with caplog.at_level(logging.WARNING):
            result = parse_incidents(csv)
        assert result["payment-service"].incidents[0].request_volume is None
        assert "invalid request_volume" in caplog.text.lower()

    def test_malformed_row_skipped_valid_rows_unaffected(self, tmp_path, caplog):
        csv = write_csv(tmp_path, """
            timestamp,service,type,severity,request_volume
            INVALID-DATE,payment-service,error,high,1000
            2024-01-15T10:00:00Z,auth-service,latency,medium,500
        """)
        with caplog.at_level(logging.WARNING):
            result = parse_incidents(csv)
        assert "payment-service" not in result
        assert "auth-service" in result

    def test_multiple_incidents_same_service(self, tmp_path):
        csv = write_csv(tmp_path, """
            timestamp,service,type,severity,request_volume
            2024-01-15T10:00:00Z,payment-service,error,critical,5000
            2024-01-16T10:00:00Z,payment-service,latency,high,4500
            2024-01-17T10:00:00Z,payment-service,timeout,medium,4800
        """)
        result = parse_incidents(csv)
        assert len(result["payment-service"].incidents) == 3

    def test_multiple_services_parsed(self, tmp_path):
        csv = write_csv(tmp_path, """
            timestamp,service,type,severity,request_volume
            2024-01-15T10:00:00Z,payment-service,error,critical,5000
            2024-01-15T10:00:00Z,auth-service,latency,high,2000
            2024-01-15T10:00:00Z,cart-service,timeout,medium,1000
        """)
        result = parse_incidents(csv)
        assert set(result.keys()) == {"payment-service", "auth-service", "cart-service"}


class TestTimestampFormats:
    """All supported timestamp formats must parse without error."""

    def _parse_one(self, tmp_path, timestamp_str):
        csv = write_csv(tmp_path, f"""
            timestamp,service,type,severity,request_volume
            {timestamp_str},payment-service,error,high,1000
        """)
        result = parse_incidents(csv)
        assert "payment-service" in result, f"Failed to parse timestamp: {timestamp_str}"
        return result["payment-service"].incidents[0].timestamp

    def test_iso8601_z(self, tmp_path):
        self._parse_one(tmp_path, "2024-01-15T10:00:00Z")

    def test_iso8601_no_z(self, tmp_path):
        self._parse_one(tmp_path, "2024-01-15T10:00:00")

    def test_space_separated(self, tmp_path):
        self._parse_one(tmp_path, "2024-01-15 10:00:00")

    def test_iso8601_microseconds_z(self, tmp_path):
        self._parse_one(tmp_path, "2024-01-15T10:00:00.123456Z")

    def test_iso8601_microseconds_no_z(self, tmp_path):
        self._parse_one(tmp_path, "2024-01-15T10:00:00.123456")


class TestAsyncServiceWarning:

    def test_kafka_service_emits_warning(self, tmp_path, caplog):
        csv = write_csv(tmp_path, """
            timestamp,service,type,severity,request_volume
            2024-01-15T10:00:00Z,kafka-broker,error,high,1000
        """)
        with caplog.at_level(logging.WARNING):
            parse_incidents(csv)
        assert "async" in caplog.text.lower()

    def test_non_async_service_no_warning(self, tmp_path, caplog):
        csv = write_csv(tmp_path, """
            timestamp,service,type,severity,request_volume
            2024-01-15T10:00:00Z,payment-service,error,high,1000
        """)
        with caplog.at_level(logging.WARNING):
            parse_incidents(csv)
        assert "async" not in caplog.text.lower()

    @pytest.mark.parametrize("service", ["kafka", "sqs", "rabbitmq", "pubsub", "nats", "kinesis"])
    def test_all_async_patterns_detected(self, tmp_path, caplog, service):
        csv = write_csv(tmp_path, f"""
            timestamp,service,type,severity,request_volume
            2024-01-15T10:00:00Z,{service}-broker,error,high,1000
        """)
        with caplog.at_level(logging.WARNING):
            parse_incidents(csv)
        assert "async" in caplog.text.lower(), f"No async warning for service: {service}"


class TestServiceIncidentsHelper:

    def test_mean_request_volume(self):
        si = ServiceIncidents(service="svc")
        si.incidents = [
            Incident(timestamp=datetime.utcnow(), service="svc", type="error", severity="high", request_volume=v)
            for v in [1000.0, 2000.0, 3000.0]
        ]
        assert si.mean_request_volume == pytest.approx(2000.0)

    def test_mean_request_volume_skips_none(self):
        si = ServiceIncidents(service="svc")
        si.incidents = [
            Incident(timestamp=datetime.utcnow(), service="svc", type="error", severity="high", request_volume=v)
            for v in [1000.0, None, 3000.0]
        ]
        assert si.mean_request_volume == pytest.approx(2000.0)

    def test_mean_request_volume_all_none_returns_none(self):
        si = ServiceIncidents(service="svc")
        si.incidents = [
            Incident(timestamp=datetime.utcnow(), service="svc", type="error", severity="high", request_volume=None)
        ]
        assert si.mean_request_volume is None


class TestJaegerParsing:

    def test_basic_caller_callee_edge(self, tmp_path):
        trace = make_jaeger_trace(
            spans=[
                make_span("span-1", "p1"),
                make_span("span-2", "p2", parent_id="span-1"),
            ],
            processes={
                "p1": {"serviceName": "frontend"},
                "p2": {"serviceName": "payment-service"},
            },
        )
        edges = parse_traces(write_jaeger(tmp_path, trace), min_call_frequency=1)
        assert ("frontend", "payment-service") in edges

    def test_edge_weight_counts_calls(self, tmp_path):
        spans = [make_span("root", "p1")]
        for i in range(5):
            spans.append(make_span(f"child-{i}", "p2", parent_id="root"))
        trace = make_jaeger_trace(
            spans=spans,
            processes={
                "p1": {"serviceName": "frontend"},
                "p2": {"serviceName": "payment-service"},
            },
        )
        edges = parse_traces(write_jaeger(tmp_path, trace), min_call_frequency=1)
        assert edges[("frontend", "payment-service")] == 5

    def test_min_call_frequency_filters_low_edges(self, tmp_path):
        spans = [
            make_span("root",    "p1"),
            make_span("child-1", "p2", parent_id="root"),
            make_span("child-2", "p2", parent_id="root"),
        ]
        trace = make_jaeger_trace(
            spans=spans,
            processes={
                "p1": {"serviceName": "frontend"},
                "p2": {"serviceName": "payment-service"},
            },
        )
        path = write_jaeger(tmp_path, trace)
        assert ("frontend", "payment-service") not in parse_traces(path, min_call_frequency=5)
        assert ("frontend", "payment-service") in     parse_traces(path, min_call_frequency=1)

    def test_intra_service_spans_not_added(self, tmp_path):
        trace = make_jaeger_trace(
            spans=[
                make_span("span-1", "p1"),
                make_span("span-2", "p1", parent_id="span-1"),
            ],
            processes={"p1": {"serviceName": "payment-service"}},
        )
        edges = parse_traces(write_jaeger(tmp_path, trace), min_call_frequency=1)
        assert ("payment-service", "payment-service") not in edges

    def test_multi_hop_chain_produces_all_edges(self, tmp_path):
        trace = make_jaeger_trace(
            spans=[
                make_span("s1", "p1"),
                make_span("s2", "p2", parent_id="s1"),
                make_span("s3", "p3", parent_id="s2"),
            ],
            processes={
                "p1": {"serviceName": "frontend"},
                "p2": {"serviceName": "payment-service"},
                "p3": {"serviceName": "db"},
            },
        )
        edges = parse_traces(write_jaeger(tmp_path, trace), min_call_frequency=1)
        assert ("frontend", "payment-service") in edges
        assert ("payment-service", "db") in edges

    def test_empty_trace_returns_empty_edges(self, tmp_path):
        edges = parse_traces(write_jaeger(tmp_path, {"data": []}), min_call_frequency=1)
        assert edges == {}

    def test_phantom_node_warning_emitted(self, tmp_path, caplog):
        trace = make_jaeger_trace(
            spans=[make_span("span-1", "p1")],
            processes={"p1": {"serviceName": "one-shot-service"}},
        )
        with caplog.at_level(logging.WARNING):
            parse_traces(write_jaeger(tmp_path, trace), min_call_frequency=1)
        assert "phantom" in caplog.text.lower()


class TestJaegerNormalizationRoundTrip:
    """Versioned service names in trace data must produce canonical graph nodes — not phantom edges."""

    def test_versioned_names_collapse_to_single_edge(self, tmp_path):
        trace = make_jaeger_trace(
            spans=[
                make_span("span-1", "p1"),
                make_span("span-2", "p2", parent_id="span-1"),
            ],
            processes={
                "p1": {"serviceName": "frontend-v3-abc123"},
                "p2": {"serviceName": "payment-service-v2-7d9f8b"},
            },
        )
        edges = parse_traces(write_jaeger(tmp_path, trace), min_call_frequency=1)

        assert ("frontend", "payment-service") in edges, (
            f"Versioned names not normalized. Edges: {list(edges.keys())}"
        )
        for caller, callee in edges:
            assert "v2" not in caller and "v3" not in caller, f"Raw version in caller: {caller}"
            assert "v2" not in callee and "v3" not in callee, f"Raw version in callee: {callee}"

    def test_same_service_different_versions_merged(self, tmp_path):
        """payment-service-v1 and payment-service-v2 called by frontend should produce one edge with weight=2."""
        trace = make_jaeger_trace(
            spans=[
                make_span("root-1",  "p1"),
                make_span("child-1", "p2", parent_id="root-1"),
                make_span("root-2",  "p1"),
                make_span("child-2", "p3", parent_id="root-2"),
            ],
            processes={
                "p1": {"serviceName": "frontend"},
                "p2": {"serviceName": "payment-service-v1"},
                "p3": {"serviceName": "payment-service-v2"},
            },
        )
        edges = parse_traces(write_jaeger(tmp_path, trace), min_call_frequency=1)

        assert ("frontend", "payment-service") in edges
        assert edges[("frontend", "payment-service")] == 2, (
            f"Versions should merge into one edge with weight 2. Edges: {edges}"
        )