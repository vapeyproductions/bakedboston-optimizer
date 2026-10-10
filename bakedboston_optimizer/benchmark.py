"""Reproducible repeated-simulation benchmark for the academic comparison.

This module deliberately sits above the optimizer.  It does not change a
policy's feasible routes, objective, or stochastic behavior; it repeatedly
calls the existing common-scenario comparison engine and records the resulting
outcomes with enough provenance to replay a single row exactly.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import platform
import statistics
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

from .experiment import (
    ExperimentConfig,
    RoutingPolicy,
    compare_policies,
)
from .network import NetworkSnapshot, parse_snapshot
from .optimizer import active_solver_backend, solver_version
from .simulation import SimulationConfig
from .travel import HaversineTravelTimeProvider, TravelTimeProvider


BENCHMARK_VERSION = "1.0"
SEED_DERIVATION_VERSION = "bakedboston-benchmark-seed-v1"
CI_Z_VALUE = 1.96


PUBLIC_COMPARISON_POLICIES: tuple[RoutingPolicy, ...] = (
    RoutingPolicy.BAKEDBOSTON_MIP,
    RoutingPolicy.NAIR_2018_COVERAGE_FIRST,
    RoutingPolicy.XUE_ZOU_2025_TOTAL_CURB,
    RoutingPolicy.HORNER_2021_SLSF_NOZ,
    RoutingPolicy.REY_2018_EQUITABLE_ALLOCATION,
)


@dataclass(frozen=True)
class BenchmarkCondition:
    """One predefined operating condition, held fixed across its replications."""

    identifier: str
    label: str
    purpose: str
    drivers_per_day: int
    bakery_food_probability: float
    staffed_pantry_open_probability: float


DEFAULT_CONDITIONS: tuple[BenchmarkCondition, ...] = (
    BenchmarkCondition(
        identifier="standard",
        label="Standard operating condition",
        purpose="Baseline contention with the fixture's standard daily surplus and driver supply.",
        drivers_per_day=12,
        bakery_food_probability=0.88,
        staffed_pantry_open_probability=0.90,
    ),
    BenchmarkCondition(
        identifier="scarce_drivers",
        label="Scarce-driver condition",
        purpose="Tests allocation quality when volunteer supply is constrained relative to food-ready pickups.",
        drivers_per_day=6,
        bakery_food_probability=0.88,
        staffed_pantry_open_probability=0.90,
    ),
    BenchmarkCondition(
        identifier="high_surplus",
        label="High-surplus condition",
        purpose="Tests routing when every scheduled bakery occurrence has hypothetical surplus available.",
        drivers_per_day=12,
        bakery_food_probability=1.00,
        staffed_pantry_open_probability=0.90,
    ),
)


# These are the outcomes intended for the report's main table.  Every metric
# returned by PolicyReport.metrics is retained in replication_metrics.csv and
# summary_statistics.csv; this smaller set makes the results section readable.
PRIMARY_METRICS: tuple[str, ...] = (
    "completedDeliveries",
    "foodSavedKg",
    "foodWastedKg",
    "uncollectedBakeryFoodKg",
    "collectedNotDistributedKg",
    "netEnvironmentalBenefitKgCO2e",
    "totalDirectKgCO2e",
    "transportKgCO2e",
    "averageDriveMinutes",
    "averageDistanceMiles",
    "driverAcceptanceRate",
    "expectedDriverAcceptanceRate",
    "likelyRejectionRate",
    "pantryCoveragePercentage",
    "foodSavedDistributionGini",
)


METRIC_DIRECTION: Mapping[str, str] = {
    "completedDeliveries": "higher",
    "foodSavedKg": "higher",
    "foodWastedKg": "lower",
    "uncollectedBakeryFoodKg": "lower",
    "collectedNotDistributedKg": "lower",
    "netEnvironmentalBenefitKgCO2e": "higher",
    "totalDirectKgCO2e": "lower",
    "transportKgCO2e": "lower",
    "averageDriveMinutes": "lower",
    "averageDistanceMiles": "lower",
    "driverAcceptanceRate": "higher",
    "expectedDriverAcceptanceRate": "higher",
    "likelyRejectionRate": "lower",
    "pantryCoveragePercentage": "higher",
    "foodSavedDistributionGini": "lower",
}


@dataclass(frozen=True)
class BenchmarkConfig:
    """Fixed, reportable settings for an entire repeated-simulation benchmark."""

    start_date: date
    replications: int = 500
    master_seed: int = 20261010
    horizon_days: int = 5
    matching_interval_minutes: int = 60
    max_simultaneous_drivers: int = 3
    acceptance_enabled: bool = True
    conditions: tuple[BenchmarkCondition, ...] = DEFAULT_CONDITIONS
    policies: tuple[RoutingPolicy, ...] = PUBLIC_COMPARISON_POLICIES

    def __post_init__(self) -> None:
        if self.replications < 1:
            raise ValueError("replications must be at least 1")
        if self.horizon_days < 1:
            raise ValueError("horizon_days must be at least 1")
        if self.matching_interval_minutes < 1:
            raise ValueError("matching_interval_minutes must be at least 1")
        if self.max_simultaneous_drivers not in (2, 3):
            raise ValueError("max_simultaneous_drivers must be 2 or 3")
        if not self.conditions:
            raise ValueError("at least one benchmark condition is required")
        if not self.policies:
            raise ValueError("at least one policy is required")
        identifiers = [condition.identifier for condition in self.conditions]
        if len(set(identifiers)) != len(identifiers):
            raise ValueError("benchmark condition identifiers must be unique")


@dataclass(frozen=True)
class BenchmarkResult:
    """In-memory rows plus a machine-readable manifest for one completed benchmark."""

    config: BenchmarkConfig
    snapshot_path: Path
    snapshot_sha256: str
    source_hashes: dict[str, str]
    rows: tuple[dict[str, Any], ...]
    elapsed_seconds: float


def derive_replication_seed(
    master_seed: int,
    condition_id: str,
    replication: int,
) -> int:
    """Return a stable positive seed without relying on Python's hash randomization.

    The condition name belongs in the derivation, so the standard condition's
    replication 17 remains unchanged if another condition is later added.
    """

    if replication < 1:
        raise ValueError("replication must be one-based and positive")
    material = (
        f"{SEED_DERIVATION_VERSION}|{int(master_seed)}|{condition_id}|{replication}"
    ).encode("utf-8")
    digest = hashlib.blake2b(material, digest_size=8).digest()
    return int.from_bytes(digest, "big") % 2_147_483_646 + 1


def run_benchmark(
    snapshot: NetworkSnapshot,
    config: BenchmarkConfig,
    *,
    travel: TravelTimeProvider | None = None,
    progress: Callable[[int, int, str, int, int], None] | None = None,
) -> tuple[dict[str, Any], ...]:
    """Run all conditions with common random numbers within every replication.

    ``compare_policies`` constructs exactly one scenario per condition/seed and
    then passes that immutable scenario to every policy.  Therefore the five
    policy rows for a condition/replication share bakery food, usability,
    pantry attendance, driver requests, feasible candidates, and candidate
    acceptance uniforms.  Their subsequent states may diverge only because
    policy choices differ.
    """

    provider = travel or HaversineTravelTimeProvider()
    rows: list[dict[str, Any]] = []
    total = len(config.conditions) * config.replications
    completed = 0
    for condition in config.conditions:
        for replication in range(1, config.replications + 1):
            seed = derive_replication_seed(
                config.master_seed, condition.identifier, replication
            )
            experiment_config = ExperimentConfig(
                SimulationConfig(
                    start_date=config.start_date,
                    days=config.horizon_days,
                    random_seed=seed,
                    drivers_per_day=condition.drivers_per_day,
                    bakery_food_probability=condition.bakery_food_probability,
                    staffed_pantry_open_probability=(
                        condition.staffed_pantry_open_probability
                    ),
                ),
                matching_interval_minutes=config.matching_interval_minutes,
                acceptance_enabled=config.acceptance_enabled,
                max_simultaneous_drivers=config.max_simultaneous_drivers,
            )
            comparison = compare_policies(
                snapshot,
                experiment_config,
                policies=config.policies,
                travel=provider,
            )
            scenario_summary = comparison.scenario.as_dict()
            for report in comparison.policies:
                rows.append({
                    "condition": condition.identifier,
                    "conditionLabel": condition.label,
                    "replication": replication,
                    "seed": seed,
                    "policy": report.policy.value,
                    **report.metrics(),
                    "scenarioFoodAvailablePickups": sum(
                        len(day.pickups) for day in comparison.scenario.days
                    ),
                    "scenarioOpenPantryWindows": sum(
                        len(day.pantries) for day in comparison.scenario.days
                    ),
                    "scenarioSyntheticDriverRequests": sum(
                        len(day.requests) for day in comparison.scenario.days
                    ),
                    "scenarioDigest": _canonical_sha256(scenario_summary),
                })
            completed += 1
            if progress:
                progress(completed, total, condition.identifier, replication, seed)
    return tuple(rows)


def write_benchmark(
    result: BenchmarkResult,
    output_dir: Path,
) -> dict[str, Path]:
    """Write reproducible raw data, summaries, paired tests, and a report insert."""

    output_dir.mkdir(parents=True, exist_ok=True)
    replication_path = output_dir / "replication_metrics.csv"
    summary_path = output_dir / "summary_statistics.csv"
    pairwise_path = output_dir / "paired_bakedboston_deltas.csv"
    manifest_path = output_dir / "reproduction_manifest.json"
    results_path = output_dir / "results_section.md"
    methodology_path = output_dir / "README.md"

    metric_names = _metric_columns(result.rows)
    _write_replication_metrics(result.rows, metric_names, replication_path)
    summary_rows = summarize_rows(result.rows, metric_names)
    _write_csv(summary_rows, summary_path)
    pairwise_rows = paired_bakedboston_deltas(result.rows, PRIMARY_METRICS)
    _write_csv(pairwise_rows, pairwise_path)
    manifest = _manifest(result, metric_names, summary_rows, pairwise_rows)
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    results_path.write_text(_render_results_section(result, summary_rows, pairwise_rows))
    methodology_path.write_text(
        _render_output_readme(result, metric_names, replay_output_dir=Path("results/benchmark-replay"))
    )
    return {
        "manifest": manifest_path,
        "replications": replication_path,
        "summary": summary_path,
        "pairwise": pairwise_path,
        "resultsSection": results_path,
        "readme": methodology_path,
    }


def summarize_rows(
    rows: Sequence[Mapping[str, Any]], metric_names: Sequence[str] | None = None
) -> list[dict[str, Any]]:
    """Return mean, sample SD, and normal-approximation 95% CIs by policy/condition."""

    names = tuple(metric_names or _metric_columns(rows))
    grouped: dict[tuple[str, str], list[Mapping[str, Any]]] = {}
    for row in rows:
        grouped.setdefault((str(row["condition"]), str(row["policy"])), []).append(row)
    summary: list[dict[str, Any]] = []
    for (condition, policy), group in sorted(grouped.items()):
        for metric in names:
            values = _numeric_values(group, metric)
            if not values:
                continue
            stats = _descriptive_statistics(values)
            summary.append({
                "condition": condition,
                "policy": policy,
                "metric": metric,
                **stats,
            })
    return summary


def paired_bakedboston_deltas(
    rows: Sequence[Mapping[str, Any]], metrics: Iterable[str]
) -> list[dict[str, Any]]:
    """Compare BakedBoston against each policy using matched condition/seed pairs."""

    index = {
        (str(row["condition"]), int(row["replication"]), str(row["policy"])): row
        for row in rows
    }
    conditions = sorted({str(row["condition"]) for row in rows})
    comparators = sorted({
        str(row["policy"])
        for row in rows
        if str(row["policy"]) != RoutingPolicy.BAKEDBOSTON_MIP.value
    })
    output: list[dict[str, Any]] = []
    for condition in conditions:
        replications = sorted({
            int(row["replication"])
            for row in rows
            if str(row["condition"]) == condition
        })
        for comparator in comparators:
            for metric in metrics:
                deltas: list[float] = []
                for replication in replications:
                    baked = index.get(
                        (condition, replication, RoutingPolicy.BAKEDBOSTON_MIP.value)
                    )
                    other = index.get((condition, replication, comparator))
                    if baked is None or other is None:
                        continue
                    left, right = baked.get(metric), other.get(metric)
                    if _is_finite_number(left) and _is_finite_number(right):
                        deltas.append(float(left) - float(right))
                if not deltas:
                    continue
                direction = METRIC_DIRECTION.get(metric, "higher")
                wins = sum(
                    delta > 0 if direction == "higher" else delta < 0
                    for delta in deltas
                )
                ties = sum(math.isclose(delta, 0.0, abs_tol=1e-12) for delta in deltas)
                output.append({
                    "condition": condition,
                    "comparator": comparator,
                    "metric": metric,
                    "bakedBostonMinusComparator": "positive" if direction == "higher" else "negative",
                    "betterDirection": direction,
                    **_descriptive_statistics(deltas),
                    "bakedBostonBetterReplicationRate": round(wins / len(deltas), 6),
                    "tieReplicationRate": round(ties / len(deltas), 6),
                })
    return output


def _descriptive_statistics(values: Sequence[float]) -> dict[str, Any]:
    count = len(values)
    mean = statistics.fmean(values)
    standard_deviation = statistics.stdev(values) if count > 1 else 0.0
    half_width = CI_Z_VALUE * standard_deviation / math.sqrt(count) if count > 1 else 0.0
    return {
        "n": count,
        "mean": round(mean, 8),
        "standardDeviation": round(standard_deviation, 8),
        "ci95Low": round(mean - half_width, 8),
        "ci95High": round(mean + half_width, 8),
        "minimum": round(min(values), 8),
        "median": round(statistics.median(values), 8),
        "maximum": round(max(values), 8),
    }


def _metric_columns(rows: Sequence[Mapping[str, Any]]) -> tuple[str, ...]:
    metadata = {
        "condition",
        "conditionLabel",
        "replication",
        "seed",
        "policy",
        "scenarioDigest",
    }
    columns = {
        key
        for row in rows
        for key, value in row.items()
        if key not in metadata and _is_finite_number(value)
    }
    return tuple(sorted(columns))


def _numeric_values(rows: Sequence[Mapping[str, Any]], metric: str) -> list[float]:
    return [float(row[metric]) for row in rows if _is_finite_number(row.get(metric))]


def _is_finite_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _write_replication_metrics(
    rows: Sequence[Mapping[str, Any]], metric_names: Sequence[str], path: Path
) -> None:
    fieldnames = (
        "condition",
        "conditionLabel",
        "replication",
        "seed",
        "policy",
        "scenarioDigest",
        *metric_names,
    )
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field) for field in fieldnames})


def _write_csv(rows: Sequence[Mapping[str, Any]], path: Path) -> None:
    if not rows:
        path.write_text("")
        return
    fieldnames = tuple(rows[0].keys())
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def _manifest(
    result: BenchmarkResult,
    metric_names: Sequence[str],
    summary_rows: Sequence[Mapping[str, Any]],
    pairwise_rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    config = result.config
    seeds = {
        condition.identifier: [
            derive_replication_seed(config.master_seed, condition.identifier, replication)
            for replication in range(1, config.replications + 1)
        ]
        for condition in config.conditions
    }
    return {
        "benchmarkVersion": BENCHMARK_VERSION,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "purpose": (
            "Academic repeated-simulation comparison. All policy rows within a condition and "
            "replication use the same immutable seeded scenario."
        ),
        "configuration": {
            "startDate": config.start_date.isoformat(),
            "replicationsPerCondition": config.replications,
            "masterSeed": config.master_seed,
            "horizonDays": config.horizon_days,
            "matchingIntervalMinutes": config.matching_interval_minutes,
            "maxSimultaneousDrivers": config.max_simultaneous_drivers,
            "acceptanceEnabled": config.acceptance_enabled,
            "conditions": [asdict(condition) for condition in config.conditions],
            "policies": [policy.value for policy in config.policies],
        },
        "seedDerivation": {
            "version": SEED_DERIVATION_VERSION,
            "algorithm": "BLAKE2b-64",
            "input": "{version}|{masterSeed}|{conditionId}|{oneBasedReplication}",
            "output": "unsigned digest modulo 2147483646, plus 1",
            "seedsByCondition": seeds,
        },
        "commonRandomNumbers": {
            "enabled": True,
            "statement": (
                "For each condition/replication, one scenario is generated once and passed "
                "unchanged to every policy. Candidate-level acceptance uniforms are also "
                "derived from the same scenario seed and candidate identifiers."
            ),
        },
        "inputs": {
            "snapshotPath": str(result.snapshot_path),
            "snapshotSha256": result.snapshot_sha256,
            "sourceFileSha256": result.source_hashes,
        },
        "runtime": {
            "pythonVersion": sys.version,
            "platform": platform.platform(),
            "solverBackend": active_solver_backend(),
            "solverVersion": solver_version(),
            "elapsedSeconds": round(result.elapsed_seconds, 3),
        },
        "outputs": {
            "replicationRows": len(result.rows),
            "summaryRows": len(summary_rows),
            "pairedDeltaRows": len(pairwise_rows),
            "metricColumns": list(metric_names),
            "primaryMetrics": list(PRIMARY_METRICS),
            "ciMethod": (
                "Two-sided normal-approximation 95% confidence interval: "
                "mean ± 1.96 × sample SD / sqrt(n)."
            ),
        },
        "sourceControl": _git_metadata(result.snapshot_path.parent),
    }


def _render_results_section(
    result: BenchmarkResult,
    summary_rows: Sequence[Mapping[str, Any]],
    pairwise_rows: Sequence[Mapping[str, Any]],
) -> str:
    """Create a conservative report insert from actual output, never claimed results."""

    config = result.config
    lines = [
        "# Repeated-Simulation Results",
        "",
        "## Experimental design",
        "",
        (
            f"The evaluation ran {config.replications} independent seeded five-day replications "
            f"for each of {len(config.conditions)} operating conditions. Each replication compared "
            f"BakedBoston with {len(config.policies) - 1} paper-based comparators on the same "
            "generated bakery, pantry, driver, and candidate-acceptance events."
        ),
        "",
        "The reported intervals are two-sided normal-approximation 95% confidence intervals for the mean across replications. They quantify simulation variability under these inputs; they are not evidence about real volunteers or organizations.",
        "",
        "## Primary outcomes",
        "",
    ]
    summary_index = {
        (str(row["condition"]), str(row["policy"]), str(row["metric"])): row
        for row in summary_rows
    }
    for condition in config.conditions:
        lines.extend([f"### {condition.label}", ""])
        lines.append("| Policy | Deliveries | Food distributed (kg) | Food wasted (kg) | Net direct CO₂e benefit (kg) | Avg. drive minutes | Expected acceptance | Pantry coverage |")
        lines.append("| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |")
        for policy in config.policies:
            values = [
                _summary_cell(summary_index.get((condition.identifier, policy.value, metric)))
                for metric in (
                    "completedDeliveries",
                    "foodSavedKg",
                    "foodWastedKg",
                    "netEnvironmentalBenefitKgCO2e",
                    "averageDriveMinutes",
                    "expectedDriverAcceptanceRate",
                    "pantryCoveragePercentage",
                )
            ]
            lines.append(f"| {policy.value} | " + " | ".join(values) + " |")
        lines.append("")

    lines.extend([
        "## Paired BakedBoston comparisons",
        "",
        "Each comparison below uses matched condition/replication pairs. For a higher-is-better metric, a positive difference means BakedBoston is higher. For a lower-is-better metric, a negative difference means BakedBoston is lower (better).",
        "",
        "| Condition | Comparator | Metric | Mean BakedBoston − comparator | 95% CI | BakedBoston better in replications |",
        "| --- | --- | --- | ---: | ---: | ---: |",
    ])
    display_metrics = {
        "foodSavedKg",
        "foodWastedKg",
        "netEnvironmentalBenefitKgCO2e",
        "averageDriveMinutes",
        "expectedDriverAcceptanceRate",
        "pantryCoveragePercentage",
    }
    for row in pairwise_rows:
        if row["metric"] not in display_metrics:
            continue
        lines.append(
            f"| {row['condition']} | {row['comparator']} | {row['metric']} | "
            f"{_number(row['mean'])} | [{_number(row['ci95Low'])}, {_number(row['ci95High'])}] | "
            f"{100 * float(row['bakedBostonBetterReplicationRate']):.1f}% |"
        )
    lines.extend([
        "",
        "## Interpretation boundary",
        "",
        "These results compare the declared policies on a fictional academic fixture. They do not establish causal effectiveness in a live food-rescue network, calibrate the volunteer sigmoid to observed behavior, or estimate the carbon footprint of a real organization. The raw replication rows, seed manifest, configuration, and source hashes are retained so every summary statistic can be reproduced or audited.",
        "",
    ])
    return "\n".join(lines)


def _summary_cell(row: Mapping[str, Any] | None) -> str:
    if row is None:
        return "—"
    return f"{_number(row['mean'])} [{_number(row['ci95Low'])}, {_number(row['ci95High'])}]"


def _number(value: Any) -> str:
    return f"{float(value):.3f}".rstrip("0").rstrip(".")


def _render_output_readme(
    result: BenchmarkResult,
    metric_names: Sequence[str],
    *,
    replay_output_dir: Path,
) -> str:
    config = result.config
    return f"""# BakedBoston repeated-simulation benchmark output

This directory is the generated evidence package for the academic benchmark.

## Exact replay command

Run from the optimizer repository root:

```bash
python3 -m bakedboston_optimizer.benchmark \\
  data/academic_comparison_snapshot.json \\
  --start-date {config.start_date.isoformat()} \\
  --replications {config.replications} \\
  --master-seed {config.master_seed} \\
  --horizon-days {config.horizon_days} \\
  --matching-interval-minutes {config.matching_interval_minutes} \\
  --max-simultaneous-drivers {config.max_simultaneous_drivers} \\
  --output-dir {replay_output_dir}
```

The command enables the existing synthetic acceptance replay. Add
`--disable-acceptance` only for the separate deterministic routing-capacity
sensitivity analysis; it is not the primary result package.

## Files

- `reproduction_manifest.json`: complete configuration, all derived seeds,
  input/source hashes, runtime information, and confidence-interval method.
- `replication_metrics.csv`: one row per condition × replication × policy.
  This is the source data for every aggregate result.
- `summary_statistics.csv`: one row per condition × policy × metric with mean,
  sample standard deviation, median, range, and a 95% confidence interval.
- `paired_bakedboston_deltas.csv`: matched-replication BakedBoston minus
  comparator differences for primary outcomes.
- `results_section.md`: report-ready results language and tables generated from
  the raw rows. It contains no manually typed performance claims.

## Reproducibility rules

For each condition and one-based replication number, the seed is calculated as
`BLAKE2b-64("{SEED_DERIVATION_VERSION}|masterSeed|conditionId|replication")`,
reduced to the integer range 1 through 2,147,483,646. All five policies share
the resulting immutable scenario and candidate-level acceptance uniforms.

Changing the code, fixture, condition definitions, horizon, date, seed,
acceptance setting, or solver can change results. The manifest records hashes
and environment metadata so a reviewer can identify such a change.

## Conditions

| Condition | Drivers/day | Bakery food probability | Staffed-pantry open probability | Purpose |
| --- | ---: | ---: | ---: | --- |
""" + "\n".join(
        f"| {condition.identifier} | {condition.drivers_per_day} | "
        f"{condition.bakery_food_probability:.2f} | "
        f"{condition.staffed_pantry_open_probability:.2f} | {condition.purpose} |"
        for condition in config.conditions
    ) + f"""

## Metric columns

{", ".join(metric_names)}

All institutions, food events, drivers, acceptance decisions, and delivery
outcomes are synthetic academic simulation data. These outputs must not be
described as live operating performance.
"""


def _canonical_sha256(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _source_hashes(repository_root: Path) -> dict[str, str]:
    """Hash executable project inputs, including local uncommitted source edits."""

    files = [
        repository_root / "pyproject.toml",
        repository_root / "requirements.txt",
        *(repository_root / "bakedboston_optimizer").glob("*.py"),
    ]
    result: dict[str, str] = {}
    for path in sorted(files):
        if path.is_file():
            result[str(path.relative_to(repository_root))] = hashlib.sha256(
                path.read_bytes()
            ).hexdigest()
    return result


def _git_metadata(repository_root: Path) -> dict[str, Any]:
    def command(*args: str) -> str | None:
        try:
            return subprocess.run(
                ["git", *args],
                cwd=repository_root,
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
        except (OSError, subprocess.CalledProcessError):
            return None

    return {
        "head": command("rev-parse", "HEAD"),
        "branch": command("branch", "--show-current"),
        "statusPorcelain": (command("status", "--porcelain") or "").splitlines(),
    }


def _load_snapshot(path: Path) -> NetworkSnapshot:
    payload: Any = json.loads(path.read_text())
    if not isinstance(payload, dict):
        raise TypeError("The scenario file must contain a JSON object.")
    return parse_snapshot(payload)


def _progress_line(
    started_at: float,
    completed: int,
    total: int,
    condition: str,
    replication: int,
    seed: int,
) -> None:
    elapsed = time.monotonic() - started_at
    rate = completed / elapsed if elapsed else 0.0
    remaining = (total - completed) / rate if rate else 0.0
    print(
        f"[{completed}/{total}] {condition} replication {replication} (seed {seed}) "
        f"— elapsed {elapsed / 60:.1f}m, estimated remaining {remaining / 60:.1f}m",
        flush=True,
    )


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Run the BakedBoston repeated seeded academic benchmark with common "
            "random numbers and reproducible confidence intervals."
        )
    )
    parser.add_argument("scenario", type=Path, help="Fictional academic snapshot JSON")
    parser.add_argument("--start-date", required=True, type=date.fromisoformat)
    parser.add_argument("--replications", type=int, default=500)
    parser.add_argument("--master-seed", type=int, default=20261010)
    parser.add_argument("--horizon-days", type=int, default=5)
    parser.add_argument("--matching-interval-minutes", type=int, default=60)
    parser.add_argument("--max-simultaneous-drivers", type=int, choices=(2, 3), default=3)
    parser.add_argument(
        "--disable-acceptance",
        action="store_true",
        help="Run only the separate deterministic routing-capacity sensitivity analysis.",
    )
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument(
        "--progress-every",
        type=int,
        default=10,
        help="Print progress after this many completed condition/replication scenarios.",
    )
    args = parser.parse_args(argv)
    if args.progress_every < 1:
        parser.error("--progress-every must be at least 1")

    config = BenchmarkConfig(
        start_date=args.start_date,
        replications=args.replications,
        master_seed=args.master_seed,
        horizon_days=args.horizon_days,
        matching_interval_minutes=args.matching_interval_minutes,
        max_simultaneous_drivers=args.max_simultaneous_drivers,
        acceptance_enabled=not args.disable_acceptance,
    )
    scenario_path = args.scenario.resolve()
    repository_root = Path(__file__).resolve().parents[1]
    started_at = time.monotonic()
    completed_since_last = 0

    def progress(completed: int, total: int, condition: str, replication: int, seed: int) -> None:
        nonlocal completed_since_last
        completed_since_last += 1
        if completed == total or completed_since_last >= args.progress_every:
            _progress_line(started_at, completed, total, condition, replication, seed)
            completed_since_last = 0

    rows = run_benchmark(
        _load_snapshot(scenario_path),
        config,
        progress=progress,
    )
    result = BenchmarkResult(
        config=config,
        snapshot_path=scenario_path,
        snapshot_sha256=hashlib.sha256(scenario_path.read_bytes()).hexdigest(),
        source_hashes=_source_hashes(repository_root),
        rows=rows,
        elapsed_seconds=time.monotonic() - started_at,
    )
    paths = write_benchmark(result, args.output_dir.resolve())
    print("\nBenchmark complete.")
    for label, path in paths.items():
        print(f"{label}: {path}")


if __name__ == "__main__":
    main()
