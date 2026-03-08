# Changelog

All notable changes to ChaosRank will be documented here.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).
ChaosRank follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [0.2.0] — 2026-03-08

### Added

**Async topology ingestion layer**
- `--async-deps` flag on `chaosrank rank` and `chaosrank graph` — accepts an
  `async-deps.yaml` manifest describing Kafka/SQS/async relationships missing
  from trace spans. Edges are merged into the graph before blast radius scoring,
  correcting the ranking inversion for async-heavy producers.
- `chaosrank convert` command — converts external async topology formats to
  `async-deps.yaml`. Explicit two-step workflow: convert and verify, then rank.
  Supports `--dry-run` to preview output without writing.
- `--from asyncapi` adapter — parses AsyncAPI 2.x single-service specs from a
  directory (or a single multi-service spec file). Cross-references channels
  across files to emit producer→consumer pairs. Supports channel-level and
  operation-level bindings (kafka, amqp/rabbitmq, sqs, sns, nats, mqtt).
- `--from kafka` adapter — parses a Kafka topic export JSON file describing
  topic names, producers, and consumers. Schema documented in
  `docs/async-deps-guide.md`.
- `adapters/base.py` — `AsyncDepsAdapter` ABC defining the adapter contract.
  All adapters implement one method: `convert(path) -> list[dict]`. Adapters
  return raw names; normalization happens downstream in `parse_async_deps()`.
- `parser/async_deps.py` — merges manifest entries into an existing `nx.DiGraph`.
  Assigns `weight = median(trace_edge_weights)` to async edges. Annotates
  `edge_type="async"`, `channel`, `topic` on all async edges. Skips malformed
  entries, self-loops, and duplicate sync edges.
- Async edges render as dashed lines in `chaosrank graph --output dot`.
- JSON output includes `blast_radius_notes` field when `--async-deps` provided,
  explaining the median weight assumption.
- `version: "1"` field added to `async-deps.yaml` schema. `parse_async_deps()`
  validates on load; warns if absent (backwards compatibility), raises on
  unrecognised future versions.

**Tests — 157 passing (+50)**
- `tests/test_async_deps.py` (19) — manifest parser: edge merging, weight
  assignment, normalization, conflict handling, malformed input
- `tests/test_adapters.py` (36) — AsyncAPI adapter: edge extraction, topic/
  binding extraction, service name extraction, malformed input; Kafka adapter:
  edge extraction, malformed input

**Documentation**
- `docs/architecture.md` — new Section 3 documents the ingestion layer design:
  adapter contract, manifest-vs-edge-list architectural decision, trace ingestion
  asymmetry, async edge propagation limitation, user workflow
- `docs/algorithm.md` — Section 10 updated from "Critical Caveat" to
  "Async/Queue Dependency Support": documents v0.2 implementation, convert
  workflow, and remaining async propagation semantics limitation
- `docs/future-work.md` — async deps section moved to Done; new items added:
  `async_weight_factor`, direct-mode flag, OTel OTLP trace adapter

**Infrastructure**
- `.dockerignore` — excludes `__pycache__`, `*.pyc`, `.pytest_cache`,
  `*.egg-info` to prevent stale compiled files from baking into image layers

### Known limitations

- Async edge propagation semantics: async edges are treated identically to sync
  edges in blast radius scoring. A Kafka producer with 12 consumers receives the
  same blast radius contribution per edge as a synchronous gateway. The graph
  annotates `edge_type="async"` on all async edges — an `async_weight_factor`
  multiplier is planned for v0.3. No schema migration required.
- Source code parsers (C#, Java, Go integration events) are not supported —
  language-specific, out of scope. See `docs/async-deps-guide.md` for manual
  extraction guide.
- OTel OTLP trace format not yet supported — v0.3 roadmap.

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