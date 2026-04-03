"""
chaosrank/engine/serializer.py

Graph and incident serialization for the engine API.

Converts local Python objects (nx.DiGraph, ServiceIncidents) to the
lightweight JSON payloads the engine expects. Only sends fields the
engine actually uses — strips all visualization/metadata attributes
to keep payloads small.

Size estimate:
  100-service graph  (~300 edges)  → ~30 KB JSON
  500-service graph  (~1500 edges) → ~150 KB JSON
  1000-service graph (~3000 edges) → ~300 KB JSON

For larger graphs, enable compression:
  EngineClient(..., compress=True)
"""
from __future__ import annotations

import networkx as nx

from chaosrank.parser.incidents import ServiceIncidents


def graph_to_payload(G: nx.DiGraph) -> dict:
    """
    Serialize a NetworkX DiGraph to a minimal edge-list payload.

    Only transmits: source, target, weight, edge_type, channel, topic.
    All other node/edge attributes are stripped to minimize payload size.
    Zero-weight edges are excluded since they contribute nothing to scoring.
    """
    edges = []
    for u, v, data in G.edges(data=True):
        weight = data.get("weight", 1)
        if weight <= 0:
            continue
        edge: dict = {
            "source":    u,
            "target":    v,
            "weight":    float(weight),
            "edge_type": data.get("edge_type", "sync"),
        }
        if data.get("channel"):
            edge["channel"] = data["channel"]
        if data.get("topic"):
            edge["topic"] = data["topic"]
        edges.append(edge)

    return {"edges": edges}


def incidents_to_payload(
    service_incidents: dict[str, ServiceIncidents],
) -> dict[str, list[dict]]:
    """
    Serialize ServiceIncidents to a JSON-safe dict.

    Format: { service_name: [ {timestamp, type, severity, request_volume}, ... ] }
    """
    payload: dict[str, list[dict]] = {}
    for svc, si in service_incidents.items():
        incidents = []
        for inc in si.incidents:
            entry: dict = {
                "timestamp": inc.timestamp.isoformat(),
                "type":      inc.type,
                "severity":  inc.severity,
            }
            if inc.request_volume is not None:
                entry["request_volume"] = inc.request_volume
            incidents.append(entry)
        if incidents:
            payload[svc] = incidents
    return payload
