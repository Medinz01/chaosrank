# Contributing to ChaosRank CLI

Thanks for your interest. This document covers how to get set up, run tests, and submit changes to the CLI SDK.

---

## Setup

**Requirements:** Python 3.11+, Docker (optional but recommended)

### Option A — Local

```bash
git clone https://github.com/Medinz01/chaosrank
cd chaosrank
pip install -e ".[dev]"
```

### Option B — Docker (matches CI environment exactly)

```bash
docker compose build
docker compose run chaosrank
# Inside container:
pip install -e ".[dev]"
```

---

## Benchmark dataset (optional)

The benchmark scripts require the UIUC/FIRM DeathStarBench dataset.
Download from: https://doi.org/10.13012/B2IDB-6738796_V1

The converted traces are already committed in `benchmarks/real_traces/` so the dataset is only needed if you want to re-run the conversion scripts.

---

## Running Tests

```bash
# Full suite
pytest tests/ -v

# Single file
pytest tests/test_parser_otlp.py -v

# With coverage
pytest tests/ --cov=chaosrank --cov-report=term-missing
```

All tests must pass before submitting a PR.

---

## Linting

```bash
ruff check chaosrank/
ruff check tests/
```

ChaosRank uses `ruff` for linting. Configuration is in `pyproject.toml`.
CI runs ruff on every push — fix warnings before submitting.

---

## Project Structure

```
chaosrank/          Core library
├── cli.py                    # Typer entrypoint: rank, graph, convert
├── engine/                   # Remote Engine Client (Communication layer)
├── adapters/                 # Async topology adapters (AsyncAPI, Kafka)
├── incident_adapters/        # Alerting system adapters (PagerDuty, etc.)
├── parser/                   # Local Trace/Incident parsing & normalization
└── output/                   # Table, JSON, Litmus renderers
tests/              Test suite — mirrors chaosrank/ structure
benchmarks/         Benchmark scripts and real trace data
testdata/           Small sample fixtures for manual testing
```

---

## Making Changes

### CLI & Parsers

The SDK handles trace parsing, incident collection, and communication with the **ChaosRank Engine**. When making changes:
- Keep the parser logic lightweight.
- Ensure any new adapters implement the necessary base classes.
- Maintain backward compatibility for the `chaosrank.yaml` configuration.

### Adding a new incident adapter

1. Create `chaosrank/incident_adapters/your_system.py`
2. Implement `IncidentAdapter` subclass
3. Add tests using `unittest.mock` to mock HTTP calls (no network required in CI)

### Adding a new output format

1. Create `chaosrank/output/your_format.py`
2. Implement `render_your_format(ranked: list[dict]) -> str`
3. Wire into `cli.py` output dispatch block
4. Add at least one integration test

---

## Submitting a PR

1. Fork the repo and create a branch: `git checkout -b your-feature`
2. Make your changes
3. Run `pytest tests/ -v` — all tests must pass
4. Run `ruff check chaosrank/ tests/` — no warnings
5. Update `CHANGELOG.md` under `[Unreleased]`
6. Open a PR with a clear description of what changed and why

### PR checklist

- [ ] Tests pass (`pytest tests/ -v`)
- [ ] Lint clean (`ruff check chaosrank/ tests/`)
- [ ] CHANGELOG updated

---

## Reporting Issues

Open a GitHub issue with:
- ChaosRank version (`chaosrank --version`)
- Python version (`python --version`)
- What you did, what you expected, what happened
- Minimal reproducing example if possible
