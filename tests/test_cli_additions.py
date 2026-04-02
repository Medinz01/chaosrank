"""Tests for cli.py additions (#10 betweenness, #11 datadog, #12 otlp-format).

These tests cover only the new CLI wiring — option parsing, validation,
dispatch logic. They do not re-test the underlying modules (those are covered
in test_betweenness.py, test_datadog_adapter.py, test_otlp_proto.py).

Strategy: patch build_graph, EngineClient.rank, EngineClient.health, and adapters
so tests run without real trace files or network calls.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch, call
import pytest
import networkx as nx
from typer.testing import CliRunner

from chaosrank.cli import app

runner = CliRunner()


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def fake_graph(tmp_path):
    """A minimal DiGraph with two nodes — passes the empty-graph guard."""
    G = nx.DiGraph()
    G.add_edge("frontend", "payment-service", weight=100)
    return G


@pytest.fixture
def traces_file(tmp_path):
    p = tmp_path / "traces.json"
    p.write_text("{}")
    return p


@pytest.fixture
def proto_file(tmp_path):
    p = tmp_path / "traces.pb"
    p.write_bytes(b"\x80\x10binary-content")
    return p


def _ranked():
    return [{"service": "payment-service", "risk": 0.9, "blast_radius": 0.8,
             "fragility": 0.5, "suggested_fault": "pod-failure", "confidence": "low"}]


# ---------------------------------------------------------------------------
# --otlp-format validation
# ---------------------------------------------------------------------------

class TestOtlpFormatValidation:
    def test_unknown_otlp_format_exits_1(self, traces_file):
        result = runner.invoke(app, [
            "rank", "--traces", str(traces_file),
            "--format", "otlp", "--otlp-format", "msgpack",
        ])
        assert result.exit_code == 1
        assert "Unknown --otlp-format" in result.output

    def test_otlp_format_with_jaeger_warns(self, traces_file, fake_graph):
        with patch("chaosrank.cli.build_graph", return_value=fake_graph), \
             patch("chaosrank.cli.EngineClient.health", return_value=True), \
             patch("chaosrank.cli.EngineClient.rank", return_value=_ranked()), \
             patch("chaosrank.cli.render_table"):
            result = runner.invoke(app, [
                "rank", "--traces", str(traces_file),
                "--format", "jaeger", "--otlp-format", "protobuf",
            ])
        assert "has no effect" in result.output

    def test_otlp_json_calls_warn_if_binary(self, traces_file, fake_graph):
        with patch("chaosrank.cli.build_graph", return_value=fake_graph), \
             patch("chaosrank.cli.EngineClient.health", return_value=True), \
             patch("chaosrank.cli.EngineClient.rank", return_value=_ranked()), \
             patch("chaosrank.cli.render_table"), \
             patch("chaosrank.parser.otlp_json_guard.warn_if_binary") as mock_guard:
            result = runner.invoke(app, [
                "rank", "--traces", str(traces_file),
                "--format", "otlp", "--otlp-format", "json",
            ])
        mock_guard.assert_called_once_with(traces_file)

    def test_otlp_protobuf_passes_format_to_build_graph(self, proto_file, fake_graph):
        with patch("chaosrank.cli.build_graph", return_value=fake_graph) as mock_build, \
             patch("chaosrank.cli.EngineClient.health", return_value=True), \
             patch("chaosrank.cli.EngineClient.rank", return_value=_ranked()), \
             patch("chaosrank.cli.render_table"):
            runner.invoke(app, [
                "rank", "--traces", str(proto_file),
                "--format", "otlp", "--otlp-format", "protobuf",
            ])
        mock_build.assert_called_once()
        _, kwargs = mock_build.call_args
        assert kwargs.get("otlp_format") == "protobuf" or mock_build.call_args[0][2:] == ("otlp",)
        call_kwargs = mock_build.call_args.kwargs
        assert call_kwargs.get("otlp_format") == "protobuf"

    def test_otlp_protobuf_does_not_call_warn_if_binary(self, proto_file, fake_graph):
        """_check_format_mismatch is internal to otlp_proto — cli should not call warn_if_binary."""
        with patch("chaosrank.cli.build_graph", return_value=fake_graph), \
             patch("chaosrank.cli.EngineClient.health", return_value=True), \
             patch("chaosrank.cli.EngineClient.rank", return_value=_ranked()), \
             patch("chaosrank.cli.render_table"), \
             patch("chaosrank.parser.otlp_json_guard.warn_if_binary") as mock_guard:
            runner.invoke(app, [
                "rank", "--traces", str(proto_file),
                "--format", "otlp", "--otlp-format", "protobuf",
            ])
        mock_guard.assert_not_called()

    def test_graph_command_otlp_format_passed(self, traces_file, fake_graph):
        with patch("chaosrank.cli.build_graph", return_value=fake_graph) as mock_build:
            runner.invoke(app, [
                "graph", "--traces", str(traces_file),
                "--format", "otlp", "--otlp-format", "protobuf",
            ])
        call_kwargs = mock_build.call_args.kwargs
        assert call_kwargs.get("otlp_format") == "protobuf"


# ---------------------------------------------------------------------------
# --betweenness and --w-bc
# ---------------------------------------------------------------------------

class TestBetweennessFlag:
    def test_betweenness_passed_to_compute_blast_radius(self, traces_file, fake_graph):
        with patch("chaosrank.cli.build_graph", return_value=fake_graph), \
             patch("chaosrank.cli.EngineClient.health", return_value=True), \
             patch("chaosrank.cli.EngineClient.rank", return_value=_ranked()) as mock_rank, \
             patch("chaosrank.cli.render_table"):
            runner.invoke(app, [
                "rank", "--traces", str(traces_file), "--betweenness",
            ])
        call_kwargs = mock_rank.call_args.kwargs
        assert call_kwargs.get("config", {}).get("use_betweenness") is True

    def test_no_betweenness_flag_default_false(self, traces_file, fake_graph):
        with patch("chaosrank.cli.build_graph", return_value=fake_graph), \
             patch("chaosrank.cli.EngineClient.health", return_value=True), \
             patch("chaosrank.cli.EngineClient.rank", return_value=_ranked()) as mock_rank, \
             patch("chaosrank.cli.render_table"):
            runner.invoke(app, ["rank", "--traces", str(traces_file)])
        call_kwargs = mock_rank.call_args.kwargs
        assert call_kwargs.get("config", {}).get("use_betweenness") is False

    def test_w_bc_passed_when_betweenness_enabled(self, traces_file, fake_graph):
        with patch("chaosrank.cli.build_graph", return_value=fake_graph), \
             patch("chaosrank.cli.EngineClient.health", return_value=True), \
             patch("chaosrank.cli.EngineClient.rank", return_value=_ranked()) as mock_rank, \
             patch("chaosrank.cli.render_table"):
            runner.invoke(app, [
                "rank", "--traces", str(traces_file),
                "--betweenness", "--w-bc", "0.2",
            ])
        call_kwargs = mock_rank.call_args.kwargs
        assert call_kwargs.get("config", {}).get("w_bc") == pytest.approx(0.2)

    def test_w_bc_without_betweenness_warns(self, traces_file, fake_graph):
        with patch("chaosrank.cli.build_graph", return_value=fake_graph), \
             patch("chaosrank.cli.EngineClient.health", return_value=True), \
             patch("chaosrank.cli.EngineClient.rank", return_value=_ranked()), \
             patch("chaosrank.cli.render_table"):
            result = runner.invoke(app, [
                "rank", "--traces", str(traces_file), "--w-bc", "0.2",
            ])
        assert "--w-bc has no effect without --betweenness" in result.output

    def test_w_bc_none_by_default(self, traces_file, fake_graph):
        with patch("chaosrank.cli.build_graph", return_value=fake_graph), \
             patch("chaosrank.cli.EngineClient.health", return_value=True), \
             patch("chaosrank.cli.EngineClient.rank", return_value=_ranked()) as mock_rank, \
             patch("chaosrank.cli.render_table"):
            runner.invoke(app, ["rank", "--traces", str(traces_file)])
        call_kwargs = mock_rank.call_args.kwargs
        assert call_kwargs.get("config", {}).get("w_bc") is None

    def test_blast_radius_value_error_exits_1(self, traces_file, fake_graph):
        """Invalid w_bc + explicit weights should surface as exit code 1."""
        from chaosrank.engine.client import EngineError
        with patch("chaosrank.cli.build_graph", return_value=fake_graph), \
             patch("chaosrank.cli.EngineClient.health", return_value=True), \
             patch("chaosrank.cli.EngineClient.rank",
                   side_effect=EngineError(400, "w_pr + w_od + w_bc must equal 1.0")):
            result = runner.invoke(app, [
                "rank", "--traces", str(traces_file),
                "--betweenness", "--w-bc", "0.9",
            ])
        assert result.exit_code == 1
        assert "Engine error" in result.output


# ---------------------------------------------------------------------------
# Datadog adapter wiring
# ---------------------------------------------------------------------------

class TestDatadogIncidentsFetch:
    def test_datadog_requires_token(self):
        result = runner.invoke(app, [
            "incidents", "--from", "datadog",
            "--app-key", "app-key-123",
        ])
        assert result.exit_code == 1
        assert "DD-API-KEY" in result.output

    def test_datadog_requires_app_key(self):
        result = runner.invoke(app, [
            "incidents", "--from", "datadog",
            "--token", "api-key-123",
        ])
        assert result.exit_code == 1
        assert "DD-APPLICATION-KEY" in result.output

    def test_datadog_adapter_initialised_with_correct_args(self):
        mock_adapter = MagicMock()
        mock_adapter.fetch.return_value = []

        with patch("chaosrank.incident_adapters.datadog.DatadogIncidentAdapter",
                   return_value=mock_adapter) as mock_cls:
            runner.invoke(app, [
                "incidents", "--from", "datadog",
                "--token", "my-api-key",
                "--app-key", "my-app-key",
                "--dry-run",
            ])

        mock_cls.assert_called_once_with(
            api_key="my-api-key",
            app_key="my-app-key",
            site="datadoghq.com",
        )

    def test_datadog_site_flag_passed(self):
        mock_adapter = MagicMock()
        mock_adapter.fetch.return_value = []

        with patch("chaosrank.incident_adapters.datadog.DatadogIncidentAdapter",
                   return_value=mock_adapter) as mock_cls:
            runner.invoke(app, [
                "incidents", "--from", "datadog",
                "--token", "key", "--app-key", "app",
                "--site", "datadoghq.eu",
                "--dry-run",
            ])

        call_kwargs = mock_cls.call_args.kwargs
        assert call_kwargs.get("site") == "datadoghq.eu"

    def test_datadog_window_passed_to_fetch(self):
        mock_adapter = MagicMock()
        mock_adapter.fetch.return_value = []

        with patch("chaosrank.incident_adapters.datadog.DatadogIncidentAdapter",
                   return_value=mock_adapter):
            runner.invoke(app, [
                "incidents", "--from", "datadog",
                "--token", "key", "--app-key", "app",
                "--window", "14d", "--dry-run",
            ])

        mock_adapter.fetch.assert_called_once_with(window_days=14)

    def test_datadog_in_incident_formats(self):
        from chaosrank.cli import _INCIDENT_FORMATS
        assert "datadog" in _INCIDENT_FORMATS

    def test_datadog_dry_run_no_csv_written(self, tmp_path):
        from datetime import datetime, timezone
        from chaosrank.parser.incidents import Incident

        mock_adapter = MagicMock()
        mock_adapter.fetch.return_value = [
            Incident(
                timestamp=datetime(2024, 1, 1, tzinfo=timezone.utc),
                service="payment-service",
                type="error",
                severity="critical",
                request_volume=None,
            )
        ]
        csv_out = tmp_path / "out.csv"

        with patch("chaosrank.incident_adapters.datadog.DatadogIncidentAdapter",
                   return_value=mock_adapter):
            result = runner.invoke(app, [
                "incidents", "--from", "datadog",
                "--token", "key", "--app-key", "app",
                "--dry-run",
            ])

        assert not csv_out.exists()
        assert "payment-service" in result.output
