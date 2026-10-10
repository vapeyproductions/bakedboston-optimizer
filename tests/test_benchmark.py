from __future__ import annotations

import json
import unittest
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory

from bakedboston_optimizer.benchmark import (
    BenchmarkCondition,
    BenchmarkConfig,
    BenchmarkResult,
    RoutingPolicy,
    derive_replication_seed,
    paired_bakedboston_deltas,
    summarize_rows,
    write_benchmark,
)
from bakedboston_optimizer.verify_benchmark import verify_benchmark_package


class BenchmarkTests(unittest.TestCase):
    def test_seed_derivation_is_stable_and_condition_specific(self) -> None:
        self.assertEqual(
            derive_replication_seed(20261010, "standard", 1),
            592352674,
        )
        self.assertEqual(
            derive_replication_seed(20261010, "scarce_drivers", 1),
            530526956,
        )
        self.assertNotEqual(
            derive_replication_seed(20261010, "standard", 1),
            derive_replication_seed(20261010, "standard", 2),
        )
        with self.assertRaises(ValueError):
            derive_replication_seed(1, "standard", 0)

    def test_summary_and_paired_deltas_preserve_metric_direction(self) -> None:
        rows = (
            {
                "condition": "standard",
                "conditionLabel": "Standard",
                "replication": 1,
                "seed": 11,
                "policy": "bakedboston_mip",
                "scenarioDigest": "a",
                "foodSavedKg": 12.0,
                "foodWastedKg": 3.0,
            },
            {
                "condition": "standard",
                "conditionLabel": "Standard",
                "replication": 1,
                "seed": 11,
                "policy": "nair_2018_coverage_first",
                "scenarioDigest": "a",
                "foodSavedKg": 10.0,
                "foodWastedKg": 5.0,
            },
            {
                "condition": "standard",
                "conditionLabel": "Standard",
                "replication": 2,
                "seed": 12,
                "policy": "bakedboston_mip",
                "scenarioDigest": "b",
                "foodSavedKg": 16.0,
                "foodWastedKg": 4.0,
            },
            {
                "condition": "standard",
                "conditionLabel": "Standard",
                "replication": 2,
                "seed": 12,
                "policy": "nair_2018_coverage_first",
                "scenarioDigest": "b",
                "foodSavedKg": 12.0,
                "foodWastedKg": 7.0,
            },
        )

        summary = summarize_rows(rows, ("foodSavedKg", "foodWastedKg"))
        baked_food = next(
            row for row in summary
            if row["policy"] == "bakedboston_mip" and row["metric"] == "foodSavedKg"
        )
        self.assertEqual(baked_food["n"], 2)
        self.assertEqual(baked_food["mean"], 14.0)

        deltas = paired_bakedboston_deltas(rows, ("foodSavedKg", "foodWastedKg"))
        saved = next(row for row in deltas if row["metric"] == "foodSavedKg")
        waste = next(row for row in deltas if row["metric"] == "foodWastedKg")
        self.assertEqual(saved["mean"], 3.0)
        self.assertEqual(saved["betterDirection"], "higher")
        self.assertEqual(saved["bakedBostonBetterReplicationRate"], 1.0)
        self.assertEqual(waste["mean"], -2.5)
        self.assertEqual(waste["betterDirection"], "lower")
        self.assertEqual(waste["bakedBostonBetterReplicationRate"], 1.0)

    def test_writer_emits_an_auditable_output_package(self) -> None:
        config = BenchmarkConfig(
            start_date=date(2026, 8, 24),
            replications=1,
        )
        rows = (
            {
                "condition": "standard",
                "conditionLabel": "Standard operating condition",
                "replication": 1,
                "seed": 592352674,
                "policy": "bakedboston_mip",
                "scenarioDigest": "abc",
                "completedDeliveries": 2,
                "foodSavedKg": 20.0,
                "foodWastedKg": 5.0,
                "netEnvironmentalBenefitKgCO2e": 7.0,
                "averageDriveMinutes": 11.0,
                "expectedDriverAcceptanceRate": 0.7,
                "pantryCoveragePercentage": 0.5,
            },
        )
        result = BenchmarkResult(
            config=config,
            snapshot_path=Path("/tmp/fixture.json"),
            snapshot_sha256="fixture-hash",
            source_hashes={"benchmark.py": "source-hash"},
            rows=rows,
            elapsed_seconds=2.0,
        )
        with TemporaryDirectory() as temporary:
            paths = write_benchmark(result, Path(temporary))
            self.assertTrue(all(path.exists() for path in paths.values()))
            manifest = json.loads(paths["manifest"].read_text())
            self.assertEqual(manifest["outputs"]["replicationRows"], 1)
            self.assertEqual(
                manifest["seedDerivation"]["seedsByCondition"]["standard"][0],
                592352674,
            )
            self.assertIn("Exact replay command", paths["readme"].read_text())

    def test_verifier_accepts_a_complete_minimal_package(self) -> None:
        condition = BenchmarkCondition(
            identifier="standard",
            label="Standard",
            purpose="Test condition",
            drivers_per_day=1,
            bakery_food_probability=1.0,
            staffed_pantry_open_probability=1.0,
        )
        config = BenchmarkConfig(
            start_date=date(2026, 8, 24),
            replications=1,
            conditions=(condition,),
            policies=(RoutingPolicy.BAKEDBOSTON_MIP,),
        )
        result = BenchmarkResult(
            config=config,
            snapshot_path=Path("/tmp/missing-fixture.json"),
            snapshot_sha256="fixture-hash",
            source_hashes={},
            rows=(
                {
                    "condition": "standard",
                    "conditionLabel": "Standard",
                    "replication": 1,
                    "seed": derive_replication_seed(20261010, "standard", 1),
                    "policy": "bakedboston_mip",
                    "scenarioDigest": "abc",
                    "foodSavedKg": 10.0,
                },
            ),
            elapsed_seconds=0.0,
        )
        with TemporaryDirectory() as temporary:
            paths = write_benchmark(result, Path(temporary))
            verification = verify_benchmark_package(paths["manifest"].parent)
            self.assertTrue(verification.valid, verification.errors)
            self.assertEqual(verification.checked_rows, 1)
            self.assertTrue(verification.warnings)


if __name__ == "__main__":
    unittest.main()
