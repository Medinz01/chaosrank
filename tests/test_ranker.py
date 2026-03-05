import logging
from datetime import datetime, timedelta

import pytest

from chaosrank.parser.incidents import Incident, ServiceIncidents
from chaosrank.scorer.ranker import rank_services


def make_blast(services: list[str], scores: list[float]) -> dict[str, float]:
    return dict(zip(services, scores))


def make_si(
    service: str,
    n_incidents: int,
    severity: str = "high",
    inc_type: str = "error",
    days_ago: float = 2.0,
    request_volume: float = 1000.0,
) -> ServiceIncidents:
    si = ServiceIncidents(service=service)
    si.incidents = [
        Incident(
            timestamp=datetime.utcnow() - timedelta(days=days_ago + i),
            service=service,
            type=inc_type,
            severity=severity,
            request_volume=request_volume,
        )
        for i in range(n_incidents)
    ]
    return si


class TestRankStructure:

    def test_output_sorted_descending_by_risk(self):
        ranked = rank_services(make_blast(["a", "b", "c"], [0.9, 0.5, 0.2]), {})
        risks = [r["risk"] for r in ranked]
        assert risks == sorted(risks, reverse=True)

    def test_rank_field_is_one_indexed_no_gaps(self):
        ranked = rank_services(make_blast(["a", "b", "c", "d"], [0.9, 0.7, 0.4, 0.1]), {})
        assert [r["rank"] for r in ranked] == list(range(1, len(ranked) + 1))

    def test_all_services_present_in_output(self):
        services = ["payment", "auth", "cart", "inventory", "frontend"]
        ranked = rank_services(make_blast(services, [0.9, 0.7, 0.5, 0.3, 0.1]), {})
        assert {r["service"] for r in ranked} == set(services)

    def test_empty_blast_returns_empty(self):
        assert rank_services({}, {}) == []

    def test_required_fields_present(self):
        ranked = rank_services(make_blast(["svc"], [0.5]), {})
        required = {"rank", "service", "risk", "blast_radius", "fragility", "suggested_fault", "confidence"}
        assert required.issubset(ranked[0].keys())


class TestRiskScoreMath:

    def test_risk_is_weighted_sum(self):
        """Cold start makes fragility uniform (0.5); top service is determined by blast radius."""
        ranked = rank_services(make_blast(["high", "low"], [1.0, 0.0]), {}, alpha=0.6, beta=0.4)
        top = ranked[0]
        assert top["service"] == "high"
        assert top["risk"] == pytest.approx(0.6 * top["blast_radius"] + 0.4 * top["fragility"], abs=1e-3)

    def test_invalid_alpha_beta_raises(self):
        with pytest.raises(ValueError, match="alpha.*beta"):
            rank_services(make_blast(["svc"], [0.5]), {}, alpha=0.7, beta=0.7)

    def test_alpha_beta_boundary_pure_blast(self):
        ranked = rank_services(make_blast(["a", "b", "c"], [0.9, 0.5, 0.1]), {}, alpha=1.0, beta=0.0)
        assert [r["service"] for r in ranked] == ["a", "b", "c"]


class TestColdStart:

    def test_cold_start_ranks_by_blast_radius(self):
        """No incidents → fragility uniform → ranking is blast radius order."""
        ranked = rank_services(make_blast(["payment", "auth", "cart"], [0.90, 0.60, 0.20]), {})
        assert [r["service"] for r in ranked] == ["payment", "auth", "cart"]

    def test_cold_start_warning_emitted(self, caplog):
        with caplog.at_level(logging.WARNING):
            rank_services(make_blast(["svc"], [0.5]), {})
        assert "no incident data" in caplog.text.lower()


class TestCombinedSignal:

    def test_high_blast_high_fragility_beats_high_blast_low_fragility(self):
        """Equal blast radius; more/worse incidents should rank higher."""
        blast = make_blast(["unstable", "stable"], [0.85, 0.85])
        si = {
            "unstable": make_si("unstable", n_incidents=8, severity="critical"),
            "stable":   make_si("stable",   n_incidents=1, severity="low"),
        }
        assert rank_services(blast, si)[0]["service"] == "unstable"

    def test_high_blast_low_fragility_vs_low_blast_high_fragility(self):
        """Default alpha=0.6 is blast-heavy; very high blast with no incidents beats low blast with many."""
        blast = make_blast(["critical-stable", "leaf-unstable"], [0.95, 0.05])
        si = {
            "critical-stable": make_si("critical-stable", n_incidents=0),
            "leaf-unstable":   make_si("leaf-unstable",   n_incidents=10, severity="critical"),
        }
        assert rank_services(blast, si, alpha=0.6, beta=0.4)[0]["service"] == "critical-stable"

    def test_fragility_breaks_blast_radius_tie(self):
        """Identical blast radius; fragility signal determines order."""
        blast = make_blast(["svc-a", "svc-b"], [0.5, 0.5])
        si = {
            "svc-a": make_si("svc-a", n_incidents=5, severity="critical"),
            "svc-b": make_si("svc-b", n_incidents=1, severity="low"),
        }
        assert rank_services(blast, si)[0]["service"] == "svc-a"


class TestFaultSuggestion:

    def test_error_incidents_suggest_partial_response(self):
        si = {"svc": make_si("svc", n_incidents=6, inc_type="error", severity="critical")}
        assert rank_services(make_blast(["svc"], [0.5]), si)[0]["suggested_fault"] == "partial-response"

    def test_latency_incidents_suggest_latency_injection(self):
        si = {"svc": make_si("svc", n_incidents=6, inc_type="latency", severity="high")}
        assert rank_services(make_blast(["svc"], [0.5]), si)[0]["suggested_fault"] == "latency-injection"

    def test_no_incidents_suggests_pod_failure(self):
        ranked = rank_services(make_blast(["svc"], [0.5]), {})
        assert ranked[0]["suggested_fault"] == "pod-failure"
        assert ranked[0]["confidence"] == "low"

    def test_high_confidence_requires_sufficient_incidents(self):
        """6 pure-signal incidents of the same type → high confidence."""
        si = {"svc": make_si("svc", n_incidents=6, inc_type="error", severity="high")}
        assert rank_services(make_blast(["svc"], [0.5]), si)[0]["confidence"] == "high"


class TestSignalAlignment:

    def test_misaligned_signals_emit_warning(self, caplog):
        """blast order: a>b>c>d; fragility order: d>c>b>a — opposite ranking triggers tau warning."""
        blast = make_blast(["a", "b", "c", "d"], [0.9, 0.7, 0.3, 0.1])
        si = {
            "a": make_si("a", n_incidents=0),
            "b": make_si("b", n_incidents=1, severity="low"),
            "c": make_si("c", n_incidents=5, severity="high"),
            "d": make_si("d", n_incidents=8, severity="critical"),
        }
        with caplog.at_level(logging.WARNING):
            rank_services(blast, si)
        assert "misalignment" in caplog.text.lower()