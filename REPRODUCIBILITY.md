# Reproducing the BakedBoston simulation

This repository contains a synthetic academic simulation. It does not dispatch
real deliveries, contact organizations, or use observed volunteer decisions.

The published evidence package is in
[`results/benchmark_2026_10_10`](results/benchmark_2026_10_10). It contains
the raw result rows, summary statistics, paired comparisons, derived seeds, and
the exact input/source hashes used for the run.

## 1. Set up the environment

Use Python 3.11 or newer and a non-production Gurobi license appropriate for
academic use. From the repository root:

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install --upgrade pip
python3 -m pip install -e .
python3 -m gurobipy.license
```

The last command confirms that Gurobi is licensed on the machine. If it does
not succeed, follow Gurobi's license instructions before running a simulation.

## 2. Validate the committed evidence package

This checks the files, row coverage, and every deterministic seed in the
published package. It does not rerun the optimization.

```bash
python3 -m bakedboston_optimizer.verify_benchmark \
  results/benchmark_2026_10_10 \
  --repository-root .
```

The command should report 7,500 replication rows. A source-hash warning is
expected after changing simulation code; it means the current source no longer
matches the source used to generate the committed results.

## 3. Run a quick reproducibility check

Run two replications per operating condition first. This produces 30
policy-level results: three conditions × two replications × five policies.

```bash
python3 -m bakedboston_optimizer.benchmark \
  data/academic_comparison_snapshot.json \
  --start-date 2026-08-24 \
  --replications 2 \
  --master-seed 20261010 \
  --horizon-days 5 \
  --matching-interval-minutes 60 \
  --max-simultaneous-drivers 3 \
  --output-dir results/benchmark-smoke-check \
  --progress-every 1

python3 -m bakedboston_optimizer.verify_benchmark \
  results/benchmark-smoke-check \
  --repository-root .
```

Delete `results/benchmark-smoke-check` after inspection; it is not a published
result package.

## 4. Re-run the full academic benchmark

The full experiment runs 500 seeded five-day replications in each of three
conditions: standard operations, scarce drivers, and high surplus. Every
policy sees the same generated scenario within a replication. The original
run produced 7,500 policy-level rows and took about 65 minutes on an Apple
Silicon development machine; run time varies with hardware and Gurobi.

```bash
python3 -m bakedboston_optimizer.benchmark \
  data/academic_comparison_snapshot.json \
  --start-date 2026-08-24 \
  --replications 500 \
  --master-seed 20261010 \
  --horizon-days 5 \
  --matching-interval-minutes 60 \
  --max-simultaneous-drivers 3 \
  --output-dir results/benchmark-replay \
  --progress-every 10
```

Do not overwrite the committed package when reproducing it. Write into a new
directory such as `results/benchmark-replay` and validate that directory after
the run.

## 5. Compare a rerun with the published package

For the same configuration, the rerun should have the same number of rows,
condition identifiers, policy identifiers, and seed values. The package
manifest records the source and fixture hashes needed to explain a mismatch.

Use these files as the evidence trail:

| File | Purpose |
| --- | --- |
| `reproduction_manifest.json` | Complete configuration, seed list, fixture/source hashes, runtime, and confidence-interval method. |
| `replication_metrics.csv` | One raw row for each condition × replication × policy. |
| `summary_statistics.csv` | Mean, sample deviation, range, and 95% confidence interval for each metric. |
| `paired_bakedboston_deltas.csv` | Matched BakedBoston-versus-comparator differences. |
| `results_section.md` | Tables generated from the raw rows for the written report. |

For model details and the adaptation boundaries for comparison policies, see
[`docs/benchmark-protocol.md`](docs/benchmark-protocol.md) and
[`docs/comparison-models.md`](docs/comparison-models.md).
