import logging

from chaosrank.parser.incidents import ServiceIncidents
from chaosrank.scorer.fragility import compute_fragility
from chaosrank.scorer.suggest import suggest_fault

logger = logging.getLogger(__name__)

DEFAULT_ALPHA = 0.6
DEFAULT_BETA  = 0.4


def rank_services(
    blast_radius: dict[str, float],
    service_incidents: dict[str, ServiceIncidents],
    alpha: float = DEFAULT_ALPHA,
    beta: float = DEFAULT_BETA,
    decay_lambda: float = 0.10,
    base_window: float = 5.0,
    severity_weights: dict[str, float] | None = None,
) -> list[dict]:
    if abs(alpha + beta - 1.0) > 1e-6:
        raise ValueError(f"alpha + beta must equal 1.0, got {alpha + beta}")

    all_services = list(blast_radius.keys())

    if not all_services:
        logger.warning("No services to rank — blast radius scores are empty")
        return []

    fragility = compute_fragility(
        service_incidents=service_incidents,
        all_service_names=all_services,
        decay_lambda=decay_lambda,
        base_window=base_window,
        severity_weights=severity_weights,
    )

    if not service_incidents:
        logger.warning(
            "No incident data. Ranking by blast radius only. "
            "Provide --incidents to enable fragility scoring."
        )

    ranked = []
    for service in all_services:
        br = blast_radius.get(service, 0.0)
        fr = fragility.get(service, 0.0)
        fault, confidence = suggest_fault(
            service=service,
            service_incidents=service_incidents,
            decay_lambda=decay_lambda,
        )
        ranked.append({
            "service":         service,
            "risk":            round(alpha * br + beta * fr, 4),
            "blast_radius":    round(br, 4),
            "fragility":       round(fr, 4),
            "suggested_fault": fault,
            "confidence":      confidence,
        })

    ranked.sort(key=lambda r: (r["risk"], r["blast_radius"]), reverse=True)

    for i, row in enumerate(ranked, start=1):
        row["rank"] = i

    _check_signal_alignment(ranked)

    return ranked


def _check_signal_alignment(ranked: list[dict]) -> None:
    if len(ranked) < 4:
        return

    br_ranks = {r["service"]: i for i, r in enumerate(sorted(ranked, key=lambda r: r["blast_radius"], reverse=True))}
    fr_ranks = {r["service"]: i for i, r in enumerate(sorted(ranked, key=lambda r: r["fragility"], reverse=True))}

    services = [r["service"] for r in ranked]
    concordant = discordant = 0
    n = len(services)

    for i in range(n):
        for j in range(i + 1, n):
            s1, s2 = services[i], services[j]
            if (br_ranks[s1] < br_ranks[s2]) == (fr_ranks[s1] < fr_ranks[s2]):
                concordant += 1
            else:
                discordant += 1

    total = concordant + discordant
    tau = (concordant - discordant) / total if total > 0 else 1.0

    if tau < 0.5:
        logger.warning(
            "Signal misalignment detected (Kendall tau=%.2f). "
            "Blast radius and fragility rankings diverge significantly for this deployment. "
            "Consider reviewing alpha/beta weights in chaosrank.yaml.",
            tau,
        )
    else:
        logger.debug("Signal alignment OK (Kendall tau=%.2f)", tau)
