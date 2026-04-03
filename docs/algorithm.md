# ChaosRank — Algorithm Summary

This document provides a high-level overview of the mathematical models used by the **ChaosRank Engine** to prioritize chaos experiments.

---

## 1. Input Data Model

ChaosRank combines structural topology with operational history:

### 1.1 Distributed Traces
Aggregated call graphs derived from Jaeger or OTel traces.
- **Edges**: Directed (caller → callee).
- **Weights**: Call frequency (spans).

### 1.2 Incident History
Raw incidents processed from CSV or alerting adapters.
- **Severity Weights**: 
  - `critical`: 1.000
  - `high`: 0.602
  - `medium`: 0.301
  - `low`: 0.100

---

## 2. Blast Radius (Impact)

Blast Radius measures how many services are affected if a specific service fails. It is a blended centrality score:

```
blast_radius(v) = 0.5 * pagerank(v, G) + 0.5 * in_degree_centrality(v, G)
```

- **PageRank**: Captures transitive influence (deep dependency chains).
- **In-Degree Centrality**: Captures direct dependents (shallow-wide hubs).

---

## 3. Fragility Score (Likelihood)

Fragility measures how unstable a service is relative to its traffic load. The **ChaosRank Engine** computes this via a four-step pipeline:

1. **Traffic-Aware Deduplication**: Collapses alert storms based on request volume.
2. **Contextual Normalization**: Normalizes severity by the traffic volume at the moment of failure.
3. **Exponential Decay**: Weights recent incidents more heavily.
4. **Z-Scaling**: Normalizes scores across the fleet to a [0, 1] range.

---

## 4. Final Risk Ranking

The Risk Score combines structural impact (Blast Radius) and operational history (Fragility):

```
risk(v) = alpha * blast_radius(v) + beta * fragility(v)
```

- **Default**: `alpha=0.6`, `beta=0.4`.

---

## 5. Fault Type Suggestion

ChaosRank suggests fault types based on the dominant incident signal:

| Dominant Signal | Suggested Fault |
|---|---|
| p99 latency spike | `latency-injection` |
| error rate breach | `partial-response` |
| timeout incident | `connection-timeout` |
| no history | `pod-failure` (default) |

chaos engineering tooling.