"""Validate the structure and seed provenance of a benchmark output package.

This utility intentionally does not rerun the optimizer. It checks that a
published result package has the expected files, row count, policy coverage,
and deterministic seed schedule recorded in its manifest. A full reproduction
is performed by rerunning ``bakedboston_optimizer.benchmark`` with the command
in the package README.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence

from .benchmark import derive_replication_seed


REQUIRED_FILES = (
    "README.md",
    "reproduction_manifest.json",
    "replication_metrics.csv",
    "summary_statistics.csv",
    "paired_bakedboston_deltas.csv",
    "results_section.md",
)


@dataclass(frozen=True)
class VerificationResult:
    """Non-destructive checks performed against one generated package."""

    output_dir: Path
    checked_rows: int
    errors: tuple[str, ...]
    warnings: tuple[str, ...]

    @property
    def valid(self) -> bool:
        return not self.errors


def verify_benchmark_package(
    output_dir: Path,
    *,
    repository_root: Path | None = None,
) -> VerificationResult:
    """Check required artifacts, deterministic seeds, and policy row coverage."""

    output_dir = output_dir.resolve()
    errors: list[str] = []
    warnings: list[str] = []
    missing = [name for name in REQUIRED_FILES if not (output_dir / name).is_file()]
    if missing:
        return VerificationResult(
            output_dir=output_dir,
            checked_rows=0,
            errors=(f"Missing required output file(s): {', '.join(missing)}.",),
            warnings=(),
        )

    try:
        manifest = json.loads((output_dir / "reproduction_manifest.json").read_text())
    except json.JSONDecodeError as error:
        return VerificationResult(
            output_dir=output_dir,
            checked_rows=0,
            errors=(f"Could not parse reproduction_manifest.json: {error}.",),
            warnings=(),
        )

    configuration = _mapping(manifest.get("configuration"), "configuration", errors)
    seed_derivation = _mapping(manifest.get("seedDerivation"), "seedDerivation", errors)
    outputs = _mapping(manifest.get("outputs"), "outputs", errors)
    inputs = _mapping(manifest.get("inputs"), "inputs", errors)

    master_seed = configuration.get("masterSeed")
    replications = configuration.get("replicationsPerCondition")
    policies = configuration.get("policies")
    conditions = configuration.get("conditions")
    if not isinstance(master_seed, int):
        errors.append("Manifest configuration.masterSeed must be an integer.")
    if not isinstance(replications, int) or replications < 1:
        errors.append("Manifest configuration.replicationsPerCondition must be a positive integer.")
    if not isinstance(policies, list) or not all(isinstance(policy, str) for policy in policies):
        errors.append("Manifest configuration.policies must be a list of policy identifiers.")
    if not isinstance(conditions, list) or not all(isinstance(condition, dict) for condition in conditions):
        errors.append("Manifest configuration.conditions must be a list of condition objects.")

    rows = list(_read_csv(output_dir / "replication_metrics.csv", errors))
    expected_rows = outputs.get("replicationRows")
    if isinstance(expected_rows, int) and len(rows) != expected_rows:
        errors.append(
            f"replication_metrics.csv has {len(rows)} rows; manifest records {expected_rows}."
        )

    if (
        isinstance(master_seed, int)
        and isinstance(replications, int)
        and isinstance(policies, list)
        and isinstance(conditions, list)
    ):
        _verify_seed_schedule(
            rows,
            master_seed=master_seed,
            replications=replications,
            policies=policies,
            conditions=conditions,
            errors=errors,
        )
        expected_by_configuration = len(conditions) * replications * len(policies)
        if len(rows) != expected_by_configuration:
            errors.append(
                "Replication row count does not equal conditions × replications × policies "
                f"({len(conditions)} × {replications} × {len(policies)} = {expected_by_configuration})."
            )

    _verify_snapshot_hash(inputs, errors, warnings)
    _verify_source_hashes(inputs, repository_root, warnings)
    return VerificationResult(
        output_dir=output_dir,
        checked_rows=len(rows),
        errors=tuple(errors),
        warnings=tuple(warnings),
    )


def _mapping(value: Any, name: str, errors: list[str]) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    errors.append(f"Manifest {name} must be an object.")
    return {}


def _read_csv(path: Path, errors: list[str]) -> Iterable[dict[str, str]]:
    try:
        with path.open(newline="") as handle:
            yield from csv.DictReader(handle)
    except OSError as error:
        errors.append(f"Could not read {path.name}: {error}.")


def _verify_seed_schedule(
    rows: Sequence[dict[str, str]],
    *,
    master_seed: int,
    replications: int,
    policies: Sequence[str],
    conditions: Sequence[dict[str, Any]],
    errors: list[str],
) -> None:
    expected_keys: set[tuple[str, int, str]] = set()
    condition_ids: set[str] = set()
    for condition in conditions:
        identifier = condition.get("identifier")
        if not isinstance(identifier, str):
            errors.append("Each manifest condition must have a string identifier.")
            continue
        condition_ids.add(identifier)
        for replication in range(1, replications + 1):
            for policy in policies:
                expected_keys.add((identifier, replication, policy))

    actual_keys: set[tuple[str, int, str]] = set()
    for row_number, row in enumerate(rows, start=2):
        condition = row.get("condition", "")
        policy = row.get("policy", "")
        try:
            replication = int(row.get("replication", ""))
            seed = int(row.get("seed", ""))
        except ValueError:
            errors.append(f"replication_metrics.csv row {row_number} has a non-integer replication or seed.")
            continue
        if condition not in condition_ids:
            errors.append(f"replication_metrics.csv row {row_number} has unknown condition '{condition}'.")
            continue
        if policy not in policies:
            errors.append(f"replication_metrics.csv row {row_number} has unknown policy '{policy}'.")
            continue
        if not 1 <= replication <= replications:
            errors.append(f"replication_metrics.csv row {row_number} has out-of-range replication {replication}.")
            continue
        expected_seed = derive_replication_seed(master_seed, condition, replication)
        if seed != expected_seed:
            errors.append(
                f"replication_metrics.csv row {row_number} has seed {seed}; expected {expected_seed}."
            )
        key = (condition, replication, policy)
        if key in actual_keys:
            errors.append(f"replication_metrics.csv has duplicate row for {key}.")
        actual_keys.add(key)

    missing_keys = expected_keys - actual_keys
    unexpected_keys = actual_keys - expected_keys
    if missing_keys:
        errors.append(f"replication_metrics.csv is missing {len(missing_keys)} expected policy rows.")
    if unexpected_keys:
        errors.append(f"replication_metrics.csv has {len(unexpected_keys)} unexpected policy rows.")


def _verify_snapshot_hash(
    inputs: dict[str, Any],
    errors: list[str],
    warnings: list[str],
) -> None:
    snapshot_path = inputs.get("snapshotPath")
    expected_hash = inputs.get("snapshotSha256")
    if not isinstance(snapshot_path, str) or not isinstance(expected_hash, str):
        warnings.append("Snapshot path/hash is not present in the manifest; source-file verification was skipped.")
        return
    path = Path(snapshot_path)
    if not path.is_file():
        warnings.append(
            f"Snapshot source is not available at {path}; could not check its SHA-256 hash."
        )
        return
    actual_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    if actual_hash != expected_hash:
        errors.append(
            f"Snapshot SHA-256 mismatch for {path}. The input differs from the package manifest."
        )


def _verify_source_hashes(
    inputs: dict[str, Any],
    repository_root: Path | None,
    warnings: list[str],
) -> None:
    if repository_root is None:
        return
    expected_hashes = inputs.get("sourceFileSha256")
    if not isinstance(expected_hashes, dict):
        warnings.append("No source-file hashes are available in the manifest.")
        return
    changed = []
    for relative_path, expected_hash in expected_hashes.items():
        path = repository_root / relative_path
        if not path.is_file():
            changed.append(f"missing {relative_path}")
            continue
        actual_hash = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual_hash != expected_hash:
            changed.append(relative_path)
    if changed:
        warnings.append(
            "Current source differs from the package manifest: " + ", ".join(changed) + "."
        )


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Validate a BakedBoston repeated-simulation output package without rerunning it."
    )
    parser.add_argument("output_dir", type=Path, help="Benchmark output directory to check")
    parser.add_argument(
        "--repository-root",
        type=Path,
        default=None,
        help="Optional optimizer repository root for source-hash comparison",
    )
    args = parser.parse_args(argv)
    result = verify_benchmark_package(
        args.output_dir,
        repository_root=args.repository_root.resolve() if args.repository_root else None,
    )
    if result.valid:
        print(f"Verified {result.checked_rows} replication rows in {result.output_dir}.")
    else:
        print(f"Verification failed for {result.output_dir}:")
        for error in result.errors:
            print(f"- {error}")
    for warning in result.warnings:
        print(f"Warning: {warning}")
    if not result.valid:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
