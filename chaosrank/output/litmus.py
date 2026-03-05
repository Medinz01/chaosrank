import yaml
from datetime import datetime


def render_litmus(ranked: list[dict], top_n: int = 1) -> str:
    """Generate a LitmusChaos ChaosEngine YAML manifest for the top-ranked service(s)."""
    if not ranked:
        return "# ChaosRank: no services to target\n"

    documents = []

    for row in ranked[:top_n]:
        service = row["service"]
        fault   = row["suggested_fault"]

        manifest = {
            "apiVersion": "litmuschaos.io/v1alpha1",
            "kind": "ChaosEngine",
            "metadata": {
                "name": f"chaosrank-{service}-{fault}".replace("_", "-"),
                "namespace": "default",
                "annotations": {
                    "generated-by": "chaosrank",
                    "generated-at": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "risk-score":   str(row["risk"]),
                    "blast-radius": str(row["blast_radius"]),
                    "fragility":    str(row["fragility"]),
                    "confidence":   row["confidence"],
                },
            },
            "spec": {
                "appinfo": {
                    "appns":    "default",
                    "applabel": f"app={service}",
                    "appkind":  "deployment",
                },
                "chaosServiceAccount": "litmus-admin",
                "experiments": [
                    {
                        "name": _experiment_name(service, fault),
                        "spec": {
                            "components": {
                                "env": _env_for_fault(fault),
                            }
                        },
                    }
                ],
            },
        }

        documents.append(
            f"# Rank #{row['rank']}: {service} "
            f"(risk={row['risk']:.3f}, fault={fault}, confidence={row['confidence']})\n"
            + yaml.dump(manifest, default_flow_style=False, sort_keys=False)
        )

    return "\n---\n".join(documents)


def _fault_to_chaos_kind(fault: str) -> str:
    return {
        "latency-injection":  "pod-network-latency",
        "partial-response":   "pod-http-modify-response",
        "connection-timeout": "pod-network-loss",
        "pod-failure":        "pod-delete",
    }.get(fault, "pod-delete")


def _experiment_name(service: str, fault: str) -> str:
    return f"{service}-{fault}".replace("_", "-")


def _env_for_fault(fault: str) -> list[dict]:
    base = [
        {"name": "TOTAL_CHAOS_DURATION", "value": "60"},
        {"name": "CHAOS_INTERVAL",       "value": "10"},
        {"name": "PODS_AFFECTED_PERC",   "value": "50"},
    ]
    if fault == "latency-injection":
        base += [
            {"name": "NETWORK_LATENCY", "value": "2000"},
            {"name": "JITTER",          "value": "500"},
        ]
    elif fault == "connection-timeout":
        base += [
            {"name": "NETWORK_PACKET_LOSS_PERCENTAGE", "value": "100"},
            {"name": "DESTINATION_PORTS",              "value": "8080,443"},
        ]
    elif fault == "partial-response":
        base += [
            {"name": "STATUS_CODE",    "value": "500"},
            {"name": "MODIFY_PERCENT", "value": "30"},
        ]
    return base
