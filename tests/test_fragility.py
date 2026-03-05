import logging
# import math
from datetime import datetime, timedelta

import pytest

from chaosrank.parser.incidents import Incident, ServiceIncidents
from chaosrank.scorer.fragility import (
    DEFAULT_LAMBDA,
    _burst_window_minutes,
    _deduplicate,
    _weighted_incident,
    _zscore_normalize,
    compute_fragility,
)


def make_incident(
    service: str,
    type: str = "error",
    severity: str = "high",
    days_ago: float = 1.0,
    request_volume: float | None = 1000.0,
) -> Incident:
    return Incident(
        timestamp=datetime.utcnow() - timedelta(days=days_ago),
        service=service,
        type=type,
        severity=severity,
        request_volume=request_volume,
    )


def make_service_incidents(service: str, incidents: list[Incident]) -> ServiceIncidents:
    si = ServiceIncidents(service=service)
    si.incidents = incidents
    return si


class TestBurstDedup:

    def test_collapses_same_type_within_window(self):
        """window = 5.0 * log(2) ≈ 3.47 min; three errors 1 min apart at traffic == baseline collapse to one."""
        base = datetime.utcnow() - timedelta(days=1)
        incidents = [
            Incident(base,                        "svc", "error", "high", 1000.0),
            Incident(base + timedelta(minutes=1), "svc", "error", "high", 1000.0),
            Incident(base + timedelta(minutes=2), "svc", "error", "high", 1000.0),
        ]
        result = _deduplicate(incidents, baseline=1000.0, base_window=5.0)
        assert len(result) == 1

    def test_preserves_incidents_outside_window(self):
        base = datetime.utcnow() - timedelta(days=1)
        incidents = [
            Incident(base,                         "svc", "error", "high", 100.0),
            Incident(base + timedelta(minutes=10), "svc", "error", "high", 100.0),
        ]
        result = _deduplicate(incidents, baseline=1000.0, base_window=5.0)
        assert len(result) == 2

    def test_different_types_not_collapsed(self):
        base = datetime.utcnow() - timedelta(days=1)
        incidents = [
            Incident(base,                        "svc", "error",   "high", 100.0),
            Incident(base + timedelta(minutes=1), "svc", "latency", "high", 100.0),
        ]
        result = _deduplicate(incidents, baseline=1000.0, base_window=5.0)
        assert len(result) == 2

    def test_high_traffic_widens_window(self):
        """burst_window grows with traffic; 8-min gap collapses at 10x baseline but not at 0.1x."""
        baseline = 1000.0
        base_window = 5.0

        window_high = _burst_window_minutes(10000.0, baseline, base_window)
        window_low  = _burst_window_minutes(100.0,   baseline, base_window)

        assert window_high > window_low
        assert window_high > 8
        assert window_low  < 8

    def test_empty_incidents_returns_empty(self):
        assert _deduplicate([], baseline=1000.0, base_window=5.0) == []


class TestWeightedIncident:

    def test_high_traffic_incident_discounted(self):
        weights = {"high": 0.602}
        w_low  = _weighted_incident(make_incident("svc", severity="high", request_volume=100.0),   weights, None)
        w_high = _weighted_incident(make_incident("svc", severity="high", request_volume=10000.0), weights, None)
        assert w_low > w_high

    def test_higher_severity_scores_higher_same_traffic(self):
        weights = {"critical": 1.000, "high": 0.602}
        w_crit = _weighted_incident(make_incident("svc", severity="critical", request_volume=1000.0), weights, None)
        w_high = _weighted_incident(make_incident("svc", severity="high",     request_volume=1000.0), weights, None)
        assert w_crit > w_high

    def test_falls_back_to_window_avg(self):
        weights = {"high": 0.602}
        inc = make_incident("svc", severity="high", request_volume=None)
        assert _weighted_incident(inc, weights, window_avg_volume=1000.0) > 0

    def test_falls_back_to_no_normalization(self):
        weights = {"high": 0.602}
        inc = make_incident("svc", severity="high", request_volume=None)
        assert _weighted_incident(inc, weights, window_avg_volume=None) == pytest.approx(0.602)


class TestZscoreNormalize:

    def test_output_in_unit_interval(self):
        raw = {"a": 10.0, "b": 2.0, "c": 0.5, "d": 0.1, "e": 50.0}
        for v in _zscore_normalize(raw).values():
            assert 0.0 <= v <= 1.0

    def test_outlier_does_not_collapse_others(self):
        """Z-score clip preserves spread among non-outliers; MinMax would compress them to ~0.00005."""
        raw = {"outlier": 1000.0, "a": 50.0, "b": 20.0, "c": 5.0, "d": 0.1}
        result = _zscore_normalize(raw)

        non_outlier = [v for k, v in result.items() if k != "outlier"]
        spread = max(non_outlier) - min(non_outlier)

        assert spread > 0.01, f"Non-outlier spread too small: {spread:.4f}"
        assert result["a"] > result["b"] > result["c"] > result["d"]

    def test_uniform_scores_emit_warning_return_half(self, caplog):
        raw = {"a": 1.0, "b": 1.0, "c": 1.0}
        with caplog.at_level(logging.WARNING):
            result = _zscore_normalize(raw)
        assert all(v == pytest.approx(0.5) for v in result.values())
        assert "uniform" in caplog.text.lower()

    def test_higher_raw_maps_to_higher_normalized(self):
        raw = {"low": 0.1, "mid": 1.0, "high": 5.0}
        result = _zscore_normalize(raw)
        assert result["high"] > result["mid"] > result["low"]

    def test_empty_returns_empty(self):
        assert _zscore_normalize({}) == {}


class TestDecay:

    def test_recent_incident_outweighs_old(self):
        si = {
            "recent": make_service_incidents("recent", [
                make_incident("recent", severity="critical", days_ago=1.0,  request_volume=1000.0)
            ]),
            "old": make_service_incidents("old", [
                make_incident("old",    severity="critical", days_ago=25.0, request_volume=1000.0)
            ]),
        }
        scores = compute_fragility(si, ["recent", "old"], decay_lambda=DEFAULT_LAMBDA)
        assert scores["recent"] > scores["old"]

    def test_high_lambda_decays_faster(self):
        """Use two services so normalization preserves the relative gap."""
        inc      = make_incident("svc",  severity="critical", days_ago=20.0, request_volume=1000.0)
        baseline = make_incident("svc2", severity="low",      days_ago=1.0,  request_volume=100.0)
        si = {
            "svc":  make_service_incidents("svc",  [inc]),
            "svc2": make_service_incidents("svc2", [baseline]),
        }
        score_slow = compute_fragility(si, ["svc", "svc2"], decay_lambda=0.05)
        score_fast = compute_fragility(si, ["svc", "svc2"], decay_lambda=0.20)
        assert score_slow["svc"] > score_fast["svc"]


class TestFragilityPreservation:
    """
    Core correctness guarantee from algorithm.md §5.7.

    payment-service (medium traffic, 1000 req/s, 5 incidents) must outscore
    frontend (high traffic, 10000 req/s, 2 proportional incidents).
    This is the case post-hoc normalization gets wrong.
    """

    def _build_scenarios(self):
        frontend_incidents = [
            make_incident("frontend", type="error", severity="high",     days_ago=5.0,  request_volume=10000.0),
            make_incident("frontend", type="error", severity="high",     days_ago=10.0, request_volume=10000.0),
        ]
        payment_incidents = [
            make_incident("payment-service", type="error", severity="critical", days_ago=2.0,  request_volume=1000.0),
            make_incident("payment-service", type="error", severity="critical", days_ago=4.0,  request_volume=1000.0),
            make_incident("payment-service", type="error", severity="high",     days_ago=6.0,  request_volume=1000.0),
            make_incident("payment-service", type="error", severity="high",     days_ago=8.0,  request_volume=1000.0),
            make_incident("payment-service", type="error", severity="high",     days_ago=10.0, request_volume=1000.0),
        ]
        return {
            "frontend":        make_service_incidents("frontend",        frontend_incidents),
            "payment-service": make_service_incidents("payment-service", payment_incidents),
        }

    def test_payment_ranks_above_frontend(self):
        scores = compute_fragility(
            service_incidents=self._build_scenarios(),
            all_service_names=["frontend", "payment-service"],
            decay_lambda=DEFAULT_LAMBDA,
        )
        assert scores["payment-service"] > scores["frontend"], (
            f"Fragility preservation FAILED: "
            f"payment-service={scores['payment-service']:.4f} frontend={scores['frontend']:.4f}\n"
            f"Per-incident normalization is not working correctly."
        )

    def test_score_differential_is_meaningful(self):
        scores = compute_fragility(
            service_incidents=self._build_scenarios(),
            all_service_names=["frontend", "payment-service"],
            decay_lambda=DEFAULT_LAMBDA,
        )
        diff = scores["payment-service"] - scores["frontend"]
        assert diff > 0.05, (
            f"Score differential too small ({diff:.4f}). "
            f"Per-incident normalization may not be preserving signal strength."
        )

    def test_extreme_traffic_differential(self):
        """100x traffic differential (100k vs 1k req/s); payment-service with 3x more incidents must still rank higher."""
        frontend_incidents = [
            make_incident("frontend",        severity="critical", days_ago=3.0, request_volume=100_000.0),
            make_incident("frontend",        severity="critical", days_ago=6.0, request_volume=100_000.0),
        ]
        payment_incidents = [
            make_incident("payment-service", severity="critical", days_ago=d,   request_volume=1000.0)
            for d in [2.0, 4.0, 6.0, 8.0, 10.0, 12.0]
        ]
        si = {
            "frontend":        make_service_incidents("frontend",        frontend_incidents),
            "payment-service": make_service_incidents("payment-service", payment_incidents),
        }
        scores = compute_fragility(
            service_incidents=si,
            all_service_names=["frontend", "payment-service"],
            decay_lambda=DEFAULT_LAMBDA,
        )
        assert scores["payment-service"] > scores["frontend"], (
            f"Extreme traffic differential test FAILED: "
            f"payment={scores['payment-service']:.4f} frontend={scores['frontend']:.4f}"
        )


class TestColdStart:

    def test_no_incidents_produces_uniform_scores(self):
        """All services with no history → all raw scores 0 → z-score uniform → all 0.5."""
        scores = compute_fragility(
            service_incidents={},
            all_service_names=["a", "b", "c"],
            decay_lambda=DEFAULT_LAMBDA,
        )
        for v in scores.values():
            assert v == pytest.approx(0.5)

    def test_services_in_graph_not_in_incidents_get_zero_raw(self):
        si = {
            "payment-service": make_service_incidents("payment-service", [
                make_incident("payment-service", severity="critical", days_ago=2.0, request_volume=1000.0),
            ])
        }
        scores = compute_fragility(
            service_incidents=si,
            all_service_names=["payment-service", "unknown-service"],
            decay_lambda=DEFAULT_LAMBDA,
        )
        assert scores["payment-service"] > scores["unknown-service"]