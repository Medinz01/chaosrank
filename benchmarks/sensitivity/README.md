# Sensitivity Analysis

Measures ranking stability across hyperparameter ranges via Kendall tau.

## What it sweeps

| Hyperparameter | Default | Sweep range | Expected stable range |
|---|---|---|---|
| `alpha` (blast radius weight) | 0.6 | [0.4, 0.8] step 0.05 | tau > 0.85 for [0.5, 0.7] |
| `w_pr` (PageRank blend weight) | 0.5 | [0.3, 0.7] step 0.05 | tau > 0.85 for [0.4, 0.6] |

Kendall tau measures rank correlation against the baseline ranking at default values.
tau = 1.0 means identical ranking. tau = 0.85 is the stability threshold.

## Usage

```bash
# From repo root
python benchmarks/sensitivity/run_sensitivity.py

# Custom inputs
python benchmarks/sensitivity/run_sensitivity.py \
  --traces path/to/traces.json \
  --incidents path/to/incidents.csv
```

Requires `matplotlib` for charts:

```bash
pip install chaosrank[benchmark]
# or
pip install matplotlib
```

## Output

```
benchmarks/sensitivity/results/
├── alpha_sweep.csv     # alpha, beta, kendall_tau, is_baseline, stable
├── w_pr_sweep.csv      # w_pr, w_od, kendall_tau, is_baseline, stable
├── alpha_sweep.png     # tau vs alpha chart
├── w_pr_sweep.png      # tau vs w_pr chart
└── summary.txt         # human-readable conclusion
```

## Interpretation

If tau drops below 0.85 within the expected stable range for your deployment,
the two signals (blast radius and fragility) are misaligned. ChaosRank will
surface a warning during `rank`. Inspect both signals before tuning weights.

Degradation at the extremes (alpha < 0.5 or alpha > 0.7) is expected and documented.
