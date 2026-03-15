# Changelog

All notable changes to ChaosRank will be documented here.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).
ChaosRank follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [0.3.0] — 2026-03-15

### Added

**OTel OTLP trace adapter**
- `parser/otlp.py` — parses OTel Collector JSON (`resourceSpans` envelope)
  and Tempo/Jaeger v2 JSON (`batches` envelope). Auto-detects envelope shape
  at parse time — no subtype flag needed.
- `--format otlp` flag on `chaosrank rank` and `chaosrank graph`. Default
  remains `jaeger` for backward compatibility.
- `graph/builder.py` — `trace_format` parameter routes to the correct parser.
- `tests/test_parser_otlp.py` — 39 tests: edge extraction, service name
  normalization, Tempo envelope, auto-detection, malformed input.
- `tests/fixtures/otlp_trace.json` — synthetic OTel Collector JSON fixture
  (10 calls per edge, passes `min_call_frequency=10` in CI without config).
- `tests/fixtures/otlp_tempo_trace.json` — synthetic Tempo/Jaeger v2 fixture.
- CI smoke test: `chaosrank rank --format otlp` with ranked output assertion.

**Alerting system adapters for incident ingestion**
- `chaosrank/incident_adapters/` — new adapter layer parallel to async deps
  adapters. Removes the primary adoption barrier: teams no longer need to
  hand-craft a CSV to use ChaosRank.
- `incident_adapters/base.py` — `IncidentAdapter` ABC with shared
  `infer_type()` and `normalize_severity()` helpers.
- `incident_adapters/pagerduty.py` — PagerDuty REST API v2, paginated,
  urgency→severity mapping.
- `incident_adapters/alertmanager.py` — Prometheus Alertmanager API, service
  extracted from labels (`service` > `job` > `app` priority order).
- `incident_adapters/grafana_oncall.py` — Grafana OnCall API, service from
  alert payload labels.
- `incident_adapters/opsgenie.py` — Opsgenie API, service from `service:<name>`
  tag convention, P1–P5 priority→severity mapping.
- `incident_adapters/csv_export.py` — converts `list[Incident]` to ChaosRank
  incident CSV format.
- `chaosrank incidents` command — fetches incidents from an alerting system
  and exports as ChaosRank CSV. Flags: `--from`, `--token`, `--url`,
  `--window`, `--output`, `--dry-run`.
- `tests/test_incident_adapters.py` — 48 tests, all HTTP calls mocked via
  `unittest.mock`. No network required in CI.
- `tests/fixtures/pagerduty_incidents.json` and `alertmanager_alerts.json` —
  synthetic API response fixtures.
- CI smoke tests: help registration, invalid format exit, missing token exit,
  dry-run via mock HTTP.

**`async_weight_factor` — async edge propagation semantics**
- `graph/blast_radius.py` — `async_weight_factor` parameter (default 0.5)
  applied to async edge weights before blast radius scoring. Fixes known model
  limitation: async edges were treated identically to sync edges. A Kafka
  producer with 12 consumers no longer scores the same blast radius as a
  synchronous gateway calling 12 services.
- `_apply_async_weight()` helper — copies G and scales async edge weights
  before PageRank and in-degree centrality. Returns G unchanged when no async
  edges present or factor == 1.0 (no unnecessary copy).
- `--async-weight-factor` flag on `chaosrank rank` (default 0.5, range (0.0, 1.0]).
- Config file support: `graph.async_weight_factor` in `chaosrank.yaml`.
- `chaosrank.yaml` updated with `async_weight_factor: 0.5` entry and comment.

**Direct-mode ingestion flags**
- `--kafka <file>` and `--asyncapi <file-or-dir>` flags on `chaosrank rank`
  and `chaosrank graph`. Converts and merges async topology in one step without
  an intermediate `async-deps.yaml` file.
- Conflict guard: at most one of `--async-deps`, `--kafka`, `--asyncapi`.
- `--async-deps` unchanged — explicit two-step workflow still supported and
  remains the documented default for interactive use.

**Sensitivity analysis**
- `benchmarks/sensitivity/run_sensitivity.py` — sweeps `alpha` in [0.4, 0.8]
  and `w_pr` in [0.3, 0.7] measuring Kendall tau vs default baseline.
- Results on DeathStarBench social-network (31 services):
  - alpha stable range: [0.50, 0.70] (tau ≥ 0.85) — confirms spec prediction
  - w_pr stable range: [0.30, 0.70] (tau ≥ 0.85) — wider than predicted
  - Signal misalignment (tau=0.10 between blast radius and fragility) documented
    as a real finding on this dataset: structural centrality and incident
    frequency are decorrelated in the DeathStarBench social-network topology.
- `benchmarks/sensitivity/README.md`
- `benchmarks/sensitivity/results/` — CSVs, PNG charts, `summary.txt`.

**Tests — 244 passing (+87 since v0.2.0)**

### Known limitations

- `type` field in incident adapters is a heuristic: keyword match on alert
  title/name (`latency`, `timeout`, `error`, `fail`). Falls back to `error`.
  Alerting systems do not emit `error/latency/timeout` natively.
- `request_volume` is always `None` from alerting adapters — these APIs do not
  carry traffic data. Scorer handles `None` gracefully (falls back to window
  average).
- No new dependencies: HTTP via stdlib `urllib.request`.
- OTel protobuf format not yet supported — JSON only.
- Tempo/Jaeger v2 auto-detection uses a 512-byte prefix scan for the streaming
  path; works for all well-formed files.

---

## [0.2.0] — 2026-03-08

### Added

**Async topology ingestion layer**
- `--async-deps` flag on `chaosrank rank` and `chaosrank graph`
- `chaosrank convert` command with `--from asyncapi` and `--from kafka`
- `adapters/base.py` — `AsyncDepsAdapter` ABC
- `adapters/asyncapi.py` — AsyncAPI 2.x single/multi-service spec support
- `adapters/kafka.py` — Kafka topic export JSON support
- `parser/async_deps.py` — manifest parser, median edge weight, edge_type annotation
- Async edges render as dashed lines in `chaosrank graph --output dot`
- `version: "1"` schema field in `async-deps.yaml`
- **Tests — 157 passing (+50)**

---

## [0.1.0] — 2026-03-06

First public release.

### Added

**Core algorithm**
- Blast radius scoring via blended centrality: `pagerank(G) + in_degree_centrality(G)`
- Fragility scoring: traffic-aware burst deduplication, per-incident traffic
  normalization, exponential decay, z-score normalization
- Risk score: `risk = alpha * blast_radius + beta * fragility`
- Fault type suggestion with confidence matrix
- Signal misalignment diagnostic (Kendall tau warning)

**CLI** — `chaosrank rank`, `chaosrank graph`, `--output table/json/litmus`

**Input** — Jaeger JSON, incident CSV, service name normalization

**Benchmark** — 9.8x faster to first weakness, 7.8x faster to all weaknesses
on DeathStarBench social-network (31 services)

**Tests — 107 passing**