# ChaosRank — Architecture

> This document describes the system architecture, component responsibilities,
> data flow, and design decisions. For the mathematical derivation, see algorithm.md.

---

## 1. Overview

ChaosRank is a CLI tool. No server, no database, no running cluster required.
Input: trace export + incident history. Output: ranked service list.

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
                    └──────────────┘                               └───────────────┘
```

---

## 2. Component Map

```
chaosrank/
├── cli.py                    # Entry point. Typer commands: rank, graph.
│                             # Orchestrates the pipeline. Owns config loading.
│
├── parser/
│   ├── jaeger.py             # Jaeger JSON → {(caller, callee): weight} edge map
│   ├── incidents.py          # incidents.csv → ServiceIncidents dataclass
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

## 3. Data Flow — `chaosrank rank`

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

  NOTE: The implementation uses pagerank(G) and in_degree_centrality(G) directly.
  An earlier design described pagerank(G^T) + out_degree_centrality(G^T).
  These are NOT equivalent. See algorithm.md §4.2 for the full correction.
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
litmus.py   → LitmusChaos ChaosEngine manifest YAML
```

---

## 4. Graph Convention

All components share a single graph convention:

  **G: directed graph, edges point caller → callee**
  frontend → payment-service → database

This is the natural trace direction. It is never reversed internally.
The callee model (services called by many = high blast radius) is implemented
by using pagerank(G) and in_degree_centrality(G) directly — no reversal needed.

The `reverse_graph()` utility in builder.py exists for visualization and
future use, but is not used in the blast radius computation.

---

## 5. Config Loading

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

## 6. Service Name Normalization

Normalization runs at parse time in normalize.py, not at graph build time.
This ensures the edge map has canonical names before any graph structure is built.

Pipeline:
  1. Lowercase
  2. Strip version: `-v\d+`, `-\d+\.\d+\.\d+`
  3. Strip pod hash: `-[a-z0-9]{5,10}$`
  4. Apply user aliases from config

Phantom node detection: services appearing only once across all traces
emit a warning — likely a normalization miss or a one-off call.

---

## 7. Cold Start

No incident data → fragility = 0 for all services.

```
risk(v) = alpha * blast_radius(v) + beta * 0.5
        = alpha * blast_radius(v) + 0.2   (at default alpha=0.6, beta=0.4)
```

The 0.5 comes from z-score normalization with uniform scores (all set to 0.5).
Ranking is effectively blast-radius-only. CLI emits a warning.

---

## 8. Output Formats

### Table (default)

Rich terminal table via rich.table. Columns: Rank, Service, Risk, Blast Radius,
Fragility, Suggested Fault, Confidence. Top-N rows shown (configurable).

### JSON

Array of objects. Each object includes all table fields plus a `reasoning` field:
a human-readable explanation of why the service was ranked at its position.

### LitmusChaos YAML

ChaosEngine manifest for the top-ranked service (or top-N with --top-n).
Includes annotations for risk score, blast radius, fragility, and confidence.
Fault-specific env vars are populated based on suggested fault type:
  latency-injection    → NETWORK_LATENCY, JITTER
  connection-timeout   → NETWORK_PACKET_LOSS_PERCENTAGE, DESTINATION_PORTS
  partial-response     → STATUS_CODE, MODIFY_PERCENT
  pod-failure          → no additional env vars

---

## 9. Benchmark Architecture

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

## 10. Dependency Summary

```
networkx>=3.2     graph construction, PageRank, in-degree centrality
numpy>=1.26       numerical operations
scipy>=1.11       z-score normalization
typer>=0.9        CLI framework
rich>=13.0        terminal table rendering
pyyaml>=6.0       config loading, LitmusChaos YAML generation
ijson>=3.2        streaming JSON parser for large trace files
```

No database. No message queue. No external API calls.
All computation is local and offline.

---

## 11. What This Is Not

- Does not inject faults           → use LitmusChaos, Chaos Mesh, or Gremlin
- Does not derive steady-state     → bring your own Prometheus thresholds
- Does not verify experiment results → check your dashboards
- Does not require a running cluster → offline analysis on trace exports
- Does not support async deps v1   → --async-deps flag planned for v0.2
- Does not support OTel OTLP v1   → explicitly v0.2 roadmap