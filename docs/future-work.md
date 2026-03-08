# ChaosRank — Future Work

Tracked improvements, in rough priority order.

---

## Done

### Async Dependency Support (`--async-deps`) — shipped in v0.2.0

Accepts an `async-deps.yaml` manifest describing Kafka/SQS/async relationships.
Edges merged into the dependency graph before blast radius scoring.

`chaosrank convert` command converts external formats to the manifest:
- `--from asyncapi` — AsyncAPI 2.x single-service or multi-service specs
- `--from kafka` — Kafka topic export JSON

See `docs/architecture.md §3` and `docs/async-deps-guide.md`.

---

## v0.3 — Next

### 1. Async Edge Propagation Semantics (`async_weight_factor`)

**Problem:** Async edges are currently treated identically to sync edges in
blast radius scoring. A Kafka producer with 12 consumers receives the same
blast radius contribution per edge as a synchronous gateway calling 12 services.

Async failure propagation is fundamentally different:
- Producer failure rarely affects consumers directly
- Consumer failure does not affect the producer
- Events queue, retry, and DLQ patterns absorb transient failures

The current model overestimates blast radius for async-heavy producers.

**Planned implementation:**
Configurable `async_weight_factor` in `chaosrank.yaml` — a multiplier applied
to async edge weights before blast radius scoring. Default ~0.5.

```yaml
graph:
  async_weight_factor: 0.5   # async edges contribute half the blast radius of sync edges
```

All async edges are already annotated `edge_type="async"` in v0.2. No schema
migration required. Change is isolated to `blast_radius.py`.

---

### 2. OTel OTLP Trace Format

**Problem:** v0.1.0 only parses Jaeger JSON export format. Teams using
OpenTelemetry Collector with OTLP exporters (Tempo, Jaeger v2, Honeycomb,
Lightstep) cannot use ChaosRank without format conversion.

**Planned implementation:**
- `chaosrank/adapters/otlp.py` — OTLP trace adapter, normalizes directly to
  graph edges following the ingestion layer pattern from v0.2
- Service identity via `resource.attributes["service.name"]`
- Same normalization pipeline as Jaeger (normalize.py)
- Auto-detect format at parse time (Jaeger vs OTLP) or explicit `--format` flag
- No change to downstream graph/scorer pipeline

Implementation decisions to make before starting:
- Direct edge extraction vs Jaeger-like intermediate representation
  (direct is simpler; intermediate reuses more existing code)
- Protobuf vs JSON-encoded OTLP (JSON first; protobuf as follow-on)

---

### 3. Direct-Mode Ingestion Flag

**Problem:** The two-step `convert → rank` workflow is explicit and verifiable,
but adds friction in CI/CD pipelines where intermediate file inspection is not
required.

**Planned implementation:**
Direct format flags on `chaosrank rank`:

```bash
chaosrank rank --traces traces.json --asyncapi ./specs/
chaosrank rank --traces traces.json --kafka ./kafka-topics.json
```

Calls adapter internally, bypasses manifest file. Architecture already supports
this — adapters are isolated. Explicit two-step workflow remains the documented
default for interactive use.

---

### 4. Sensitivity Analysis (`benchmarks/sensitivity/`)

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

## v0.4 — Research

### 5. Betweenness Centrality (Opt-in)

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

### 6. Learned Alpha/Beta Weights

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

### 7. Multi-Region Topology

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
| Source code parsers | Language-specific, out of scope | docs/async-deps-guide.md |