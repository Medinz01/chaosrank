# ChaosRank — Architecture

> This document describes the system architecture, component responsibilities,
> data flow, and design decisions. For the mathematical derivation, see algorithm.md.

---

## 1. Overview

ChaosRank is a CLI tool. No server, no database, no running cluster required.
Input: trace export + optional incident history + optional async topology.
Output: ranked service list.

```
traces.json ──────────────────────────────────────────────────────────────┐
                                                                           ▼
incidents.csv ──────────────────────────────────────────┐         ┌───────────────┐
                                                         ▼         │               │
                                               ┌──────────────┐   │    ranker     │──► table
                                               │  fragility   │──►│               │──► JSON
                                               └──────────────┘   │  risk = α·BR  │──► litmus YAML
                                                                   │       + β·FR  │
                    ┌──────────────┐                               │               │
traces.json ───────►│ graph builder│──► blast_radius ────────────►│               │
                    └──────────────┘ ▲                             └───────────────┘
                                     │
async-deps.yaml ─────────────────────┘
(merged before scoring)
```

---

## 2. Component Map

```
chaosrank/
├── cli.py                    # Entry point. Typer commands: rank, graph, convert, incidents.
│                             # Orchestrates the pipeline. Owns config loading.
│
├── adapters/                 # Async topology ingestion — external format → async-deps.yaml
│   ├── base.py               # AsyncDepsAdapter ABC — one method: convert(path) -> list[dict]
│   ├── asyncapi.py           # AsyncAPI 2.x spec → async-deps.yaml entries
│   └── kafka.py              # Kafka topic export JSON → async-deps.yaml entries
│
├── incident_adapters/        # Incident ingestion — alerting APIs → list[Incident]
│   ├── base.py               # IncidentAdapter ABC — fetch(window_days) + shared helpers
│   ├── pagerduty.py          # PagerDuty REST API v2
│   ├── alertmanager.py       # Prometheus Alertmanager API
│   ├── grafana_oncall.py     # Grafana OnCall API
│   ├── opsgenie.py           # Opsgenie API
│   └── csv_export.py         # list[Incident] → ChaosRank incidents.csv
│
├── parser/
│   ├── jaeger.py             # Jaeger JSON → {(caller, callee): weight} edge map
│   ├── otlp.py               # OTel OTLP JSON → edge map (Collector + Tempo envelopes)
│   ├── incidents.py          # incidents.csv → ServiceIncidents dataclass
│   ├── async_deps.py         # async-deps.yaml → merged into nx.DiGraph
│   └── normalize.py          # Service name normalization pipeline
│
├── graph/
│   ├── builder.py            # Edge map → NetworkX DiGraph
│   ├── blast_radius.py       # DiGraph → {service: blast_radius_score}
│   └── visualize.py          # DiGraph → Graphviz DOT string
│
├── scorer/
│   ├── fragility.py          # ServiceIncidents → {service: fragility_score}
│   ├── ranker.py             # blast_radius + fragility → ranked list
│   └── suggest.py            # incident history → (fault_type, confidence)
│
└── output/
    ├── table.py              # ranked list → Rich terminal table
    ├── json_out.py           # ranked list → JSON with reasoning field
    └── litmus.py             # ranked list → LitmusChaos ChaosEngine YAML
```

---

## 3. Ingestion Layer

### 3.1 Design principle

ChaosRank has two categories of input: trace data and async topology data.
Both flow through an explicit ingestion layer before reaching the scoring pipeline.
The scoring pipeline (blast_radius, fragility, ranker) never sees raw external formats.

```
External source → [ingestion layer] → internal representation → scoring pipeline
```

The ingestion layer has two responsibilities:
  1. Format conversion — translate external formats to internal representation
  2. User visibility — the user controls and verifies what was extracted
     before it affects rankings

### 3.2 Trace ingestion (current)

Traces are currently privileged: Jaeger JSON is parsed directly by `parser/jaeger.py`
with no adapter abstraction. This is a known asymmetry.

OTel OTLP support is implemented in v0.3 via `parser/otlp.py`:

```
Jaeger JSON     → jaeger.py (direct)
OTel Collector  → otlp.py (_extract_collector)
Tempo/Jaeger v2 → otlp.py (_extract_tempo, auto-detected)
```

The ingestion architecture is now fully symmetric for trace formats:

```
trace format → parser → edge map       → graph builder
async format → adapter → manifest      → async_deps.py → graph edges
```

The canonical internal representation is **typed graph edges**, not the manifest.
The manifest (`async-deps.yaml`) is the interface for manual input and for
formats that do not yet have an adapter. It is not a permanent internal abstraction.

Direct-mode flags (`--kafka`, `--asyncapi`) on `rank` and `graph` bypass the
manifest entirely for CI/CD pipelines. The explicit two-step workflow remains
the documented default for interactive use.

### 3.3 Async topology ingestion

Async dependencies are described in `async-deps.yaml` and merged into the graph
before blast radius scoring. This corrects the ranking inversion described in
`algorithm.md §10` — async producers with many consumers were scoring zero
blast radius because async calls leave no trace spans.

The manifest schema (v1):

```yaml
version: "1"               # schema version — checked by parse_async_deps()
dependencies:
  - producer: order-service
    consumer: inventory-service
    channel: kafka
    topic: order-placed
  - producer: payment-service
    consumer: notification-service
    channel: sqs
    queue: payment-events
```

`version: "1"` is required. parse_async_deps() emits a warning if absent
(backwards compatibility with pre-versioned manifests) and raises on
unrecognised future versions.

### 3.4 Adapter contract

All async topology adapters implement `AsyncDepsAdapter` from `adapters/base.py`:

```python
class AsyncDepsAdapter(ABC):
    def convert(self, input_path: Path) -> list[dict]:
        """Return list of dependency dicts matching async-deps.yaml schema."""

    def source_format(self) -> str:
        """Return the --from flag value this adapter handles."""
```

Adapters return raw names — normalization happens downstream in `parse_async_deps()`.
Adapters are responsible for extraction only. A broken adapter is isolated;
it cannot affect the scoring pipeline.

### 3.5 Supported adapters

  Format          Flag              Input
  ──────────────────────────────────────────────────────────────────────
  AsyncAPI 2.x    --from asyncapi   directory of single-service specs,
                                    or a single multi-service spec file
  Kafka topics    --from kafka      kafka-topics.json export
                                    (see docs/async-deps-guide.md)

  Not supported:  source code parsers (C#, Java, Go integration events)
                  → language-specific, out of scope
                  → see docs/async-deps-guide.md for manual extraction guide

### 3.6 User workflow

The `convert` command is explicit and separate from `rank` by design.
The user sees what was extracted before it touches their rankings.

```bash
# Step 1 — convert and verify
chaosrank convert --from kafka --input ./kafka-topics.json --dry-run
chaosrank convert --from kafka --input ./kafka-topics.json --output ./async-deps.yaml

# Step 2 — rank using the verified manifest
chaosrank rank --traces ./traces.json --async-deps ./async-deps.yaml
```

Direct-mode flags `--kafka` and `--asyncapi` on `rank` and `graph` are available
for CI/CD pipelines where intermediate file inspection is not required. The explicit
two-step convert workflow remains the documented default for interactive use.

---

## 4. Data Flow — `chaosrank rank`

### Step 1: Parse traces → build graph

```
jaeger.py
  input:  traces.json (Jaeger HTTP API export format)
  output: dict[(caller, callee), weight]
  notes:  - Streams via ijson for files >100MB
          - Filters edges below min_call_frequency (default: 10)
          - Normalizes service names at parse time

builder.py
  input:  edge dict
  output: nx.DiGraph G where G[u][v]['weight'] = call frequency
  notes:  - Edges point caller -> callee
          - Emits warning if graph has <5 edges
```

### Step 1b: Merge async dependencies (optional)

```
async_deps.py
  input:  async-deps.yaml, nx.DiGraph G
  output: nx.DiGraph G with async edges added

  - Validates manifest version field
  - Normalizes producer/consumer names via normalize.py
  - Assigns weight = median(trace_edge_weights) to async edges
  - Annotates async edges: edge_type="async", channel, topic
  - Skips: malformed entries, self-loops, duplicate sync edges
  - Async edges render as dashed lines in `chaosrank graph --output dot`

CURRENT LIMITATION — async edge propagation semantics:
  Async edges are currently merged with the same weight as sync edges
  and treated identically in blast radius scoring. This is a simplification.

  Sync failure propagation:  A → B
    failure propagates immediately, high probability

  Async failure propagation: A → topic → B
    producer failure rarely affects consumers directly;
    events queue, retry, and DLQ patterns absorb failures;
    consumer failure does not affect the producer

  A service producing to 12 Kafka consumers does not carry the same blast
  radius as a synchronous gateway calling 12 services. The current model
  overestimates blast radius for async-heavy producers.

  The graph already annotates edge_type="async" on all async edges,
  preserving the information needed for a future fix. A configurable
  async_weight_factor (multiplier on async edge weights before scoring,
  default ~0.5) is planned for v0.3. No schema migration will be required.
```

### Step 2: Compute blast radius

```
blast_radius.py
  input:  nx.DiGraph G
  output: dict[service, float] — scores in [0, 1]

  blast_radius(v) = w_pr * pagerank(v, G) + w_od * in_degree_centrality(v, G)

  pagerank(G):             random walk on G (caller->callee direction)
                           rewards frequently-called sink services
  in_degree_centrality(G): fraction of services that directly call v
  Both components normalized to [0,1] before blending.
  Default: w_pr=0.5, w_od=0.5. Configurable via blast_centrality_weights.

  NOTE: The scoring pipeline code is unchanged by the introduction of async edges.
  However, because async edges change graph topology, blast radius scores change
  when --async-deps is provided. This is expected and correct — the graph now
  reflects a more complete picture of the system's dependency structure.
```

### Step 3: Parse incidents → compute fragility

```
incidents.py
  input:  incidents.csv
  output: ServiceIncidents (dataclass: list of Incident per service)
  notes:  - Deduplicates within traffic-aware burst window
          - Emits warning if request_volume unavailable

fragility.py
  input:  ServiceIncidents
  output: dict[service, float] — scores in [0, 1]

  Pipeline (order matters):
    1. Traffic-aware burst deduplication
    2. Per-incident normalization: w(i) = severity_weight(i) / log(1 + volume)
    3. Exponential decay: sum(w(i) * exp(-lambda * age_days(i)))
    4. Z-score with ±3σ clip, rescaled to [0, 1]
```

### Step 4: Combine → rank

```
ranker.py
  input:  blast_radius dict, fragility dict, alpha, beta
  output: list of RankedService (sorted descending by risk)

  risk(v) = alpha * blast_radius(v) + beta * fragility(v)

  Also computes Kendall tau between blast radius and fragility rankings.
  Emits signal misalignment warning if tau < 0.3.
```

### Step 5: Suggest fault type

```
suggest.py
  input:  ServiceIncidents for the target service
  output: (fault_type: str, confidence: str)

  Maps dominant incident type within decay window to LitmusChaos fault class.
  Confidence depends on signal purity and effective incident count.
```

### Step 6: Render output

```
table.py    → Rich terminal table (default)
json_out.py → JSON array with reasoning field per service
              blast_radius_notes field added when --async-deps provided
litmus.py   → LitmusChaos ChaosEngine manifest YAML
```

---

## 5. Graph Convention

All components share a single graph convention:

  **G: directed graph, edges point caller → callee**
  frontend → payment-service → database

This is the natural trace direction. It is never reversed internally.
The callee model (services called by many = high blast radius) is implemented
by using pagerank(G) and in_degree_centrality(G) directly — no reversal needed.

Async edges follow the same convention:
  **producer → consumer**
  order-service → inventory-service  (via kafka topic: order-placed)

The `reverse_graph()` utility in builder.py exists for visualization and
future use, but is not used in the blast radius computation.

Edge attributes:
  weight      int     call frequency (sync) or median trace weight (async)
  edge_type   str     "sync" (default) | "async"
  channel     str     async only: "kafka" | "rabbitmq" | "sqs" | etc.
  topic       str     async only: topic or queue name

---

## 6. Config Loading

`chaosrank.yaml` is loaded at CLI entry (cli.py). Defaults are hardcoded in
each module and overridden by config values. Config is passed explicitly
through the pipeline — no global state.

```yaml
weights:
  blast_radius: 0.6     # alpha
  fragility: 0.4        # beta

fragility:
  decay_lambda: 0.10
  burst_window_minutes: 5

graph:
  min_call_frequency: 10

output:
  top_n: 5
```

---

## 7. Service Name Normalization

Normalization runs at parse time in normalize.py, not at graph build time.
This ensures the edge map has canonical names before any graph structure is built.

Pipeline:
  1. Lowercase
  2. Strip version: `-v\d+`, `-\d+\.\d+\.\d+`
  3. Strip pod hash: `-[a-z0-9]{5,10}$`
  4. Apply user aliases from config

Adapter output is NOT pre-normalized — adapters return raw names from source specs.
Normalization happens in parse_async_deps() after adapter output is received.
This keeps adapters testable in isolation: adapter tests verify extraction,
not normalization correctness.

Phantom node detection: services appearing only once across all traces
emit a warning — likely a normalization miss or a one-off call.

---

## 8. Cold Start

No incident data → fragility = 0 for all services.

```
risk(v) = alpha * blast_radius(v) + beta * 0.5
        = alpha * blast_radius(v) + 0.2   (at default alpha=0.6, beta=0.4)
```

The 0.5 comes from z-score normalization with uniform scores (all set to 0.5).
Ranking is effectively blast-radius-only. CLI emits a warning.

---

## 9. Output Formats

### Table (default)

Rich terminal table via rich.table. Columns: Rank, Service, Risk, Blast Radius,
Fragility, Suggested Fault, Confidence. Top-N rows shown (configurable).

### JSON

Array of objects. Each object includes all table fields plus:
  reasoning           human-readable explanation of the ranking
  blast_radius_notes  present when --async-deps provided; explains async
                      edge weight assumption (median trace weight)

### LitmusChaos YAML

ChaosEngine manifest for the top-ranked service (or top-N with --top-n).
Includes annotations for risk score, blast radius, fragility, and confidence.
Fault-specific env vars are populated based on suggested fault type:
  latency-injection    → NETWORK_LATENCY, JITTER
  connection-timeout   → NETWORK_PACKET_LOSS_PERCENTAGE, DESTINATION_PORTS
  partial-response     → STATUS_CODE, MODIFY_PERCENT
  pod-failure          → no additional env vars

---

## 10. Benchmark Architecture

The benchmark is a standalone simulation — it does not run a live cluster.

```
benchmarks/
├── convert_deathstar.py    # DeathStarBench CSV → Jaeger JSON
│                           # Extracts topology from execution_paths.txt
│                           # Synthesizes span counts from edge weights
│
├── extract_incidents.py    # Anomaly trace files → incidents CSV
│                           # Compares per-service latency vs baseline
│                           # Severity derived from degradation ratio
│
├── run_comparison.py       # 20-trial simulation
│                           # ChaosRank order: deterministic, computed once
│                           # Random order: shuffled each trial (seed=42)
│                           # Metric: experiments to discover each weakness
│
└── plot_results.py         # Discovery curve chart
                            # Mean cumulative weaknesses found per step
                            # 95% CI bands via ±1.96σ/√n
```

Dataset: UIUC/FIRM DeathStarBench (OSDI 2020), CC0 license.
DOI: 10.13012/B2IDB-6738796_V1

---

## 11. Dependency Summary

```
networkx>=3.2     graph construction, PageRank, in-degree centrality
numpy>=1.26       numerical operations
scipy>=1.11       z-score normalization
typer>=0.9        CLI framework
rich>=13.0        terminal table rendering
pyyaml>=6.0       config loading, LitmusChaos YAML generation, manifest I/O
ijson>=3.2        streaming JSON parser for large trace files
```

No database. No message queue. No external API calls.
All computation is local and offline.

---

## 12. What This Is Not



- Does not inject faults             → use LitmusChaos, Chaos Mesh, or Gremlin
- Does not derive steady-state       → bring your own Prometheus thresholds
- Does not verify experiment results → check your dashboards
- Does not require a running cluster → offline analysis on trace exports
- Does not support OTel protobuf     → JSON-encoded OTLP supported; protobuf v0.4 roadmap
- Does not parse source code         → adapters target structured formats only;
                                       see docs/async-deps-guide.md for manual
                                       topology extraction from codebases