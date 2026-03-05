# Changelog

All notable changes to ChaosRank will be documented here.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).
ChaosRank follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [0.1.0] — 2026-03-06

First public release.

### Added

**Core algorithm**
- Blast radius scoring via blended centrality: `pagerank(G) + in_degree_centrality(G)`
- Fragility scoring via four-step pipeline: traffic-aware burst deduplication,
  per-incident traffic normalization, exponential decay, z-score normalization
- Risk score combination: `risk = alpha * blast_radius + beta * fragility`
- Fault type suggestion with confidence matrix (purity × effective sample size)
- Signal misalignment diagnostic — Kendall tau warning when blast radius and
  fragility rankings diverge significantly

**CLI**
- `chaosrank rank` — rank services by risk score
- `chaosrank graph` — export dependency graph as Graphviz DOT
- `--output table` (default), `--output json`, `--output litmus`
- `--config` flag for custom `chaosrank.yaml`
- `--top-n` flag to control output length
- Cold start handling — blast-radius-only ranking when no incident data provided

**Input support**
- Jaeger JSON trace export (Jaeger HTTP API format)
- Streaming parser via `ijson` for trace files >100MB
- Incident history CSV with optional `request_volume` column
- Service name normalization: version stripping, pod hash removal, user aliases

**Output formats**
- Rich terminal table with color-coded confidence levels
- JSON output with per-service `reasoning` field
- LitmusChaos ChaosEngine YAML manifest, ready for `kubectl apply`
- Graphviz DOT export with blast-radius-based node coloring

**Benchmark**
- `benchmarks/convert_deathstar.py` — converts UIUC/FIRM DeathStarBench
  trace dataset to ChaosRank Jaeger JSON format
- `benchmarks/extract_incidents.py` — extracts incidents from anomaly
  injection trace files by comparing against no-interference baseline
- `benchmarks/run_comparison.py` — 20-trial simulation vs random selection
- `benchmarks/plot_results.py` — cumulative discovery curve with 95% CI bands
- Benchmark results: **9.8x faster to first weakness, 7.8x faster to all
  weaknesses** on DeathStarBench social-network (31 services)

**Tests — 107 passing**
- `test_fragility.py` (21) — burst dedup, per-incident normalization,
  fragility preservation, z-score, decay
- `test_blast_radius.py` (15) — callee model, chain ordering, blend weights
- `test_ranker.py` (18) — risk math, cold start, combined signal, fault suggestion
- `test_parser.py` (53) — normalization round-trip, incident parsing, Jaeger parsing

**Documentation**
- `docs/algorithm.md` — full mathematical derivation including PageRank
  direction correction and blast radius semantic model
- `docs/architecture.md` — component map, data flow, graph convention
- `docs/future-work.md` — async deps, OTel OTLP, learned weights roadmap

### Known limitations

- Jaeger JSON only — OTel OTLP planned for v0.2
- Async dependencies (Kafka, SQS, etc.) not captured in trace spans —
  potential ranking inversion for event-driven architectures (see README)
- Single-region topology only
- Z-score normalization less stable below 10 services

---

## [Unreleased]

### Planned for v0.2

- `--async-deps` flag — accept async dependency manifest, merge into graph
- OTel OTLP trace format support
- Sensitivity analysis sweep — alpha/beta + Kendall tau charts
- Betweenness centrality as opt-in blast radius component
- Multi-region topology support
