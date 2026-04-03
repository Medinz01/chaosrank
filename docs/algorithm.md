# ChaosRank — Algorithm Design & Mathematical Derivation

> This document explains the reasoning behind every algorithmic decision in ChaosRank.
> Intended for reviewers, contributors, and anyone asking "why not just use X?"

---

## 1. Why Random Experiment Selection Fails

Netflix Chaos Monkey popularized random fault injection in 2011 — terminate a random
instance, see if the system survives. Valuable then. Leaves systematic gaps now.

A system with 20 services gives each equal selection probability under random selection.
But the payment service (called by 15 others) and the email notification service
(called by 1) are not equal risks. Running 10 random experiments can miss the
highest-risk service entirely.

The framing ChaosRank uses, borrowed from fault tree analysis:

  risk = impact x likelihood

- Impact      = if this service degrades, how many others are affected?
- Likelihood  = based on history, how probable is a degradation?

ChaosRank operationalizes both into measurable, reproducible scores.

---

## 2. Input Data Model

### 2.1 Distributed Traces (Jaeger JSON)

Each trace is a tree of spans. A span where service A calls service B is a dependency
A -> B. Aggregate across all traces in the observation window to build weighted
directed graph G. Edge weight = call frequency. Filter edges below min_call_frequency
(default: 10) to remove noise from health checks and one-off calls.

### 2.2 Incident History (CSV)

  timestamp, service, type, severity, request_volume

Severity weights use a log scale to reflect operational reality:

  critical = 1.000   (reference point)
  high     = 0.602   (~60% of critical; derived as log10(4) on a log10 scale)
  medium   = 0.301   (~30% of critical; derived as log10(2) on a log10 scale)
  low      = 0.100   (fixed floor to preserve weak fragility signal)

RATIONALE: A linear scale (1.0, 0.7, 0.4, 0.1) implies critical is only 10x
low-severity. In practice a critical outage is orders of magnitude more impactful.
The log scale compresses less aggressively and better reflects real operational
impact differences. The low=0.1 floor prevents zero-weighting services with
no high-severity history. Configurable via severity_weights in chaosrank.yaml.

request_volume = per-service request count at time of incident.
Used for per-incident traffic normalization (Section 5.3).
Falls back to window average, then skips with warning if unavailable.

### 2.3 Service Name Normalization

OTel exporters include version strings or pod hashes:
  payment-service-v2-7d9f8b  vs  payment-service  vs  payments

Normalization pipeline (parse time):
  1. Lowercase
  2. Strip version patterns: -v\d+, -\d+.\d+.\d+
  3. Strip pod hash suffixes: -[a-z0-9]{5,10}$
  4. Apply user-defined aliases from config

Missed normalization creates phantom nodes and broken edges.
Emit warnings for services appearing only once in the trace window.

---

## 3. Dependency Graph Construction

For each parent-child span pair across different services:
  G.add_edge(parent.service, child.service, weight += 1)

Filter below min_call_frequency. Result: weighted directed graph G.

Edges in G point from caller to callee:
  frontend -> payment-service -> database

This convention is consistent throughout the pipeline. All centrality
computations reference this direction explicitly.

---

## 4. Blast Radius — Blended Centrality

### 4.1 Semantic Model — Callee-Centric Scoring

Blast radius answers: "if this service fails, how many others are affected?"

The answer is determined by how many services depend on this one — i.e., how many
services call it, directly or transitively. This is the CALLEE perspective:

  High blast radius = many services call me (I am a shared dependency)
  Low blast radius  = few services call me (I am a leaf or entry point)

In graph G (caller -> callee):
  - in_degree(v) = number of direct callers = direct dependents
  - A service called by 5 others has in_degree=5 and high blast radius
  - A frontend entry point has in_degree=0 and low blast radius

### 4.2 Implementation — Corrected from Original Spec

The original spec described:

  blast_radius(v) = 0.5 * pagerank(v, G^T) + 0.5 * out_degree_centrality(v, G^T)

Where G^T is the reversed graph (callee -> caller).

This was revised during implementation after semantic analysis revealed a contradiction:

ORIGINAL SPEC PROBLEM:
  G^T reverses edges: callee -> caller.
  pagerank(G^T) performs a random walk on G^T, following callee->caller edges.
  A random walker on G^T flows FROM callees TO callers — rewarding callers, not callees.
  This is backwards: entry points (callers with no callers) accumulate PageRank.

  out_degree_centrality(G^T) = out-degree on reversed graph = in-degree on G.
  This component was correct: in_degree(G) counts direct callers.

  The two components contradicted each other in the original spec.

CORRECTED IMPLEMENTATION:
  pagerank(G, weight='weight')  +  in_degree_centrality(G)

  pagerank(G): random walk follows caller->callee edges.
    A random walker flows along the call graph toward frequently-called services.
    Sinks (services called by many, calling few) accumulate PageRank.
    Terminal dependencies (databases, caches, shared services) score highest.
    Entry points (frontends, load generators) score lowest.
    This correctly rewards high-blast-radius callees.

  in_degree_centrality(G): direct callers of each service.
    Counts "how many services call me directly."
    Equivalent to out_degree_centrality(G^T) — same result, cleaner semantics.

CORRECTED FORMULA:
  blast_radius(v) = w_pr * pagerank(v, G) + w_od * in_degree_centrality(v, G)

  Default: w_pr = 0.5, w_od = 0.5. Configurable via blast_centrality_weights.

Both components normalized to [0,1] before blending.

### 4.3 Why Blend At All

The two metrics answer different questions:
  PageRank (global, iterative): transitive influence — how far does failure propagate?
  In-degree (local, single-hop): direct dependents — what breaks immediately?

Neither alone is sufficient:
  Shallow-wide hub (many services call hub directly):
    high in-degree, moderate PageRank (all dependents may be leaves)
  Deep-narrow chain (A->B->C->D->E, terminal sink E):
    low in-degree on E, but highest PageRank (all flow accumulates at E)

Both are high-risk for different reasons. The blend surfaces both.

### 4.4 Worked Example — Shallow-Wide vs Deep-Narrow

Shallow-wide: A, B, C, D, E all call payment-service
  payment-service: in_degree=5 (high), PageRank moderate
  A..E: in_degree=0, low PageRank

Deep-narrow: root -> X -> Y -> Z -> W
  W: in_degree=1, PageRank highest (all flow accumulates)
  root: in_degree=0, PageRank lowest (no callers, random walk flows away)

With 0.5/0.5 blend:
  payment-service -> high (high in-degree saves it)
  W               -> high (high PageRank saves it)

Both surface as priorities. This is correct:
  payment-service failure breaks 5 services immediately
  W failure cascades through the entire chain

### 4.5 Blend Ratio Sensitivity

THE BLEND RATIO IS A HYPERPARAMETER — not a derived constant.
The 0.5/0.5 default is a neutral prior that avoids penalizing either topology.

Sensitivity sweep for w_pr in [0.3, 0.7] (w_od = 1 - w_pr) is in
/benchmarks/sensitivity/ alongside alpha/beta analysis.
Expected: Kendall tau > 0.85 across w_pr in [0.4, 0.6].

  w_pr=0.7 (PageRank-heavy): under-prioritizes shallow-wide hubs
  w_pr=0.3 (in-degree-heavy): under-prioritizes deep chains
  w_pr=0.5 (default): neutral prior, both topologies surface

---

## 5. Fragility Score

### 5.1 Design Goal
Fragility answers: "Is this service genuinely unstable relative to its load?" The goal is to surface services that are disproportionately failing, regardless of their total traffic volume.

### 5.2 The Fragility Pipeline (Conceptual)

ChaosRank uses a multi-step pipeline to transform raw incident records into a normalized score. This process happens within the **ChaosRank Engine**.

#### Step 1: Traffic-Aware Deduplication
Alert storms often follow a single root cause. ChaosRank collapses logically related incidents within a time window that scales with the service's traffic. This prevents a single failure cascade from being counted as dozens of independent incidents.

#### Step 2: Contextual Normalization
An incident in a low-traffic service is statistically different from an incident in a high-traffic service. ChaosRank evaluates every incident within the context of the service's request volume at that precise moment. This ensures a "fair" comparison across the entire microservice graph.

#### Step 3: Recency Decay
Operational reality changes fast. Recent incidents are more predictive of future failure than those from six months ago. ChaosRank applies an exponential decay to historical data, effectively giving the engine a "memory" window that prioritizes recent stability (or lack thereof).

#### Step 4: Signal Normalization
Finally, raw fragility scores are normalized across the fleet using robust statistical methods (Z-Scaling). This ensures that extreme outliers do not "wash out" the rest of the results, and that the fragility score is on the same [0, 1] scale as the Blast Radius for final risk combination.

### 5.3 High-Traffic Preservation
A key innovation in the ChaosRank engine is the ability to preserve the "fragility signal" even in extremely high-traffic environments. By normalizing *at the moment of the incident* rather than after aggregation, we ensure that a genuinely unstable high-traffic service still ranks highly, while a stable service with occasional minor alerts is correctly deprioritized.

  - High-traffic, proportionally high incident rate   -> scores high   (correct)
  - High-traffic, proportionally low incident rate    -> scores low    (correct)
  - Low-traffic, occasional severe incidents          -> scores appropriately

BENCHMARK VALIDATION:
/benchmarks/fragility-preservation/ includes an explicit test case:
  - frontend (highest traffic): seeded with proportional incident rate
  - payment-service (medium traffic): seeded with disproportionately high rate

ChaosRank must rank payment-service above frontend on fragility despite lower absolute traffic.

---

## 6. Risk Score Combination

The final Risk Score is a weighted blend of the structural impact (Blast Radius) and the operational history (Fragility). 

```
risk(v) = alpha * blast_radius(v) + beta * fragility(v)
```

The **ChaosRank Engine** dynamically optimizes these weights based on the system's maturity and the quality of the incoming signal. In early-stage deployments with sparse incident history, the system naturally prioritizes the structural graph. As operational data accumulates, the fragility signal becomes more influential in the final ranking.

---

## 7. Computational Efficiency

The ChaosRank architecture is designed for low-latency, interactive analysis. By separating the high-compute scoring logic from the data collection SDK, we ensure that:
1.  **SDK is lightweight**: Runs on standard developer machines with minimal memory footprint.
2.  **Engine is scalable**: Can be deployed on serverless infrastructure (like AWS Lambda) to handle processing for thousands of services in milliseconds.

The engine uses optimized graph algorithms and vectorized statistical operations to ensure that even the largest microservice topologies (200+ services) return rankings in near real-time.

---

## 8. Relationship to Prior Work

ChaosRank builds upon decades of research in graph theory and reliability engineering, combining them into a unified prioritization engine.

| Prior Work | Conceptual Relationship |
| :--- | :--- |
| **PageRank & Centrality** | Foundation for measuring service importance in a directed graph. |
| **Fault Tree Analysis** | Inspiration for the Risk = Impact x Likelihood framing. |
| **Chaos Engineering** | The primary application domain for these rankings. |
| **Observability (OTel/Jaeger)** | The source of truth for the system's actual behavior. |

The core contribution of the ChaosRank Engine is the **Contextual Normalization Pipeline**—the unique way we blend real-time traffic statistics with historical incident patterns and transitive graph influence to produce a single, actionable risk metric.

---

## 9. Summary

ChaosRank does not claim novelty in any individual technique. The key contribution is the specific combination of graph-theoretic blast radius scoring, traffic-normalized fragility scoring, and their application to chaos engineering prioritization—providing a deterministic, principled approach to a traditionally subjective field.
chaos engineering tooling.