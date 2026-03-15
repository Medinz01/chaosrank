# ChaosRank — Future Work

Tracked improvements, in rough priority order.

---

## Done

### Async Dependency Support (`--async-deps`) — shipped in v0.2.0

Accepts an `async-deps.yaml` manifest describing Kafka/SQS/async relationships.
`chaosrank convert` converts AsyncAPI 2.x specs and Kafka topic exports to the manifest.

### OTel OTLP Trace Adapter — shipped in v0.3.0

`--format otlp` on `rank` and `graph`. Supports OTel Collector JSON
(`resourceSpans`) and Tempo/Jaeger v2 (`batches`). Auto-detects envelope.

### Alerting System Adapters — shipped in v0.3.0

`chaosrank incidents --from <pagerduty|alertmanager|grafana-oncall|opsgenie>`
fetches incident history directly. No manual CSV required.

### `async_weight_factor` — shipped in v0.3.0

`--async-weight-factor` flag and `graph.async_weight_factor` in config.
Default 0.5 — async edges contribute half the blast radius of sync edges.

### Direct-Mode Ingestion Flags — shipped in v0.3.0

`--kafka` and `--asyncapi` flags on `rank` and `graph`. One-step async
topology ingestion without intermediate manifest file.

### Sensitivity Analysis — shipped in v0.3.0

`benchmarks/sensitivity/run_sensitivity.py`. Results on DeathStarBench:
alpha stable in [0.50, 0.70], w_pr stable in [0.30, 0.70].

---

## v0.4 — Next

### 1. Betweenness Centrality (Opt-in)

**Problem:** PageRank and in-degree centrality miss services that sit on
critical paths without being high-volume. A service on the only path between
two clusters scores low on both metrics despite being a single point of failure.

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

### 2. OTel Protobuf Support

**Problem:** `parser/otlp.py` currently handles JSON-encoded OTLP only.
Teams exporting directly from the OTel Collector in protobuf format cannot
use `--format otlp` without conversion.

**Planned implementation:**
Extend `parser/otlp.py` to detect and parse binary protobuf OTLP exports.
Requires `protobuf` or `opentelemetry-proto` as an optional dependency.

---

### 3. Learned Alpha/Beta Weights

**Problem:** The default alpha=0.6, beta=0.4 is a principled prior but not
optimal for all deployments. A system with rich incident history should weight
fragility more heavily.

**Planned implementation:**
Bayesian update of alpha/beta based on incident data density and signal
alignment (Kendall tau). Requires experiment outcome tracking.
This is the feature that most naturally drives a SaaS layer.

---

### 4. Multi-Region Topology

**Problem:** ChaosRank builds a single graph. In multi-region deployments,
the blast radius is overstated for cross-region dependencies with independent
failure domains.

**Planned implementation:**
Region tag on services via config or trace metadata. Per-region subgraphs.
Regional blast radius surfaced alongside global.

---

### 5. Confluent Schema Registry Adapter

**Problem:** Teams using Confluent Kafka can query producer/consumer topology
directly from the Schema Registry REST API without maintaining a topic export
file.

**Planned implementation:**
`chaosrank convert --from confluent --url http://schema-registry:8081`
Calls Confluent Schema Registry API, maps subjects to producer/consumer pairs.

---

## Not Planned (Scope Boundaries)

| Feature | Why Out of Scope | Better Tool |
|---|---|---|
| Fault injection | ChaosRank ranks experiments, not runs them | LitmusChaos, Gremlin |
| Steady-state verification | Requires SLO definitions | Steadybit, Prometheus |
| Experiment result tracking | Requires persistent state | LitmusChaos dashboard |
| Real-time streaming ranking | Requires live trace access | Future SaaS layer |
| Service mesh integration | Istio/Envoy out of scope | Future work |
| Source code parsers | Language-specific, out of scope | docs/async-deps-guide.md |