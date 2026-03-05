# ChaosRank — Future Work

Tracked improvements beyond v0.1.0, in rough priority order.

---

## v0.2 — Planned

### 1. Async Dependency Support (`--async-deps`)

**Problem:** ChaosRank builds its dependency graph from synchronous Jaeger
trace spans. Async dependencies — Kafka topics, SQS queues, RabbitMQ exchanges,
Pub/Sub subscriptions — produce no parent-child spans. A Kafka producer consumed
by 10 downstream services appears in ChaosRank's graph as having zero dependents.
This is a potential ranking inversion, not just a missing feature.

**Planned implementation:**
Accept a YAML manifest describing async relationships:

```yaml
async_deps:
  - producer: order-service
    consumers:
      - inventory-service
      - notification-service
      - analytics-service
    channel: kafka
    topic: orders.created

  - producer: payment-service
    consumers:
      - ledger-service
      - fraud-service
    channel: sqs
    queue: payment-events
```

These edges are merged into graph G before blast radius scoring, with a
configurable weight (default: same as median synchronous edge weight).

**Who is affected:** Any architecture using event sourcing, CQRS,
stream processing, or choreography-based sagas.

---

### 2. OTel OTLP Trace Format

**Problem:** v0.1.0 only parses Jaeger JSON export format. Teams using
OpenTelemetry Collector with OTLP exporters (Tempo, Jaeger v2, Honeycomb,
Lightstep) cannot use ChaosRank without format conversion.

**Planned implementation:**
- Add `chaosrank/parser/otlp.py` — parse OTLP JSON and protobuf formats
- Auto-detect format at parse time (Jaeger vs OTLP)
- No change to downstream graph/scorer pipeline

---

### 3. Sensitivity Analysis (`benchmarks/sensitivity/`)

**Problem:** The spec promises a sensitivity sweep for alpha in [0.4, 0.8]
measuring Kendall tau between rankings. This validates that the default
alpha=0.6 is a reasonable prior and that the ranking is stable in a
neighborhood around it.

**Planned implementation:**
`benchmarks/sensitivity/run_sensitivity.py`:
- Sweep alpha in [0.4, 0.8], steps of 0.05
- At each alpha, compute full ranking on DeathStarBench social-network
- Compute Kendall tau against alpha=0.6 baseline
- Plot: x=alpha, y=Kendall tau — expect tau > 0.85 for alpha in [0.5, 0.7]

Same sweep for w_pr (PageRank weight in blast radius blend).

---

## v0.3 — Research

### 4. Betweenness Centrality (Opt-in)

**Problem:** PageRank and in-degree centrality capture "how many depend on me"
but miss services that sit on critical paths without being high-volume.
A service on the only path between two clusters scores low on both metrics
despite being a single point of failure.

**Planned implementation:**
Optional `--betweenness` flag. Adds betweenness centrality as a third
component in the blast radius blend:

```
blast_radius(v) = w_pr * pagerank(v, G)
               + w_od * in_degree_centrality(v, G)
               + w_bc * betweenness_centrality(v, G)
```

Off by default: O(VE) cost makes it slow on large graphs.
Acceptable for offline analysis on graphs < 500 nodes.

---

### 5. Learned Alpha/Beta Weights

**Problem:** The default alpha=0.6, beta=0.4 is a principled prior but not
optimal for all deployments. A system with rich incident history should weight
fragility more heavily. A new system with no history should weight blast radius
exclusively.

**Planned implementation:**
Bayesian update of alpha/beta based on:
- Incident data density (more incidents → higher beta)
- Signal alignment (high Kendall tau → stable weights)
- User feedback on past experiment outcomes

Requires experiment outcome tracking — which experiments ran, what they found.
This is the feature that most naturally drives a SaaS layer.

---

### 6. Multi-Region Topology

**Problem:** ChaosRank builds a single graph from traces. In multi-region
deployments, a service failure in us-east-1 may not propagate to eu-west-1.
The blast radius is overstated for cross-region dependencies with independent
failure domains.

**Planned implementation:**
- Accept region tag on services (via config or trace metadata)
- Build per-region subgraphs
- Compute blast radius within region and cross-region separately
- Surface regional blast radius alongside global

---

## Not Planned (Scope Boundaries)

These are explicitly out of scope for ChaosRank. Other tools do them better.

| Feature | Why Out of Scope | Better Tool |
|---|---|---|
| Fault injection | ChaosRank ranks experiments, not runs them | LitmusChaos, Gremlin |
| Steady-state verification | Requires SLO definitions | Steadybit, Prometheus |
| Experiment result tracking | Requires persistent state | LitmusChaos dashboard |
| Real-time streaming ranking | Requires live trace access | Future SaaS layer |
| Service mesh integration | Istio/Envoy out of scope v1 | Future work |
