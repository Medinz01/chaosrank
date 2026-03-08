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

Fragility answers: "Is this service genuinely unstable relative to its load?"

Three failure modes to avoid:
  1. Burst bias        — one failure cascade firing 20 alerts scores as 20 incidents
  2. Traffic bias      — high-traffic services accumulate more incidents regardless
  3. Over-penalization — a genuinely fragile high-traffic service washes toward the mean

The pipeline below addresses all three. Order matters.

### 5.2 Step 1 — Burst Deduplication (Traffic-Aware)

Naive dedup: collapse incidents of the same type on the same service within a fixed
5-minute window. Problem: for a high-traffic service, 20 alerts in 5 minutes may
represent a genuine multi-faceted failure cascade. A fixed window collapses real signal.

Traffic-aware burst window:

  burst_window(v, t) = base_window * log(1 + request_volume_at(v, t) / baseline_volume)

Where:
  request_volume_at(v, t) = request volume of service v at time t of the incident
                             (point-in-time, NOT window average)
  baseline_volume          = median of each service's mean request volume over the
                             observation window — one scalar for typical load

High-traffic services get a wider dedup window — but only when alert rate scales
with traffic at time t. If alert rate spikes faster than traffic at that moment,
the window does not expand. That divergence is real signal.

base_window defaults to 5 minutes. Configurable.

### 5.3 Step 2 — Per-Incident Traffic Normalization

This is the critical correction to naive post-hoc normalization.

THE PROBLEM WITH NORMALIZING AFTER AGGREGATION:

Consider two services over 7 days:

  Service A (payment):  10,000 req/s,  5 high-severity incidents
  Service B (notifier):    100 req/s,  2 high-severity incidents

Naive — apply decay, then divide by traffic (severity_weight=0.602 for high):

  raw_fragility(A) = 5 * 0.602 = 3.010
  raw_fragility(B) = 2 * 0.602 = 1.204

  fragility(A) = 3.010 / log(1 + 10000) = 3.010 / 9.21 = 0.327
  fragility(B) = 1.204 / log(1 +   100) = 1.204 / 4.61 = 0.261

Service A has 2.5x more severe incidents but scores only 25% higher.
At extreme differentials (100,000 req/s vs 100 req/s), a service with
10x more incidents can rank below a quieter one. Broken.

THE FIX — NORMALIZE PER INCIDENT, BEFORE AGGREGATION:

  weighted_incident(i) = severity_weight(i) / log(1 + request_volume_at(i))

request_volume_at(i) = request volume of service v at time of incident i.

Same example with per-incident normalization:

  weighted(A, each) = 0.602 / log(1 + 10000) = 0.602 / 9.21 = 0.065
  weighted(B, each) = 0.602 / log(1 +   100) = 0.602 / 4.61 = 0.131

  raw_fragility(A) = 5 * 0.065 = 0.325
  raw_fragility(B) = 2 * 0.131 = 0.262

Service A scores higher (0.325 vs 0.262). The 2.5x incident differential is
preserved as a proportional, interpretable score differential.

Fallback chain:
  1. request_volume_at(i) unavailable -> use window average (warn)
  2. window average unavailable       -> skip normalization (warn)

### 5.4 Step 3 — Exponential Decay

Apply recency weighting after per-incident normalization:

  fragility(v) = sum( weighted_incident(i) * exp(-lambda * age_days(i)) )

LAMBDA'S SINGLE RESPONSIBILITY:

With per-incident normalization handling traffic bias, lambda has exactly one job:
recency weighting. It no longer compensates for traffic imbalance. This decoupling
is intentional — one knob, one behavior.

Valid lambda range:

  lambda = 0.05  ->  ~60-day effective memory  (stable, infrequent-deploy systems)
  lambda = 0.10  ->  ~30-day effective memory  (default)
  lambda = 0.20  ->  ~15-day effective memory  (fast-moving, high-deploy-frequency)

Effective memory = -log(0.05) / lambda (age at which incident contributes <5%).

### 5.5 Step 4 — Robust Normalization (Z-Score, Clipped)

MinMax normalization is rejected. With 11 services where one has 30 incidents
and the rest have 0-2, MinMax compresses all others to ~0.02-0.08.

Z-score with clipping:

  z(v)                    = (fragility_raw(v) - mean_all) / stddev_all
  fragility_normalized(v) = (clip(z(v), -3, 3) + 3) / 6

Properties:
  Outlier services score high without collapsing all others toward zero
  Below-mean services receive meaningful differentiation from each other
  Clip at +-3 sigma prevents extreme outliers from dominating the scale
  Rescale to [0,1] for combination with blast_radius on the same scale

Edge case: stddev = 0 (all services identical fragility) -> all set to 0.5,
warning emitted: "Fragility scores are uniform. Incident data may be insufficient."

Note: z-score estimates are less statistically stable below 10 services.
In small graphs, fragility scores should be interpreted directionally.

### 5.6 Complete Pipeline

  For each service v:

    1. logical_incidents(v) = deduplicate(
         incidents(v),
         window = base_window * log(1 + request_volume_at(v, t) / baseline_volume)
       )

    2. For each incident i:
         w(i) = severity_weight(i) / log(1 + request_volume_at(i))

    3. fragility_raw(v) = sum( w(i) * exp(-lambda * age_days(i)) )

    4. fragility(v) = zscore_clip_rescale( fragility_raw(v) )  # clip +-3sigma, rescale [0,1]

### 5.7 High-Traffic Service Fragility Preservation

THE CONCERN:
log normalization might over-penalize high-traffic services with genuinely
severe incident rates, washing their scores toward the mean.

WHY PER-INCIDENT NORMALIZATION PRESERVES THE SIGNAL:
Normalizing each incident at time of occurrence evaluates every event in its own
context. The aggregated score reflects "how abnormal were incidents relative to load"
— not "how many total incidents occurred."

  - High-traffic, proportionally high incident rate   -> scores high   (correct)
  - High-traffic, proportionally low incident rate    -> scores low    (correct)
  - Low-traffic, occasional severe incidents          -> scores appropriately

BENCHMARK VALIDATION:
/benchmarks/fragility-preservation/ includes an explicit test case:
  - frontend (highest traffic): seeded with proportional incident rate
  - payment-service (medium traffic): seeded with disproportionately high rate

ChaosRank must rank payment-service above frontend on fragility despite lower
absolute traffic. Verified in test_fragility.py::TestFragilityPreservation.

---

## 6. Risk Score Combination

  risk(v) = alpha * blast_radius(v) + beta * fragility(v)

Default: alpha=0.6, beta=0.4.

WHY alpha > beta:
A structurally critical but currently stable service is more dangerous to ignore
than an unstable leaf. If the high-blast-radius service fails unexpectedly, the
blast radius is realized at full scale.

Expected damage asymmetry:
  expected_damage(failure) ~ P(failure) * blast_radius

Weighting blast radius higher operationalizes this.

SENSITIVITY ANALYSIS:
Sweep alpha in [0.4, 0.8] in steps of 0.1.
Measure Kendall's tau (rank correlation) between rankings at each alpha value.

Expected: tau > 0.85 across alpha in [0.5, 0.7].
Degradation at extremes is expected and documented.

If tau drops significantly within [0.5, 0.7] for a given system, the two signals
are misaligned for that deployment — surfaced as a diagnostic warning to the user.

See /benchmarks/sensitivity/ for full results.

---

## 7. Fault Type Suggestion

Maps dominant incident type in recent history to suggested fault class:

  Dominant Signal        Suggested Fault                Rationale
  ─────────────────────────────────────────────────────────────────────
  p99 latency spike      latency-injection              Exposes timeout handling
  error rate breach      partial-response/wrong-status  Exposes error handling
  timeout incident       connection-timeout             Exposes retry/fallback logic
  no history             pod-failure                    Safe default
  mixed/unclear          pod-failure                    Conservative default

Confidence is a function of BOTH signal purity AND sample size within the
effective decay window (incidents with weighted contribution > 5%).

  effective_n(v)    = count of incidents within effective decay window
  signal_purity(v)  = fraction of effective_n dominated by one incident type

Confidence matrix:

  effective_n    purity > 0.70    purity 0.50-0.70    mixed
  >= 5           high             medium              low
  2-4            medium           low                 low
  < 2            low              low                 low

---

## 8. Cold Start Behavior

No incident history: fragility = 0 for all services.
Risk reduces to:

  risk(v) = alpha * blast_radius(v)

Documented, expected, graceful degradation.
CLI emits: "No incident data. Ranking by blast radius only.
            Provide --incidents to enable fragility scoring."

---

## 9. Computational Complexity

All operations run interactively on a laptop for typical deployments
(10-200 services, millions of spans).

  Step                      Complexity      Notes
  Graph build from traces   O(S)            S = number of spans
  PageRank (k iterations)   O(k * E)        E = edges, k converges ~50 iters
  In-degree centrality      O(V + E)        V = services
  Fragility scoring         O(I)            I = logical incidents after dedup
  Sorting / ranking         O(V log V)      Final rank output
  Overall                   O(S + k*E + I)  Dominated by trace parsing at scale

Practical numbers, 31-service DeathStarBench system, 6000 spans, 31 incidents:
  Graph build ~5ms  |  PageRank ~10ms  |  Fragility <5ms  |  Total <50ms

NetworkX pagerank() and in_degree_centrality() are used directly.
SciPy zscore() handles normalization. No custom graph implementation needed.
ijson (streaming JSON parser) is used for trace files >100MB to avoid loading
the full file into memory.

---

## 10. Async/Queue Dependency Support

### 10.1 The Original Problem

ChaosRank builds its dependency graph from synchronous trace spans. In many modern
architectures, the most critical dependencies flow through async channels:
  - Kafka / Pulsar topic producers and consumers
  - SQS / SNS publisher-subscriber relationships
  - gRPC streaming connections
  - Event-driven choreography patterns

None of these produce parent-child span relationships in Jaeger traces.

A service that produces to a Kafka topic consumed by 10 downstream services appears
in ChaosRank's graph as having zero trace-visible dependents. It receives a low
blast radius score and is deprioritized — a potential systematic ranking inversion,
not merely a coverage gap.

WHO IS AFFECTED:
  Primarily synchronous (REST, gRPC request-response): largely unaffected
  Heavy async messaging (event sourcing, CQRS, stream processing): significant risk

### 10.2 Mitigation — --async-deps flag (implemented in v0.2)

The --async-deps flag accepts an async-deps.yaml manifest describing async
relationships. These edges are merged into the graph before blast radius scoring.

  chaosrank rank --traces ./traces.json --async-deps ./async-deps.yaml

The manifest is populated either manually or via the `chaosrank convert` command:

  chaosrank convert --from asyncapi --input ./specs/ --output ./async-deps.yaml
  chaosrank convert --from kafka    --input ./kafka-topics.json --output ./async-deps.yaml

Supported adapters: AsyncAPI 2.x specs, Kafka topic export JSON.
See architecture.md §3 for the full ingestion layer design.

When --async-deps is provided, the startup warning is suppressed and replaced
with a confirmation log showing the count of async edges merged.

### 10.3 Remaining Limitation — Async Edge Propagation Semantics

Introducing async edges into the graph corrects the ranking inversion for
async-heavy producers. However, the current model treats async edges identically
to sync edges in blast radius scoring. This is a simplification.

Sync failure propagation:
  A -> B
  Failure in A propagates to B immediately, with high probability.

Async failure propagation:
  A -> topic -> B
  Producer failure (A) rarely affects consumers (B) directly.
  Consumer failure (B) does not affect the producer (A).
  Events queue, retry, and DLQ patterns absorb transient failures.
  The coupling is looser than synchronous calls.

CONSEQUENCE:
A service producing to 12 Kafka consumers does not have the same blast radius
as a synchronous gateway calling 12 services. The current model overestimates
blast radius for async-heavy producers.

The graph already annotates edge_type="async" on all async edges, preserving
the information needed for a future fix:

  async_weight_factor = configurable multiplier on async edge weights (v0.3)
  default ~0.5 -- reflects lower failure propagation probability
  tunable per deployment based on observed failure patterns

No schema migration will be required when async_weight_factor is implemented.
The edge annotation is already in place.

---

## 11. Known Limitations

  Limitation                    Impact                        Status
  ──────────────────────────────────────────────────────────────────────
  Async propagation semantics   Blast radius overestimated    See Section 10.3
                                for async-heavy producers     async_weight_factor v0.3
  Async deps: source code       No C#/Java/Go parsers         docs/async-deps-guide.md
  Single-region topology        Misses cross-region radius    Future work
  Jaeger format only            Narrow trace input support    v0.3: OTel OTLP adapter
  Static alpha/beta             Optimal weights vary          Future: learned
  Blend ratio (w_pr/w_od)       Topology-dependent tuning     Configurable
  Betweenness centrality        Transitive paths missing      Future: opt flag
  Point-in-time request vol     Requires enriched CSV         Falls back to avg
  Z-score below 10 services     Less statistically stable     Interpret directionally
  Manifest as internal format   May be bypassed by adapters   Tracked in architecture.md

---

## 12. Relationship to Prior Work

  Prior Work               Relationship
  ─────────────────────────────────────────────────────────────────────
  PageRank (1998)          One component of blast radius scoring
  Fault Tree Analysis      Conceptual framing: risk = impact x likelihood
  Netflix Chaos Monkey     Motivating baseline for benchmarks
  Jepsen                   Inspiration for principled, reproducible eval
  ChaosEater (2024)        Most similar; ChaosRank is deterministic, LLM-free
  FIRM / UIUC (OSDI 2020)  Benchmark dataset source (DeathStarBench traces)

ChaosRank does not claim novelty in any individual technique.
The contribution is the combination of:
  - Graph-theoretic blast radius (blended centrality on dependency graph)
  - Per-incident traffic-normalized fragility scoring
  - Their application to chaos experiment prioritization

This combination is an open problem in OSS chaos engineering tooling.