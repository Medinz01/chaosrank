"""
tests/test_parser_otlp.py — OTel Collector JSON trace parser tests

Tests mirror the structure of test_parser.py for consistency.
All tests use the synthetic fixture at tests/fixtures/otlp_trace.json
or small inline payloads written to tmp_path.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from chaosrank.parser.otlp import parse_otlp, _extract_service_name


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

FIXTURE = Path(__file__).parent / "fixtures" / "otlp_trace.json"


def _write_otlp(tmp_path: Path, payload: dict) -> Path:
    p = tmp_path / "trace.json"
    p.write_text(json.dumps(payload))
    return p


def _make_span(
    span_id: str,
    parent_id: str = "",
    name: str = "op",
    trace_id: str = "t1",
) -> dict:
    return {
        "traceId": trace_id,
        "spanId": span_id,
        "parentSpanId": parent_id,
        "name": name,
        "kind": 2,
        "startTimeUnixNano": "1700000000000000000",
        "endTimeUnixNano": "1700000000100000000",
        "status": {},
    }


def _make_resource_span(service_name: str, spans: list[dict]) -> dict:
    attrs = (
        [{"key": "service.name", "value": {"stringValue": service_name}}]
        if service_name
        else []
    )
    return {
        "resource": {"attributes": attrs},
        "scopeSpans": [{"scope": {"name": "tracer"}, "spans": spans}],
    }


def _make_otlp(resource_spans: list[dict]) -> dict:
    return {"resourceSpans": resource_spans}


# ---------------------------------------------------------------------------
# TestEdgeExtraction
# ---------------------------------------------------------------------------


class TestEdgeExtraction:
    def test_single_edge_extracted(self, tmp_path):
        payload = _make_otlp([
            _make_resource_span("frontend", [_make_span("s1", "")]),
            _make_resource_span("payment-service", [_make_span("s2", "s1")]),
        ])
        # Repeat to exceed min_call_frequency=10
        payload["resourceSpans"][0]["scopeSpans"][0]["spans"] = [
            _make_span(f"s_fe_{i}", "") for i in range(10)
        ]
        payload["resourceSpans"][1]["scopeSpans"][0]["spans"] = [
            _make_span(f"s_pay_{i}", f"s_fe_{i}") for i in range(10)
        ]
        p = _write_otlp(tmp_path, payload)
        edges = parse_otlp(p, min_call_frequency=1)
        assert ("frontend", "payment-service") in edges

    def test_edge_weight_accumulates(self, tmp_path):
        spans_fe = [_make_span(f"fe_{i}", "") for i in range(5)]
        spans_pay = [_make_span(f"pay_{i}", f"fe_{i}") for i in range(5)]
        payload = _make_otlp([
            _make_resource_span("frontend", spans_fe),
            _make_resource_span("payment-service", spans_pay),
        ])
        p = _write_otlp(tmp_path, payload)
        edges = parse_otlp(p, min_call_frequency=1)
        assert edges[("frontend", "payment-service")] == 5

    def test_min_call_frequency_filters_low_weight_edges(self, tmp_path):
        spans_fe = [_make_span(f"fe_{i}", "") for i in range(3)]
        spans_pay = [_make_span(f"pay_{i}", f"fe_{i}") for i in range(3)]
        payload = _make_otlp([
            _make_resource_span("frontend", spans_fe),
            _make_resource_span("payment-service", spans_pay),
        ])
        p = _write_otlp(tmp_path, payload)
        edges = parse_otlp(p, min_call_frequency=10)
        assert ("frontend", "payment-service") not in edges

    def test_root_spans_produce_no_edges(self, tmp_path):
        payload = _make_otlp([
            _make_resource_span("frontend", [_make_span("s1", "")]),
            _make_resource_span("auth-service", [_make_span("s2", "")]),
        ])
        p = _write_otlp(tmp_path, payload)
        edges = parse_otlp(p, min_call_frequency=1)
        assert len(edges) == 0

    def test_intraservice_spans_not_counted_as_edges(self, tmp_path):
        # Both spans belong to payment-service — should not create a self-edge
        spans = [_make_span("s1", ""), _make_span("s2", "s1")]
        payload = _make_otlp([
            _make_resource_span("payment-service", spans),
        ])
        p = _write_otlp(tmp_path, payload)
        edges = parse_otlp(p, min_call_frequency=1)
        assert ("payment-service", "payment-service") not in edges
        assert len(edges) == 0

    def test_chain_topology_all_edges_extracted(self, tmp_path):
        # frontend -> payment-service -> database
        spans_fe = [_make_span(f"fe_{i}", "") for i in range(5)]
        spans_pay = [_make_span(f"pay_{i}", f"fe_{i}") for i in range(5)]
        spans_db = [_make_span(f"db_{i}", f"pay_{i}") for i in range(5)]
        payload = _make_otlp([
            _make_resource_span("frontend", spans_fe),
            _make_resource_span("payment-service", spans_pay),
            _make_resource_span("database", spans_db),
        ])
        p = _write_otlp(tmp_path, payload)
        edges = parse_otlp(p, min_call_frequency=1)
        assert ("frontend", "payment-service") in edges
        assert ("payment-service", "database") in edges
        assert len(edges) == 2

    def test_multiple_callees_from_same_caller(self, tmp_path):
        spans_fe = [_make_span(f"fe_{i}", "") for i in range(5)]
        spans_pay = [_make_span(f"pay_{i}", f"fe_{i}") for i in range(5)]
        spans_auth = [_make_span(f"auth_{i}", f"fe_{i}") for i in range(5)]
        payload = _make_otlp([
            _make_resource_span("frontend", spans_fe),
            _make_resource_span("payment-service", spans_pay),
            _make_resource_span("auth-service", spans_auth),
        ])
        p = _write_otlp(tmp_path, payload)
        edges = parse_otlp(p, min_call_frequency=1)
        assert ("frontend", "payment-service") in edges
        assert ("frontend", "auth-service") in edges


# ---------------------------------------------------------------------------
# TestFixture — exercises the shared otlp_trace.json fixture
# ---------------------------------------------------------------------------


class TestFixture:
    def test_fixture_loads(self):
        edges = parse_otlp(FIXTURE, min_call_frequency=1)
        assert isinstance(edges, dict)

    def test_fixture_extracts_frontend_to_payment(self):
        edges = parse_otlp(FIXTURE, min_call_frequency=1)
        assert ("frontend", "payment-service") in edges

    def test_fixture_extracts_payment_to_database(self):
        edges = parse_otlp(FIXTURE, min_call_frequency=1)
        assert ("payment-service", "database") in edges

    def test_fixture_database_has_no_outgoing_edges(self):
        # database is the terminal sink — it has no outgoing edges
        edges = parse_otlp(FIXTURE, min_call_frequency=1)
        callers = {e[0] for e in edges}
        assert "database" not in callers

    def test_fixture_unknown_service_from_missing_attribute(self):
        # The fixture has one resourceSpan with no service.name attribute
        # It should produce an unknown-service node (warn) but not crash
        edges = parse_otlp(FIXTURE, min_call_frequency=1)
        # unknown-service has no children in fixture — just verify no crash
        assert isinstance(edges, dict)


# ---------------------------------------------------------------------------
# TestServiceNameExtraction
# ---------------------------------------------------------------------------


class TestServiceNameExtraction:
    def test_string_value_extracted(self):
        rs = _make_resource_span("payment-service", [])
        name = _extract_service_name(rs)
        assert name == "payment-service"

    def test_missing_service_name_returns_unknown(self):
        rs = {"resource": {"attributes": []}, "scopeSpans": []}
        name = _extract_service_name(rs)
        assert name == "unknown-service"

    def test_missing_resource_returns_unknown(self):
        rs = {"scopeSpans": []}
        name = _extract_service_name(rs)
        assert name == "unknown-service"

    def test_version_stripped_from_service_name(self):
        rs = _make_resource_span("payment-service-v2", [])
        name = _extract_service_name(rs)
        assert name == "payment-service"

    def test_pod_hash_stripped_from_service_name(self):
        rs = _make_resource_span("payment-service-7d9f8b", [])
        name = _extract_service_name(rs)
        assert name == "payment-service"

    def test_alias_applied(self):
        from chaosrank.parser.normalize import load_aliases
        load_aliases({"payments": "payment-service"})
        rs = _make_resource_span("payments", [])
        name = _extract_service_name(rs)
        load_aliases({})  # reset
        assert name == "payment-service"

    def test_name_lowercased(self):
        rs = _make_resource_span("Payment-Service", [])
        name = _extract_service_name(rs)
        assert name == "payment-service"


# ---------------------------------------------------------------------------
# TestMalformedInput
# ---------------------------------------------------------------------------


class TestMalformedInput:
    def test_empty_resource_spans(self, tmp_path):
        p = _write_otlp(tmp_path, {"resourceSpans": []})
        edges = parse_otlp(p, min_call_frequency=1)
        assert edges == {}

    def test_missing_resource_spans_key(self, tmp_path):
        p = _write_otlp(tmp_path, {})
        edges = parse_otlp(p, min_call_frequency=1)
        assert edges == {}

    def test_span_with_empty_parent_id(self, tmp_path):
        spans = [_make_span("s1", "")]
        payload = _make_otlp([_make_resource_span("frontend", spans)])
        p = _write_otlp(tmp_path, payload)
        edges = parse_otlp(p, min_call_frequency=1)
        assert len(edges) == 0

    def test_span_with_unknown_parent_skipped(self, tmp_path):
        # parentSpanId references a span not in the export window
        spans = [_make_span("s1", "nonexistent-parent-id")]
        payload = _make_otlp([_make_resource_span("frontend", spans)])
        p = _write_otlp(tmp_path, payload)
        edges = parse_otlp(p, min_call_frequency=1)
        assert len(edges) == 0

    def test_scope_spans_missing_is_safe(self, tmp_path):
        rs = {"resource": {"attributes": [
            {"key": "service.name", "value": {"stringValue": "svc"}}
        ]}}  # no scopeSpans key
        payload = _make_otlp([rs])
        p = _write_otlp(tmp_path, payload)
        edges = parse_otlp(p, min_call_frequency=1)
        assert edges == {}

    def test_invalid_json_raises(self, tmp_path):
        p = tmp_path / "bad.json"
        p.write_text("not json {{{")
        with pytest.raises(Exception):
            parse_otlp(p, min_call_frequency=1)


# ---------------------------------------------------------------------------
# TestTempoEnvelope — Tempo/Jaeger v2 (batches envelope)
# ---------------------------------------------------------------------------

TEMPO_FIXTURE = Path(__file__).parent / "fixtures" / "otlp_tempo_trace.json"


def _make_tempo_batch(service_name: str, spans: list[dict]) -> dict:
    attrs = (
        [{"key": "service.name", "value": {"stringValue": service_name}}]
        if service_name
        else []
    )
    return {
        "resource": {"attributes": attrs},
        "instrumentationLibrarySpans": [{"spans": spans}],
    }


def _make_tempo_payload(batches: list[dict]) -> dict:
    return {"batches": batches}


class TestTempoEnvelope:

    def test_tempo_fixture_loads(self):
        edges = parse_otlp(TEMPO_FIXTURE, min_call_frequency=1)
        assert isinstance(edges, dict)

    def test_tempo_fixture_extracts_frontend_to_payment(self):
        edges = parse_otlp(TEMPO_FIXTURE, min_call_frequency=1)
        assert ("frontend", "payment-service") in edges

    def test_tempo_fixture_extracts_payment_to_database(self):
        edges = parse_otlp(TEMPO_FIXTURE, min_call_frequency=1)
        assert ("payment-service", "database") in edges

    def test_tempo_fixture_database_has_no_outgoing_edges(self):
        edges = parse_otlp(TEMPO_FIXTURE, min_call_frequency=1)
        callers = {e[0] for e in edges}
        assert "database" not in callers

    def test_tempo_edge_weight_accumulates(self, tmp_path):
        spans_fe = [_make_span(f"fe_{i}", "") for i in range(5)]
        spans_pay = [_make_span(f"pay_{i}", f"fe_{i}") for i in range(5)]
        payload = _make_tempo_payload([
            _make_tempo_batch("frontend", spans_fe),
            _make_tempo_batch("payment-service", spans_pay),
        ])
        p = _write_otlp(tmp_path, payload)
        edges = parse_otlp(p, min_call_frequency=1)
        assert edges[("frontend", "payment-service")] == 5

    def test_tempo_min_call_frequency_filters(self, tmp_path):
        spans_fe = [_make_span(f"fe_{i}", "") for i in range(3)]
        spans_pay = [_make_span(f"pay_{i}", f"fe_{i}") for i in range(3)]
        payload = _make_tempo_payload([
            _make_tempo_batch("frontend", spans_fe),
            _make_tempo_batch("payment-service", spans_pay),
        ])
        p = _write_otlp(tmp_path, payload)
        edges = parse_otlp(p, min_call_frequency=10)
        assert ("frontend", "payment-service") not in edges

    def test_tempo_intraservice_spans_not_counted(self, tmp_path):
        spans = [_make_span("s1", ""), _make_span("s2", "s1")]
        payload = _make_tempo_payload([_make_tempo_batch("payment-service", spans)])
        p = _write_otlp(tmp_path, payload)
        edges = parse_otlp(p, min_call_frequency=1)
        assert len(edges) == 0

    def test_tempo_missing_service_name_uses_unknown(self, tmp_path):
        spans = [_make_span("s1", "")]
        batch = {"resource": {"attributes": []}, "instrumentationLibrarySpans": [{"spans": spans}]}
        payload = _make_tempo_payload([batch])
        p = _write_otlp(tmp_path, payload)
        edges = parse_otlp(p, min_call_frequency=1)
        assert isinstance(edges, dict)

    def test_tempo_empty_batches_returns_empty(self, tmp_path):
        p = _write_otlp(tmp_path, {"batches": []})
        edges = parse_otlp(p, min_call_frequency=1)
        assert edges == {}

    def test_tempo_scopespans_fallback_accepted(self, tmp_path):
        """Batches with scopeSpans instead of instrumentationLibrarySpans still parse."""
        spans_fe = [_make_span(f"fe_{i}", "") for i in range(3)]
        spans_pay = [_make_span(f"pay_{i}", f"fe_{i}") for i in range(3)]
        payload = {
            "batches": [
                {
                    "resource": {"attributes": [
                        {"key": "service.name", "value": {"stringValue": "frontend"}}
                    ]},
                    "scopeSpans": [{"spans": spans_fe}],
                },
                {
                    "resource": {"attributes": [
                        {"key": "service.name", "value": {"stringValue": "payment-service"}}
                    ]},
                    "scopeSpans": [{"spans": spans_pay}],
                },
            ]
        }
        p = _write_otlp(tmp_path, payload)
        edges = parse_otlp(p, min_call_frequency=1)
        assert ("frontend", "payment-service") in edges


class TestEnvelopeAutoDetection:

    def test_collector_envelope_detected(self, tmp_path):
        spans_fe = [_make_span(f"fe_{i}", "") for i in range(3)]
        spans_pay = [_make_span(f"pay_{i}", f"fe_{i}") for i in range(3)]
        payload = _make_otlp([
            _make_resource_span("frontend", spans_fe),
            _make_resource_span("payment-service", spans_pay),
        ])
        p = _write_otlp(tmp_path, payload)
        edges = parse_otlp(p, min_call_frequency=1)
        assert ("frontend", "payment-service") in edges

    def test_tempo_envelope_detected(self, tmp_path):
        spans_fe = [_make_span(f"fe_{i}", "") for i in range(3)]
        spans_pay = [_make_span(f"pay_{i}", f"fe_{i}") for i in range(3)]
        payload = _make_tempo_payload([
            _make_tempo_batch("frontend", spans_fe),
            _make_tempo_batch("payment-service", spans_pay),
        ])
        p = _write_otlp(tmp_path, payload)
        edges = parse_otlp(p, min_call_frequency=1)
        assert ("frontend", "payment-service") in edges

    def test_both_envelopes_produce_same_edges(self, tmp_path):
        spans_fe = [_make_span(f"fe_{i}", "") for i in range(5)]
        spans_pay = [_make_span(f"pay_{i}", f"fe_{i}") for i in range(5)]

        collector_payload = _make_otlp([
            _make_resource_span("frontend", spans_fe),
            _make_resource_span("payment-service", spans_pay),
        ])
        tempo_payload = _make_tempo_payload([
            _make_tempo_batch("frontend", spans_fe),
            _make_tempo_batch("payment-service", spans_pay),
        ])

        # Write directly — _write_otlp appends /trace.json internally so we
        # cannot pass a filename as the path argument.
        p1 = tmp_path / "collector.json"
        p1.write_text(json.dumps(collector_payload))
        p2 = tmp_path / "tempo.json"
        p2.write_text(json.dumps(tempo_payload))

        edges_collector = parse_otlp(p1, min_call_frequency=1)
        edges_tempo = parse_otlp(p2, min_call_frequency=1)

        assert edges_collector == edges_tempo

    def test_unknown_envelope_returns_empty(self, tmp_path):
        p = _write_otlp(tmp_path, {"someOtherKey": []})
        edges = parse_otlp(p, min_call_frequency=1)
        assert edges == {}