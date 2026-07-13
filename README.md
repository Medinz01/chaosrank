# ChaosRank CLI

**Chaos engineering requires a hypothesis. ChaosRank tells you where to point it.**

> **Questions? Feedback?** Join our [GitHub Discussions](https://github.com/Medinz01/chaosrank/discussions) to connect with the team.

[![PyPI](https://img.shields.io/pypi/v/chaosrank-cli)](https://pypi.org/project/chaosrank-cli/)
![CI](https://github.com/Medinz01/chaosrank/actions/workflows/ci.yml/badge.svg)
![Python](https://img.shields.io/badge/python-3.11%2B-blue)
![License](https://img.shields.io/badge/license-Apache_2.0-green)

ChaosRank analyzes your service dependency graph and incident history to rank which service to target next. 

It works by collecting your system data and sending it to the **ChaosRank Engine** for high-performance scoring.

---

## Architecture

ChaosRank is split into two components:
1.  **CLI (This repo)**: Trace parsing, graph building, and incident collection.
2.  **Engine**: Core scoring algorithms (Blast Radius, Fragility, Adaptive).

### Running the System:
Both the SDK and the Engine are now distributed via PyPI. You can run the engine locally or deploy it to your own cluster. The CLI communicates with the engine via a standard REST API.

---

## Results

Evaluated on the **DeathStarBench social-network topology** (31 services) from the UIUC/FIRM dataset (OSDI 2020). ChaosRank's engine identifies high-risk services by combining structural importance (traces) and history (incidents).

| Metric | ChaosRank | Random | Improvement |
|---|---|---|---|
| Mean experiments to first weakness | 1.0 | 9.8 | **9.8x** |
| Mean experiments to all weaknesses | 3.0 | 23.2 | **7.8x** |

ChaosRank found all 3 weaknesses in exactly 3 experiments across all 20 trials. Random selection needed 23.2 experiments on average.

---

## How It Works

ChaosRank uses a **Client-Server** model. The SDK (this repo) acts as the "Body," collecting traces and incidents from your environment. These are summarized and sent to the **ChaosRank Engine** (The "Brain") for scoring.

```
traces.json  ──► [ SDK Parser ] ──► [ EngineClient ] ──► [ Hosted Engine ]
incidents.csv ──► [ SDK Parser ] ──► [ EngineClient ] ──► [ Risk Ranking ]
```

The engine provides deterministic rankings based on:
- **Blast Radius**: Transitive impact of failure (Impact).
- **Fragility**: Load-normalized incident history (Likelihood).
- **Adaptive Weights**: Self-correcting risk factors based on experiment outcomes.

---

## Installation

ChaosRank is distributed via PyPI. We recommend installing in a virtual environment:

```bash
pip install chaosrank-cli chaosrank-engine

# Start the engine in the background
uvicorn chaosrank_engine.api.main:app --port 8080 &
```

### From Source

```bash
git clone https://github.com/Medinz01/chaosrank
cd chaosrank
pip install -e .
```

### Configuration

ChaosRank communicates with the engine via HTTP. By default, it expects the engine to be running at `http://localhost:8080`.
Update your `chaosrank.yaml` if you are hosting the engine remotely:

```yaml
engine:
  url: "http://your-engine-host:8080"
```

---

## Usage

### Basic ranking
```bash
chaosrank rank --traces ./traces.json --incidents ./incidents.csv
```

### With async topology (Kafka, SQS, RabbitMQ)
```bash
# Step 1 — convert your async topology source
chaosrank convert --from kafka --input ./kafka-topics.json --output ./async-deps.yaml

# Step 2 — rank with async deps merged
chaosrank rank --traces ./traces.json --async-deps ./async-deps.yaml
```

### Fetch incidents from alerting system
```bash
# From PagerDuty (no manual CSV needed)
chaosrank incidents --from pagerduty --token $PD_TOKEN --window 30d --output incidents.csv
chaosrank rank --traces ./traces.json --incidents incidents.csv
```

---

## Repository Structure

```
chaosrank/
├── chaosrank/
│   ├── cli.py                    # Typer entrypoint: rank, graph, convert
│   ├── engine/                   # Remote Engine Client (Communication layer)
│   ├── adapters/                 # Async topology adapters (AsyncAPI, Kafka)
│   ├── incident_adapters/        # Alerting system adapters (PagerDuty, etc.)
│   ├── parser/                   # Local Trace/Incident parsing & normalization
│   └── output/                   # Table, JSON, Litmus renderers
├── tests/                        # 244+ tests
├── chaosrank.yaml                # Default configuration
└── pyproject.toml
```

---

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for setup, testing, and PR guidelines.

## Changelog

See [CHANGELOG.md](CHANGELOG.md) for version history.

## License

Apache 2.0 — see [LICENSE](LICENSE) for full text.