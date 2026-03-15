# ChaosRank

**Stop running random chaos experiments. Run the right one next.**

![CI](https://github.com/Medinz01/chaosrank/actions/workflows/ci.yml/badge.svg)
![Python](https://img.shields.io/badge/python-3.11%2B-blue)
![License](https://img.shields.io/badge/license-MIT-green)
![Tests](https://img.shields.io/badge/tests-244%20passing-brightgreen)
![PyPI](https://img.shields.io/pypi/v/chaosrank-cli)

ChaosRank analyzes your service dependency graph and incident history to rank which service to break first — so your chaos experiments find real weaknesses instead of wasting cycles on low-risk services.

```
Rank  Service                    Risk   Blast  Fragility  Suggested Fault     Confidence
1     composepost-uploadcreator  0.888  0.907  0.860      latency-injection   medium
2     composepost-uploadmedia    0.866  0.907  0.805      latency-injection   medium
3     urlservice-upload          0.770  0.669  0.922      latency-injection   low
4     composepost-uploadurl      0.738  1.000  0.341      pod-failure         low
5     nginx-compose-post         0.200  0.000  0.393      pod-failure         low
```

---

## The Problem

Chaos engineering teams face a prioritization problem: given a system with 20+ microservices, which service should you break first?

Today the answer is gut feel, "whatever failed last week", or random selection. None of these are principled. A payment service with 15 downstream dependents is not the same risk as an internal logging sidecar — but most teams treat them identically.

**Core framing:** `risk = impact × likelihood`

- **Blast radius** estimates impact — how many services are affected if this one fails?
- **Fragility** estimates likelihood — based on incident history, how probable is degradation?

ChaosRank estimates both, combines them, and tells you which service to target next.

---

## Results

Evaluated on the **DeathStarBench social-network topology** (31 services) from the UIUC/FIRM dataset (OSDI 2020). Three high-risk services were identified as weaknesses based on structural importance and anomaly injection history.

![Discovery curve](benchmarks/results/discovery_curve.png)

| Metric | ChaosRank | Random | Improvement |
|---|---|---|---|
| Mean experiments to first weakness | 1.0 | 9.8 | **9.8x** |
| Mean experiments to all weaknesses | 3.0 | 23.2 | **7.8x** |

ChaosRank found all 3 weaknesses in exactly 3 experiments across all 20 trials. Random selection needed 23.2 experiments on average.

> **Methodology note:** Service topology and incident data are derived from the UIUC/FIRM DeathStarBench dataset (CC0 license). The topology reflects real microservice call graphs. Incident data was extracted by comparing per-service latency in anomaly-injected trace files against the no-interference baseline (~7x degradation → critical severity). This is a simulation benchmark — ChaosRank does not inject faults itself.

---

## How It Works

### Risk Score

```
risk(service) = alpha * blast_radius(service) + beta * fragility(service)
```

Default: `alpha=0.6, beta=0.4`. Blast radius is weighted higher because a stable-but-critical service is more dangerous to ignore than an unstable leaf — its failure would be high-impact and potentially surprising.

### Blast Radius — Blended Centrality

```
blast_radius(v) = 0.5 * pagerank(v, G) + 0.5 * in_degree_centrality(v, G)
```

Built from your Jaeger trace data. PageRank captures transitive influence (how far does failure propagate?). In-degree centrality captures direct dependents (what breaks immediately?). Neither alone is sufficient — the blend surfaces both shallow-wide hubs and deep dependency chains.

### Fragility Score — Four-Step Pipeline

1. **Traffic-aware burst deduplication** — collapses alert storms proportionally to traffic volume, preserving genuine failure cascades
2. **Per-incident traffic normalization** — each incident evaluated in its own traffic context, preventing high-traffic services from being unfairly penalized
3. **Exponential decay** — recent incidents weighted more heavily (`lambda=0.10` → ~30-day effective memory)
4. **Z-score normalization** — outlier services score high without collapsing all others toward zero (MinMax rejected for this reason)

Severity weights use a log scale: `critical=1.000, high=0.602, medium=0.301, low=0.100`.

See [docs/algorithm.md](docs/algorithm.md) for the full mathematical derivation.

### Fault Type Suggestion

| Dominant Signal | Suggested Fault | Confidence |
|---|---|---|
| p99 latency spike | `latency-injection` | high if purity >70% and n ≥ 5 |
| error rate breach | `partial-response` | high if purity >70% and n ≥ 5 |
| timeout incident | `connection-timeout` | medium if purity >50% and n ≥ 3 |
| no history | `pod-failure` | low (cold start default) |
| mixed/unclear | `pod-failure` | low (conservative default) |

---

## Installation

```bash
pip install chaosrank-cli
# or isolated install (recommended)
pipx install chaosrank-cli
```

**Requirements:** Python 3.11+

### From source

```bash
git clone https://github.com/Medinz01/chaosrank
cd chaosrank
pip install -e ".[dev]"
```

### Docker

```bash
docker compose build
docker compose run chaosrank
```

---

## Usage

### Basic ranking

```bash
chaosrank rank \
  --traces ./traces.json \
  --incidents ./incidents.csv
```

### JSON output

```bash
chaosrank rank \
  --traces ./traces.json \
  --incidents ./incidents.csv \
  --output json
```

### Pipe directly to LitmusChaos

```bash
chaosrank rank \
  --traces ./traces.json \
  --incidents ./incidents.csv \
  --output litmus | kubectl apply -f -
```

### With async topology (Kafka, SQS, RabbitMQ)

```bash
# Step 1 — convert your async topology source
chaosrank convert --from kafka --input ./kafka-topics.json --dry-run
chaosrank convert --from kafka --input ./kafka-topics.json --output ./async-deps.yaml

# Step 2 — rank with async deps merged
chaosrank rank --traces ./traces.json --async-deps ./async-deps.yaml
```

### Fetch incidents from alerting system

```bash
# From PagerDuty (no manual CSV needed)
chaosrank incidents --from pagerduty --token $PD_TOKEN --window 30d --output incidents.csv
chaosrank rank --traces ./traces.json --incidents incidents.csv

# From Alertmanager
chaosrank incidents --from alertmanager --url http://alertmanager:9093 --window 30d --output incidents.csv

# Dry-run to preview
chaosrank incidents --from pagerduty --token $PD_TOKEN --window 7d --dry-run
```

### OTel OTLP traces

```bash
# OTel Collector JSON or Tempo/Jaeger v2 — auto-detected
chaosrank rank --traces ./otlp_traces.json --format otlp
```

### Visualize the dependency graph

```bash
# Sync topology only
chaosrank graph --traces ./traces.json --output dot | dot -Tpng > graph.png

# With async edges (shown as dashed lines)
chaosrank graph --traces ./traces.json --async-deps ./async-deps.yaml --output dot | dot -Tpng > graph.png
```

---

## Input Formats

### Traces — Jaeger JSON

Standard Jaeger HTTP API export format. Export from your Jaeger instance:

```bash
curl "http://jaeger:16686/api/traces?service=frontend&limit=5000" > traces.json
```

ChaosRank streams files >100MB via `ijson` to avoid memory issues.

### Incidents — CSV

```csv
timestamp,service,type,severity,request_volume
2026-02-10T08:00:00Z,payment-service,error,critical,9000
2026-02-14T15:00:00Z,productcatalog-service,latency,high,12000
2026-02-20T11:00:00Z,cart-service,error,medium,5000
```

| Column | Required | Description |
|---|---|---|
| `timestamp` | Yes | ISO 8601 |
| `service` | Yes | Service name (normalized automatically) |
| `type` | Yes | `error`, `latency`, `timeout` |
| `severity` | Yes | `critical`, `high`, `medium`, `low` |
| `request_volume` | No | Per-service request count at incident time. Falls back to window average, then skips normalization with warning. |

---

## Configuration

**`chaosrank.yaml`** (place in working directory or pass via `--config`):

```yaml
weights:
  blast_radius: 0.6   # alpha — blast radius weight
  fragility: 0.4      # beta  — fragility weight

fragility:
  decay_lambda: 0.10          # recency decay (0.05=60d, 0.10=30d, 0.20=15d)
  burst_window_minutes: 5     # base alert dedup window

graph:
  min_call_frequency: 10      # filter noisy edges

output:
  top_n: 5

# Optional: service name aliases
aliases:
  payments: payment-service
  auth: authentication-service
```

### Tuning `alpha` and `beta`

| Scenario | Recommendation |
|---|---|
| New system, no incident history | Increase `alpha` (blast radius only) |
| Mature system with rich incident data | Decrease `alpha`, increase `beta` |
| Signal misalignment warning fires | Review — blast radius and fragility are disagreeing. Inspect both signals before tuning. |

---

## Service Name Normalization

OTel exporters often emit versioned or hashed service names. ChaosRank normalizes automatically:

```
payment-service-v2-7d9f8b  →  payment-service
payment-service-1.2.3      →  payment-service
Payment-Service-v2-abc12f  →  payment-service
```

Pipeline: lowercase → strip version patterns → strip pod hash suffixes → apply aliases.

---

## Prior Art & Positioning

| Tool | Experiment Selection | Gap |
|---|---|---|
| LitmusChaos | Manual, declarative CRDs | No ranking or guidance |
| Chaos Mesh | Manual workflow definition | No risk awareness |
| Gremlin | UI-driven, some "advice" | Closed source, not graph-based |
| Steadybit | Reliability hints (rule-based) | No dependency graph, no incidents |
| ChaosEater | LLM-driven hypotheses | Non-deterministic, not reproducible |

ChaosRank does not claim novelty in any individual technique. The contribution is the combination of graph-theoretic blast radius scoring, per-incident traffic-normalized fragility scoring, and their application to chaos experiment prioritization. This combination is an open problem in OSS chaos engineering tooling.

---

## Known Limitations

| Limitation | Impact | Status |
|---|---|---|
| Async propagation semantics | Blast radius overestimated for async-heavy producers | `async_weight_factor=0.5` default — configurable via `--async-weight-factor` |
| Async deps: source code | No C#/Java/Go event class parsers | See `docs/async-deps-guide.md` for manual extraction |
| OTel protobuf | JSON-encoded OTLP only | Protobuf support planned for v0.4 |
| Single-region topology | Misses cross-region blast radius | Future work |
| Static alpha/beta | Optimal weights vary by system | Future: learned weights |
| Z-score less stable below 10 services | Directional scores only | Documented |
| Point-in-time request volume | Requires enriched incident CSV | Falls back gracefully |

### Async dependency support

ChaosRank builds its dependency graph from synchronous trace spans. Services that produce to Kafka topics, SQS queues, or other async channels **do not appear as dependents** in trace data without additional input. A Kafka producer with 10 downstream consumers would show zero blast radius from traces alone.

Use `--async-deps` to describe your async topology:

```bash
# From a Kafka topic export
chaosrank convert --from kafka --input ./kafka-topics.json --output ./async-deps.yaml
chaosrank rank --traces ./traces.json --async-deps ./async-deps.yaml

# From AsyncAPI 2.x specs
chaosrank convert --from asyncapi --input ./specs/ --output ./async-deps.yaml
chaosrank rank --traces ./traces.json --async-deps ./async-deps.yaml
```

Use `--dry-run` to verify extraction before it affects rankings. See `docs/async-deps-guide.md` for the manifest format and manual extraction guide.

---

## What ChaosRank Is Not

- **Does not inject faults** → use LitmusChaos, Chaos Mesh, or Gremlin
- **Does not derive steady-state** → bring your own Prometheus thresholds
- **Does not verify results** → check your dashboards or Steadybit
- **Does not need a running cluster** → offline analysis on trace exports
- **Does not support OTel protobuf** → JSON-encoded OTLP supported; protobuf v0.4 roadmap
- **Does not parse source code** → see `docs/async-deps-guide.md` for manual topology extraction

---

## Benchmark Reproduction

```bash
# Convert DeathStarBench traces to ChaosRank format
python benchmarks/convert_deathstar.py \
  --app social-network \
  --data-dir /path/to/tracing-data \
  --output benchmarks/real_traces/social_network.json

# Extract incidents from anomaly injection files
python benchmarks/extract_incidents.py \
  --app social-network \
  --data-dir /path/to/tracing-data \
  --output benchmarks/real_traces/social_network_incidents.csv

# Run 20-trial comparison
python benchmarks/run_comparison.py

# Generate chart
python benchmarks/plot_results.py
```

Dataset: Qiu et al., *FIRM: An Intelligent Fine-grained Resource Management Framework for SLO-oriented Microservices*, OSDI 2020.
DOI: [10.13012/B2IDB-6738796_V1](https://doi.org/10.13012/B2IDB-6738796_V1) — CC0 license.

---

## Development

```bash
# Run tests
pytest tests/ -v

# Lint
ruff check chaosrank/

# Run with verbose logging
chaosrank rank --traces traces.json --incidents incidents.csv --verbose
```

### Test coverage

| Suite | Tests | What it validates |
|---|---|---|
| `test_parser.py` | 53 | Normalization round-trip, incident parsing, Jaeger edge extraction |
| `test_fragility.py` | 21 | Burst dedup, per-incident normalization, fragility preservation, z-score, decay |
| `test_blast_radius.py` | 15 | Callee model, chain ordering, blend weights, graph reversal |
| `test_ranker.py` | 18 | Risk math, cold start, combined signal, fault suggestion |
| `test_async_deps.py` | 19 | Manifest parser: edge merging, weight assignment, normalization, conflict handling |
| `test_adapters.py` | 36 | AsyncAPI adapter (edge/topic/binding extraction), Kafka adapter (edge extraction, malformed input) |
| `test_parser_otlp.py` | 39 | OTel Collector JSON, Tempo/Jaeger v2, auto-detection, service name normalization |
| `test_incident_adapters.py` | 48 | PagerDuty, Alertmanager, Grafana OnCall, Opsgenie — all HTTP mocked |

The fragility preservation test is load-bearing for the benchmark: it asserts that a medium-traffic service with disproportionately high incident rate ranks above a high-traffic service with proportional incidents — the case that post-hoc normalization gets wrong.

---

## Repository Structure

```
chaosrank/
├── chaosrank/
│   ├── cli.py                    # Typer entrypoint: rank, graph, convert
│   ├── adapters/
│   │   ├── base.py               # AsyncDepsAdapter ABC
│   │   ├── asyncapi.py           # AsyncAPI 2.x → async-deps.yaml
│   │   └── kafka.py              # Kafka topic export → async-deps.yaml
│   ├── incident_adapters/
│   │   ├── base.py               # IncidentAdapter ABC
│   │   ├── pagerduty.py          # PagerDuty REST API v2
│   │   ├── alertmanager.py       # Prometheus Alertmanager API
│   │   ├── grafana_oncall.py     # Grafana OnCall API
│   │   ├── opsgenie.py           # Opsgenie API
│   │   └── csv_export.py         # list[Incident] → ChaosRank CSV
│   ├── parser/
│   │   ├── normalize.py          # Service name normalization
│   │   ├── jaeger.py             # Jaeger JSON trace parser
│   │   ├── otlp.py               # OTel OTLP trace parser (Collector + Tempo)
│   │   ├── incidents.py          # Incident CSV parser
│   │   └── async_deps.py         # async-deps.yaml → graph edges
│   ├── graph/
│   │   ├── builder.py            # NetworkX DiGraph construction
│   │   ├── blast_radius.py       # Blended centrality scoring
│   │   └── visualize.py          # DOT/Graphviz export
│   ├── scorer/
│   │   ├── fragility.py          # Four-step fragility pipeline
│   │   ├── ranker.py             # Risk score combination
│   │   └── suggest.py            # Fault type suggestion
│   └── output/
│       ├── table.py              # Rich table renderer
│       ├── json_out.py           # JSON output with reasoning
│       └── litmus.py             # LitmusChaos ChaosEngine manifest
├── tests/                        # 244 tests
├── benchmarks/
│   ├── convert_deathstar.py      # DeathStarBench → Jaeger JSON converter
│   ├── extract_incidents.py      # Anomaly traces → incident CSV extractor
│   ├── run_comparison.py         # 20-trial benchmark
│   ├── plot_results.py           # Discovery curve chart
│   └── real_traces/              # Converted DeathStarBench data
├── docs/
│   ├── algorithm.md              # Full mathematical derivation
│   ├── architecture.md           # Component map and data flow
│   └── future-work.md            # v0.2 roadmap
├── chaosrank.yaml                # Default configuration
├── pyproject.toml
└── Dockerfile
```

---

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for setup, testing, and PR guidelines.

## Documentation

- [docs/algorithm.md](docs/algorithm.md) — full mathematical derivation
- [docs/architecture.md](docs/architecture.md) — component map, data flow, ingestion layer design
- [docs/async-deps-guide.md](docs/async-deps-guide.md) — async manifest format and manual extraction guide
- [docs/future-work.md](docs/future-work.md) — roadmap
- [benchmarks/sensitivity/](benchmarks/sensitivity/) — hyperparameter stability analysis

## Changelog

See [CHANGELOG.md](CHANGELOG.md) for version history.

## License

MIT — see [LICENSE](LICENSE) for full text.