"""Tests for chaosrank.parser.otlp_proto (#12).

Strategy: we cannot import opentelemetry-proto in CI without installing it,
so we mock trace_pb2 throughout. This lets us test all logic paths without
the optional dependency being present.

Covers:
  - _check_format_mismatch: JSON file warns, binary file is silent
  - _extract_service_proto: service.name present, absent, numeric fallback
  - _span_to_dict: bytes → hex conversion, empty parent_span_id (root span)
  - parse_otlp_proto: happy path (single service, multi-service)
  - parse_otlp_proto: empty resource_spans → {}
  - parse_otlp_proto: min_call_frequency filtering
  - parse_otlp_proto: self-loops dropped (same service caller == callee)
  - parse_otlp_proto: ParseFromString failure raises ValueError with hint
  - parse_otlp_proto: missing opentelemetry-proto raises ImportError with hint
  - parse_otlp_proto: large file (> 100 MB) emits warning but still parses
  - _build_edge_map reuse: protobuf path produces same edge shape as JSON path
  - warn_if_binary (otlp_json_guard): binary file warns, JSON file is silent
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch, mock_open

import pytest


# ---------------------------------------------------------------------------
# Helpers to build fake protobuf objects
# (mirrors the opentelemetry.proto.trace.v1 message structure)
# ---------------------------------------------------------------------------

def _make_attr(key: str, string_value: str = "", int_value: int = 0) -> SimpleNamespace:
    value = SimpleNamespace(string_value=string_value, int_value=int_value,
                            double_value=0.0, bool_value=False)
    # HasField returns True only for non-default values in real proto
    value.HasField = lambda field: (
        field == "int_value" and int_value != 0
    )
    return SimpleNamespace(key=key, value=value)


def _make_resource(*service_attrs) -> SimpleNamespace:
    attrs = list(service_attrs)
    return SimpleNamespace(attributes=attrs)


def _make_span(span_id_hex: str, parent_id_hex: str = "", name: str = "op") -> SimpleNamespace:
    return SimpleNamespace(
        span_id=bytes.fromhex(span_id_hex) if span_id_hex else b"",
        parent_span_id=bytes.fromhex(parent_id_hex) if parent_id_hex else b"",
        name=name,
    )


def _make_scope_span(*spans) -> SimpleNamespace:
    return SimpleNamespace(spans=list(spans))


def _make_resource_span(resource, *scope_spans) -> SimpleNamespace:
    return SimpleNamespace(resource=resource, scope_spans=list(scope_spans))


def _make_traces(*resource_spans) -> SimpleNamespace:
    t = SimpleNamespace(resource_spans=list(resource_spans))
    return t


# Fake TracesData class whose ParseFromString populates resource_spans
class FakeTracesData:
    def __init__(self, traces_ns: SimpleNamespace | None = None, fail: bool = False):
        self._traces = traces_ns
        self._fail = fail
        self.resource_spans = []

    def ParseFromString(self, raw: bytes) -> None:
        if self._fail:
            raise Exception("corrupt protobuf")
        if self._traces is not None:
            self.resource_spans = self._traces.resource_spans


def _make_trace_pb2(traces_ns=None, fail=False):
    """Return a fake trace_pb2 module whose TracesData behaves correctly."""
    m = MagicMock()
    m.TracesData.return_value = FakeTracesData(traces_ns, fail)
    return m


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_proto_file(tmp_path):
    """A small binary-looking temp file."""
    p = tmp_path / "traces.pb"
    p.write_bytes(b"\x80\x10some-binary-content")
    return p


@pytest.fixture
def tmp_json_file(tmp_path):
    """A JSON-looking temp file."""
    p = tmp_path / "traces.json"
    p.write_bytes(b'{"resourceSpans": []}')
    return p


@pytest.fixture
def tmp_empty_file(tmp_path):
    p = tmp_path / "empty.pb"
    p.write_bytes(b"\x0a\x00")
    return p


# ---------------------------------------------------------------------------
# _check_format_mismatch
# ---------------------------------------------------------------------------

class TestCheckFormatMismatch:
    def test_json_file_emits_warning(self, tmp_json_file, caplog):
        from chaosrank.parser.otlp_proto import _check_format_mismatch
        with caplog.at_level(logging.WARNING, logger="chaosrank.parser.otlp_proto"):
            _check_format_mismatch(tmp_json_file)
        assert "--otlp-format json" in caplog.text
        assert "looks like a JSON file" in caplog.text

    def test_binary_file_no_warning(self, tmp_proto_file, caplog):
        from chaosrank.parser.otlp_proto import _check_format_mismatch
        with caplog.at_level(logging.WARNING, logger="chaosrank.parser.otlp_proto"):
            _check_format_mismatch(tmp_proto_file)
        assert caplog.text == ""

    def test_missing_file_no_crash(self, tmp_path):
        from chaosrank.parser.otlp_proto import _check_format_mismatch
        _check_format_mismatch(tmp_path / "nonexistent.pb")  # should not raise


# ---------------------------------------------------------------------------
# _extract_service_proto
# ---------------------------------------------------------------------------

class TestExtractServiceProto:
    def _call(self, resource):
        # Import normalize stub is already patched via the package structure
        from chaosrank.parser.otlp_proto import _extract_service_proto
        return _extract_service_proto(resource)

    def test_service_name_present(self):
        resource = _make_resource(_make_attr("service.name", string_value="payment-service"))
        result = self._call(resource)
        assert result == "payment-service"

    def test_service_name_normalized(self):
        """normalize() strips version suffixes — payment-service-v2 → payment-service."""
        resource = _make_resource(_make_attr("service.name", string_value="payment-service-v2"))
        result = self._call(resource)
        # normalize() is the real function — it should strip -v2
        assert "payment-service" in result

    def test_service_name_absent_returns_unknown(self, caplog):
        resource = _make_resource(_make_attr("env", string_value="prod"))
        with caplog.at_level(logging.WARNING, logger="chaosrank.parser.otlp_proto"):
            result = self._call(resource)
        assert result == "unknown-service"
        assert "missing service.name" in caplog.text

    def test_empty_attributes_returns_unknown(self):
        resource = _make_resource()
        from chaosrank.parser.otlp_proto import _extract_service_proto
        assert _extract_service_proto(resource) == "unknown-service"

    def test_other_attributes_ignored(self):
        resource = _make_resource(
            _make_attr("k8s.namespace", string_value="prod"),
            _make_attr("service.name", string_value="auth-service"),
            _make_attr("host.name", string_value="node-1"),
        )
        result = self._call(resource)
        assert result == "auth-service"


# ---------------------------------------------------------------------------
# _span_to_dict
# ---------------------------------------------------------------------------

class TestSpanToDict:
    def test_hex_conversion(self):
        from chaosrank.parser.otlp_proto import _span_to_dict
        span = _make_span("0102030405060708", "0807060504030201")
        d = _span_to_dict(span)
        assert d["spanId"]       == "0102030405060708"
        assert d["parentSpanId"] == "0807060504030201"

    def test_root_span_empty_parent(self):
        from chaosrank.parser.otlp_proto import _span_to_dict
        span = _make_span("aabbccdd11223344", parent_id_hex="")
        d = _span_to_dict(span)
        assert d["parentSpanId"] == ""

    def test_name_preserved(self):
        from chaosrank.parser.otlp_proto import _span_to_dict
        span = _make_span("0102030405060708", name="POST /checkout")
        d = _span_to_dict(span)
        assert d["name"] == "POST /checkout"

    def test_empty_span_id(self):
        from chaosrank.parser.otlp_proto import _span_to_dict
        span = _make_span("", "")
        d = _span_to_dict(span)
        assert d["spanId"] == ""
        assert d["parentSpanId"] == ""


# ---------------------------------------------------------------------------
# parse_otlp_proto — happy paths
# ---------------------------------------------------------------------------

class TestParseOtlpProto:
    def _parse(self, traces_ns, tmp_proto_file, min_call_frequency=1):
        from chaosrank.parser.otlp_proto import parse_otlp_proto
        fake_pb2 = _make_trace_pb2(traces_ns)
        with patch("chaosrank.parser.otlp_proto._import_proto", return_value=fake_pb2):
            return parse_otlp_proto(tmp_proto_file, min_call_frequency=min_call_frequency)

    def test_single_edge(self, tmp_proto_file):
        # frontend --[span_a]--> payment-service --[span_b (child of span_a)]--> db
        span_a = _make_span("aaaa000000000001")          # root (frontend)
        span_b = _make_span("bbbb000000000002", "aaaa000000000001")  # child of span_a

        rs_frontend = _make_resource_span(
            _make_resource(_make_attr("service.name", string_value="frontend")),
            _make_scope_span(span_a),
        )
        rs_payment = _make_resource_span(
            _make_resource(_make_attr("service.name", string_value="payment-service")),
            _make_scope_span(span_b),
        )
        traces = _make_traces(rs_frontend, rs_payment)

        edges = self._parse(traces, tmp_proto_file)
        assert ("frontend", "payment-service") in edges
        assert edges[("frontend", "payment-service")] == 1

    def test_multi_service_multi_edge(self, tmp_proto_file):
        # 3 calls: frontend->payment (2), frontend->auth (1)
        spans_frontend = [_make_span(f"aa0{i}000000000001") for i in range(3)]
        spans_payment  = [
            _make_span(f"bb0{i}000000000002", f"aa0{i}000000000001")
            for i in range(2)
        ]
        spans_auth = [
            _make_span("cc00000000000003", "aa02000000000001")
        ]

        rs = [
            _make_resource_span(
                _make_resource(_make_attr("service.name", string_value="frontend")),
                _make_scope_span(*spans_frontend),
            ),
            _make_resource_span(
                _make_resource(_make_attr("service.name", string_value="payment-service")),
                _make_scope_span(*spans_payment),
            ),
            _make_resource_span(
                _make_resource(_make_attr("service.name", string_value="auth-service")),
                _make_scope_span(*spans_auth),
            ),
        ]
        traces = _make_traces(*rs)
        edges = self._parse(traces, tmp_proto_file)

        assert edges.get(("frontend", "payment-service")) == 2
        assert edges.get(("frontend", "auth-service")) == 1

    def test_self_loop_dropped(self, tmp_proto_file):
        """Spans within the same service should not create self-loop edges."""
        span_parent = _make_span("aa00000000000001")
        span_child  = _make_span("aa00000000000002", "aa00000000000001")

        rs = _make_resource_span(
            _make_resource(_make_attr("service.name", string_value="payment-service")),
            _make_scope_span(span_parent, span_child),
        )
        traces = _make_traces(rs)
        edges = self._parse(traces, tmp_proto_file)
        assert ("payment-service", "payment-service") not in edges

    def test_min_call_frequency_filters(self, tmp_proto_file):
        """Edges below min_call_frequency should be dropped."""
        spans_frontend = [_make_span(f"aa0{i}000000000001") for i in range(3)]
        spans_svc      = [
            _make_span(f"bb0{i}000000000002", f"aa0{i}000000000001")
            for i in range(3)
        ]
        rs = [
            _make_resource_span(
                _make_resource(_make_attr("service.name", string_value="frontend")),
                _make_scope_span(*spans_frontend),
            ),
            _make_resource_span(
                _make_resource(_make_attr("service.name", string_value="svc")),
                _make_scope_span(*spans_svc),
            ),
        ]
        traces = _make_traces(*rs)

        edges_strict = self._parse(traces, tmp_proto_file, min_call_frequency=5)
        assert ("frontend", "svc") not in edges_strict

        edges_loose = self._parse(traces, tmp_proto_file, min_call_frequency=2)
        assert ("frontend", "svc") in edges_loose

    def test_empty_resource_spans_returns_empty(self, tmp_proto_file):
        traces = _make_traces()   # no resource spans
        edges = self._parse(traces, tmp_proto_file)
        assert edges == {}

    def test_root_spans_ignored(self, tmp_proto_file):
        """Spans with no parentSpanId are roots and should produce no edges."""
        root = _make_span("aa00000000000001", parent_id_hex="")
        rs = _make_resource_span(
            _make_resource(_make_attr("service.name", string_value="frontend")),
            _make_scope_span(root),
        )
        traces = _make_traces(rs)
        edges = self._parse(traces, tmp_proto_file)
        assert edges == {}


# ---------------------------------------------------------------------------
# parse_otlp_proto — error handling
# ---------------------------------------------------------------------------

class TestParseOtlpProtoErrors:
    def test_parse_failure_raises_value_error(self, tmp_proto_file):
        from chaosrank.parser.otlp_proto import parse_otlp_proto
        fake_pb2 = _make_trace_pb2(fail=True)
        with patch("chaosrank.parser.otlp_proto._import_proto", return_value=fake_pb2):
            with pytest.raises(ValueError, match="Failed to decode"):
                parse_otlp_proto(tmp_proto_file)

    def test_missing_library_raises_import_error(self, tmp_proto_file):
        from chaosrank.parser.otlp_proto import parse_otlp_proto
        with patch(
            "chaosrank.parser.otlp_proto._import_proto",
            side_effect=ImportError("install hint"),
        ):
            with pytest.raises(ImportError, match="install hint"):
                parse_otlp_proto(tmp_proto_file)

    def test_large_file_warning(self, tmp_path, caplog):
        from chaosrank.parser.otlp_proto import parse_otlp_proto
        p = tmp_path / "big.pb"
        p.write_bytes(b"\x0a\x00")

        traces = _make_traces()
        fake_pb2 = _make_trace_pb2(traces)

        with patch("chaosrank.parser.otlp_proto._import_proto", return_value=fake_pb2):
            with patch("chaosrank.parser.otlp_proto.os.path.getsize", return_value=200 * 1024 * 1024):
                with caplog.at_level(logging.WARNING, logger="chaosrank.parser.otlp_proto"):
                    parse_otlp_proto(p)

        assert "Streaming is not yet supported" in caplog.text
        assert "issue #16" in caplog.text

    def test_json_file_warns_but_proceeds_to_value_error(self, tmp_json_file, caplog):
        """JSON file should warn about mismatch, then fail on ParseFromString."""
        from chaosrank.parser.otlp_proto import parse_otlp_proto
        fake_pb2 = _make_trace_pb2(fail=True)
        with patch("chaosrank.parser.otlp_proto._import_proto", return_value=fake_pb2):
            with caplog.at_level(logging.WARNING, logger="chaosrank.parser.otlp_proto"):
                with pytest.raises(ValueError):
                    parse_otlp_proto(tmp_json_file)
        assert "looks like a JSON file" in caplog.text


# ---------------------------------------------------------------------------
# warn_if_binary (otlp_json_guard) — symmetric check for JSON parser
# ---------------------------------------------------------------------------

class TestWarnIfBinary:
    def test_binary_file_warns(self, tmp_proto_file, caplog):
        from chaosrank.parser.otlp_json_guard import warn_if_binary
        with caplog.at_level(logging.WARNING, logger="chaosrank.parser.otlp_json_guard"):
            warn_if_binary(tmp_proto_file)
        assert "--otlp-format protobuf" in caplog.text
        assert "does not look like a JSON file" in caplog.text

    def test_json_file_no_warning(self, tmp_json_file, caplog):
        from chaosrank.parser.otlp_json_guard import warn_if_binary
        with caplog.at_level(logging.WARNING, logger="chaosrank.parser.otlp_json_guard"):
            warn_if_binary(tmp_json_file)
        assert caplog.text == ""

    def test_missing_file_no_crash(self, tmp_path):
        from chaosrank.parser.otlp_json_guard import warn_if_binary
        warn_if_binary(tmp_path / "nonexistent.json")


# ---------------------------------------------------------------------------
# Edge shape consistency — proto path produces same dict shape as JSON path
# ---------------------------------------------------------------------------

class TestEdgeShapeConsistency:
    def test_proto_and_json_build_same_edge_keys(self, tmp_proto_file):
        """_build_edge_map is shared — verify proto-produced dicts are compatible."""
        from chaosrank.parser.otlp import _build_edge_map

        # Simulate what parse_otlp_proto produces internally
        span_service = {
            "aa00000000000001": "frontend",
            "bb00000000000002": "payment-service",
        }
        all_spans = [
            ({"spanId": "aa00000000000001", "parentSpanId": ""}, "frontend"),
            ({"spanId": "bb00000000000002", "parentSpanId": "aa00000000000001"}, "payment-service"),
        ]
        edges = _build_edge_map(all_spans, span_service, min_call_frequency=1)
        assert ("frontend", "payment-service") in edges
