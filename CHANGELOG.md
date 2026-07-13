# Changelog

All notable changes to ChaosRank will be documented here.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).
ChaosRank follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [1.0.3] — 2026-07-13

### Added
- **Modular CLI Architecture**: Extensively refactored the monolithic `cli.py` into a highly maintainable `chaosrank/commands/` structure (`rank`, `graph`, `convert`, `adaptive`, `federation`, `orchestration`).
- **Local UI Dashboard**: Introduced `chaosrank dashboard`, a new built-in local web server (`dashboard_server.py`) with a bundled frontend (`ui_dist/`) for visualizing topology and ranked results directly in the browser.
- **Datadog Integration**: Added Datadog support to incident adapters (`chaosrank incidents --from datadog`).
- **Confluent Schema Registry**: Added Confluent support to async topology adapters (`adapters/confluent.py`).

### Changed
- **Keyless Open Core**: Removed all API key, token, and tier restrictions from the `EngineClient` and configuration schemas. The CLI now connects seamlessly to any self-hosted engine without SaaS authentication.
- **Documentation**: Cleaned up the `README.md` and removed legacy SaaS architecture documentation from the `docs/` folder to reflect the new open-source structure.

---

## [1.0.2] — 2026-07-13

### Changed
- Minor bug fixes and PyPI updates.

---

## [1.0.1] — 2026-04-03

### Added
- **Public Tier Support**: Introduces a default shared API key (`chaosrank-public-dev`) for immediate out-of-the-box testing.
- **Access Tiers**: Added documentation for Public vs. Pro keys in `README.md`.

### Changed
- Defaulted `chaosrank.yaml` to the new public access key.
- Updated documentation links in `README.md`.

---

## [1.0.0] — 2026-04-03

### Changed
- **Open-Core Refactoring**: ChaosRank is now an API Client.
- The proprietary scorer (fragility and blast radius models) has been migrated to the private ChaosRank-Engine SaaS backend.
- The public CLI now acts as a Domain-Agnostic Adapter Hub handling trace parsing, async topology generation, and incident aggregation.
- Introduces `EngineClient` for HTTP communication with the remote scaling backend.
- Local command logic now gracefully fails if the engine API is offline or unreachable.

---

## [0.3.0] — 2026-03-15

### Added
- **OTel OTLP trace adapter**
  - `parser/otlp.py` — parses OTel Collector JSON and Tempo/Jaeger v2 JSON.
  - `--format otlp` flag on `chaosrank rank` and `chaosrank graph`.
- **Alerting system adapters for incident ingestion**
  - `chaosrank/incident_adapters/` — ingests directly from PagerDuty, Alertmanager, Grafana OnCall, and Opsgenie.
  - `chaosrank incidents` command — fetches incidents and exports as ChaosRank CSV.
- **async_weight_factor** — Fixes blast radius overestimation for async-heavy producers. Default 0.5.
- **Direct-mode ingestion flags** — `--kafka <file>` and `--asyncapi <file>` on rank and graph.
- **Sensitivity analysis** — `benchmarks/sensitivity/run_sensitivity.py`.

### Fixed
- Known limitations with incident types.

---

## [0.2.0] — 2026-03-08

### Added
- **Async topology ingestion layer**
- `--async-deps` flag on `chaosrank rank` and `chaosrank graph`
- `chaosrank convert` command with `--from asyncapi` and `--from kafka`
- `adapters/asyncapi.py` and `adapters/kafka.py`

---

## [0.1.0] — 2026-03-06

### Added
- **Initial public release**.
- Blast radius scoring via blended centrality.
- Fragility scoring via burst deduplication.
- Fault type suggestion with confidence matrix.