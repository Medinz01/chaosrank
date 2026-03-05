# Contributing to ChaosRank

Thanks for your interest. This document covers how to get set up, run tests,
and submit changes.

---

## Setup

**Requirements:** Python 3.11+, Docker (optional but recommended)

### Option A — Local

```bash
git clone https://github.com/yourname/chaosrank
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

Mount it when starting the container:
```bash
# Linux / macOS
docker compose run -v /path/to/tracing-data:/data chaosrank

# Windows
docker compose run -v "C:\path\to\tracing-data:/data" chaosrank
```

The converted traces are already committed in `benchmarks/real_traces/`
so the dataset is only needed if you want to re-run the conversion scripts.

---

## Running Tests

```bash
# Full suite
pytest tests/ -v

# Single file
pytest tests/test_fragility.py -v

# Single test
pytest tests/test_fragility.py::TestFragilityPreservation::test_payment_ranks_above_frontend -v

# With coverage
pytest tests/ --cov=chaosrank --cov-report=term-missing
```

All 107 tests must pass before submitting a PR.

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
tests/              Test suite — mirrors chaosrank/ structure
docs/               Algorithm derivation, architecture, future work
benchmarks/         Benchmark scripts and real trace data
testdata/           Small sample fixtures for manual testing
```

Read `docs/algorithm.md` before touching `blast_radius.py` or `fragility.py`.
The mathematical justification for every design decision is there.
The key correctness guarantee is `test_fragility.py::TestFragilityPreservation` —
do not break this test.

---

## Making Changes

### Blast radius

The semantic model is callee-centric: services called by many score high.
Graph G has edges pointing caller → callee. Centrality uses `pagerank(G)`
and `in_degree_centrality(G)` — no graph reversal in the hot path.
See `docs/algorithm.md §4` for why `pagerank(G^T)` was rejected.

### Fragility pipeline

Order matters: dedup → per-incident normalization → decay → z-score.
Per-incident normalization (Step 2) must happen before aggregation.
Post-hoc normalization produces ranking inversions at high traffic differentials.
See `docs/algorithm.md §5.3` for the worked example.

### Adding a new output format

1. Create `chaosrank/output/your_format.py`
2. Implement `render_your_format(ranked: list[dict]) -> str`
3. Wire into `cli.py` output dispatch block
4. Add at least one integration test

### Adding a new fault type

1. Update `chaosrank/scorer/suggest.py` — add mapping in `FAULT_MAP`
2. Update `docs/algorithm.md §7` — add row to the fault table
3. Update `chaosrank/output/litmus.py` — add env vars in `_env_for_fault()`
4. Add test in `test_ranker.py::TestFaultSuggestion`

---

## Submitting a PR

1. Fork the repo and create a branch: `git checkout -b your-feature`
2. Make your changes
3. Run `pytest tests/ -v` — all 107 must pass
4. Run `ruff check chaosrank/ tests/` — no warnings
5. Update `CHANGELOG.md` under `[Unreleased]`
6. Open a PR with a clear description of what changed and why

### PR checklist

- [ ] Tests pass (`pytest tests/ -v`)
- [ ] Lint clean (`ruff check chaosrank/ tests/`)
- [ ] CHANGELOG updated
- [ ] If algorithm changed: `docs/algorithm.md` updated
- [ ] If architecture changed: `docs/architecture.md` updated

---

## Reporting Issues

Open a GitHub issue with:
- ChaosRank version (`chaosrank --version`)
- Python version (`python --version`)
- What you did, what you expected, what happened
- Minimal reproducing example if possible

---

## Areas Where Help Is Wanted

```
--async-deps flag       Accept Kafka/SQS dependency manifest
OTel OTLP support       Currently Jaeger JSON only
Sensitivity analysis    Alpha sweep + Kendall tau charts
Betweenness centrality  Opt-in blast radius component
Multi-region support    Cross-region blast radius scoring
```

These are tracked in `docs/future-work.md` and the GitHub issue tracker.
