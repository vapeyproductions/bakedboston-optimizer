from __future__ import annotations

import csv
import json
import unittest
from collections import Counter, defaultdict, deque
from datetime import date, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from zoneinfo import ZoneInfo

from bakedboston_optimizer.experiment import (
    DEFAULT_POLICIES,
    ExperimentConfig,
    RoutingPolicy,
    _finalize_pantry_opportunities,
    build_scenario,
    compare_horizons,
    compare_policies,
    gini,
    write_summary_csv,
)
from bakedboston_optimizer.models import Location, Pantry
from bakedboston_optimizer.network import parse_snapshot
from bakedboston_optimizer.simulation import SimulationConfig, pantry_priority
from bakedboston_optimizer.travel import HaversineTravelTimeProvider
from bakedboston_optimizer.web_export import build_web_payload


def _comparison_snapshot():
    days = "Mon,Tue,Wed,Thu,Fri"
    ready = '{"Mon":"17:00","Tue":"17:00","Wed":"17:00","Thu":"17:00","Fri":"17:00"}'
    deadlines = '{"Mon":"18:00","Tue":"18:00","Wed":"18:00","Thu":"18:00","Fri":"18:00"}'
    open_items = [
        {"recurrence": "weekly", "day": day, "time": "16:30"}
        for day in ("Mon", "Tue", "Wed", "Thu", "Fri")
    ]
    close_items = [
        {"recurrence": "weekly", "day": day, "time": "19:30"}
        for day in ("Mon", "Tue", "Wed", "Thu", "Fri")
    ]
    arrival_items = [
        {"recurrence": "weekly", "day": day, "time": "19:15"}
        for day in ("Mon", "Tue", "Wed", "Thu", "Fri")
    ]
    modes = [{"mode": "unattended"} for _ in open_items]
    return parse_snapshot({
        "schemaVersion": 2,
        "generatedAt": "2026-08-20T12:00:00Z",
        "bakeries": [
            {
                "id": 1,
                "name": "North Bakery",
                "address": "1 North Street, Boston, MA",
                "formattedAddress": "1 North Street, Boston, MA",
                "googlePlaceId": "bakery-north",
                "addressValidationStatus": "validated",
                "latitude": 42.375,
                "longitude": -71.065,
                "recurringDays": days,
                "readyTime": ready,
                "pickupDeadline": deadlines,
            },
            {
                "id": 2,
                "name": "South Bakery",
                "address": "2 South Street, Boston, MA",
                "formattedAddress": "2 South Street, Boston, MA",
                "googlePlaceId": "bakery-south",
                "addressValidationStatus": "validated",
                "latitude": 42.335,
                "longitude": -71.075,
                "recurringDays": days,
                "readyTime": ready,
                "pickupDeadline": deadlines,
            },
        ],
        "pantries": [
            {
                "id": 11,
                "name": "East Pantry",
                "address": "11 East Street, Boston, MA",
                "formattedAddress": "11 East Street, Boston, MA",
                "googlePlaceId": "pantry-east",
                "addressValidationStatus": "validated",
                "latitude": 42.355,
                "longitude": -71.025,
                "recurringDays": days,
                "openTime": open_items,
                "closeTime": close_items,
                "latestPermittedArrival": arrival_items,
                "serviceModes": modes,
            },
            {
                "id": 12,
                "name": "West Pantry",
                "address": "12 West Street, Boston, MA",
                "formattedAddress": "12 West Street, Boston, MA",
                "googlePlaceId": "pantry-west",
                "addressValidationStatus": "validated",
                "latitude": 42.355,
                "longitude": -71.115,
                "recurringDays": days,
                "openTime": open_items,
                "closeTime": close_items,
                "latestPermittedArrival": arrival_items,
                "serviceModes": modes,
            },
        ],
        "availabilityWindows": [],
        "availabilityPauses": [],
        "scheduleExceptions": [],
        "routes": [],
        "pickupOccurrences": [],
        "rideRequests": [],
        "routeOffers": [],
        "drivers": [],
        "pantryWindowConfirmations": [],
    })


class ExperimentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.snapshot = _comparison_snapshot()
        self.config = ExperimentConfig(
            SimulationConfig(
                start_date=date(2026, 8, 24),
                days=3,
                random_seed=77,
                drivers_per_day=4,
                bakery_food_probability=1,
                staffed_pantry_open_probability=1,
            ),
            acceptance_enabled=False,
        )

    def test_scenario_generation_is_deterministic(self) -> None:
        first = build_scenario(self.snapshot, self.config).as_dict()
        second = build_scenario(self.snapshot, self.config).as_dict()

        self.assertEqual(first, second)
        self.assertEqual(len(first["days"]), 3)
        self.assertEqual(first["horizonDays"], 3)
        self.assertFalse(first["acceptanceEnabled"])
        self.assertEqual(first["matchingIntervalMinutes"], 15)
        self.assertTrue(all(day["foodAvailablePickups"] == 2 for day in first["days"]))

    def test_every_policy_uses_same_scenario_and_reports_metrics(self) -> None:
        result = compare_policies(
            self.snapshot,
            self.config,
            travel=HaversineTravelTimeProvider(),
        )

        self.assertEqual(len(result.policies), len(DEFAULT_POLICIES))
        for report in result.policies:
            metrics = report.metrics()
            required_metrics = {
                "bakeryPickupCoverage",
                "completedDeliveries",
                "pantryCoverageCount",
                "pantryCoveragePercentage",
                "pantriesNeverServedPercentage",
                "pantryServiceGini",
                "pantryServiceGap",
                "averageDriveMinutes",
                "averageDistanceMiles",
                "averagePredepartureWaitMinutes",
                "averageTotalTripDurationMinutes",
                "unservedPickups",
                "foodSavedKg",
                "foodWastedKg",
                "transportKgCO2e",
                "wastePathwayKgCO2e",
                "totalDirectKgCO2e",
                "driverAcceptanceRate",
                "expectedDriverAcceptanceRate",
                "likelyAcceptanceRate",
                "likelyRejectionRate",
                "averageSolverRuntimeSeconds",
                "averageOptimalityGap",
            }
            self.assertTrue(required_metrics.issubset(metrics))
            self.assertEqual(metrics["routesRejected"], 0)
            for day in report.days:
                pickup_ids = [item.pickup_id for item in day.completed]
                driver_ids = [item.driver_id for item in day.completed]
                self.assertEqual(len(pickup_ids), len(set(pickup_ids)))
                self.assertEqual(len(driver_ids), len(set(driver_ids)))

    def test_public_payload_compares_four_declared_academic_models(self) -> None:
        payload = build_web_payload(
            self.snapshot,
            start_date=date(2026, 8, 24),
            days=2,
            seed=77,
            drivers_per_day=4,
            matching_interval_minutes=60,
            max_simultaneous_drivers=3,
        )

        self.assertEqual(
            [item["policy"] for item in payload["results"]],
            [
                "bakedboston_mip",
                "nair_2018_distance_first",
                "xue_zou_2025_total_curb",
                "horner_2021_slsf_noz",
            ],
        )
        self.assertEqual(
            payload["policyMetadata"]["bakedboston_mip"]["selectionMode"],
            "rank_one_choice",
        )
        self.assertEqual(
            payload["policyMetadata"]["nair_2018_distance_first"]["selectionMode"],
            "direct_assignment",
        )
        self.assertEqual(
            payload["policyMetadata"]["xue_zou_2025_total_curb"]["selectionMode"],
            "direct_assignment",
        )
        self.assertEqual(
            payload["policyMetadata"]["horner_2021_slsf_noz"]["selectionMode"],
            "direct_assignment",
        )
        horner = next(
            item
            for item in payload["results"]
            if item["policy"] == "horner_2021_slsf_noz"
        )
        self.assertGreater(horner["metrics"]["menuOptionsOffered"], 0)
        self.assertGreater(horner["metrics"]["averageMenuSize"], 0)
        self.assertIn("decisionEpochs", horner["days"][0])
        self.assertTrue(any(
            recommendation["routes"]
            for day in horner["days"]
            for epoch in day["decisionEpochs"]
            for recommendation in epoch["driverRecommendations"]
        ))
        for item in payload["results"]:
            self.assertTrue({
                "foodSavedKg",
                "foodWastedKg",
                "transportKgCO2e",
                "wastePathwayKgCO2e",
                "totalDirectKgCO2e",
                "expectedDriverAcceptanceRate",
                "likelyAcceptanceRate",
                "likelyRejectionRate",
            }.issubset(item["metrics"]))

    def test_multi_horizon_report_contains_requested_horizons(self) -> None:
        result = compare_horizons(
            self.snapshot,
            start_date=date(2026, 8, 24),
            horizons=(3, 4, 5),
            seeds=(91,),
            policies=(RoutingPolicy.BAKEDBOSTON_MIP,),
            drivers_per_day=3,
            bakery_food_probability=1,
            staffed_pantry_open_probability=1,
            matching_interval_minutes=10,
            acceptance_enabled=False,
        )

        self.assertEqual(set(result.summary()), {"3", "4", "5"})
        self.assertEqual(len(result.runs), 3)
        self.assertEqual(result.as_dict()["runtime"]["solverBackend"], "gurobi")
        self.assertIsNotNone(result.as_dict()["runtime"]["gurobiVersion"])
        self.assertTrue(all(
            "bakedboston_mip" in policies
            for policies in result.summary().values()
        ))
        self.assertTrue(all(
            not run.scenario.config.acceptance_enabled
            and run.scenario.config.matching_interval_minutes == 10
            for run in result.runs
        ))

    def test_academic_fixture_creates_nontrivial_policy_comparison(self) -> None:
        fixture_path = (
            Path(__file__).resolve().parents[1]
            / "data"
            / "academic_comparison_snapshot.json"
        )
        snapshot = parse_snapshot(json.loads(fixture_path.read_text()))
        config = ExperimentConfig(
            SimulationConfig(
                start_date=date(2026, 8, 24),
                days=5,
                random_seed=2026,
                drivers_per_day=12,
                bakery_food_probability=0.88,
                staffed_pantry_open_probability=0.90,
            ),
            matching_interval_minutes=60,
            max_simultaneous_drivers=3,
            acceptance_enabled=False,
        )

        result = compare_policies(
            snapshot,
            config,
            travel=HaversineTravelTimeProvider(),
        )
        metrics = {report.policy.value: report.metrics() for report in result.policies}
        # Baseline ranking is intentionally not asserted while comparison
        # policies are being revised. The fixture must still produce a
        # nontrivial, food-accounting-complete result.
        self.assertGreaterEqual(
            metrics[RoutingPolicy.BAKEDBOSTON_MIP.value]["uniquePantriesServed"],
            5,
        )
        mip_report = next(report for report in result.policies if report.policy == RoutingPolicy.BAKEDBOSTON_MIP)
        for day in mip_report.days:
            for assignment in day.completed:
                self.assertAlmostEqual(
                    assignment.food_saved_kg,
                    assignment.estimated_food_kg * assignment.bakery_usable_fraction * assignment.pantry_distribution_fraction,
                )
        self.assertGreater(metrics[RoutingPolicy.BAKEDBOSTON_MIP.value]["foodSavedKg"], 0)
        self.assertGreaterEqual(metrics[RoutingPolicy.BAKEDBOSTON_MIP.value]["uncollectedBakeryFoodKg"], 0)
        self.assertGreater(
            len({
                (
                    round(report_metrics["totalRouteQuality"], 3),
                    round(report_metrics["pantryServiceGini"], 3),
                    report_metrics["uniquePantriesServed"],
                )
                for report_metrics in metrics.values()
            }),
            3,
        )

    def test_selected_route_is_rank_one_in_each_driver_recommendation(self) -> None:
        fixture_path = (
            Path(__file__).resolve().parents[1]
            / "data"
            / "academic_comparison_snapshot.json"
        )
        snapshot = parse_snapshot(json.loads(fixture_path.read_text()))
        scenario = build_scenario(
            snapshot,
            ExperimentConfig(
                SimulationConfig(
                    start_date=date(2026, 8, 24),
                    days=2,
                    random_seed=2026,
                    drivers_per_day=7,
                    bakery_food_probability=1,
                    staffed_pantry_open_probability=1,
                ),
                matching_interval_minutes=120,
                max_simultaneous_drivers=3,
                acceptance_enabled=False,
            ),
        )

        daily_driver_counts = [len(day.requests) for day in scenario.days]
        epoch_driver_counts = [
            count
            for day in scenario.days
            for count in Counter(
                request.login_time for request in day.requests
            ).values()
        ]
        self.assertTrue(all(1 <= count <= 7 for count in daily_driver_counts))
        self.assertTrue(any(count < 7 for count in daily_driver_counts))
        self.assertTrue(all(1 <= count <= 3 for count in epoch_driver_counts))
        self.assertTrue(any(count == 1 for count in epoch_driver_counts))
        self.assertTrue(any(count > 1 for count in epoch_driver_counts))
        self.assertTrue(any(
            request.login_time.minute % 15 != 0
            for day in scenario.days
            for request in day.requests
        ))
        for day in scenario.days:
            daily_epochs = Counter(
                request.earliest_start for request in day.requests
            )
            self.assertLessEqual(
                sum(count > 1 for count in daily_epochs.values()),
                2,
            )

        report = next(
            item
            for item in compare_policies(
                snapshot,
                scenario.config,
                policies=(RoutingPolicy.BAKEDBOSTON_MIP,),
                travel=HaversineTravelTimeProvider(),
            ).policies
            if item.policy == RoutingPolicy.BAKEDBOSTON_MIP
        )
        epochs = [epoch for day in report.days for epoch in day.decision_epochs]
        self.assertTrue(any(len(epoch.requests) > 1 for epoch in epochs))
        self.assertLessEqual(max(len(epoch.requests) for epoch in epochs), 3)
        selected = [
            candidate
            for epoch in epochs
            for candidate in epoch.candidates
            if candidate.selected
        ]
        self.assertTrue(selected)
        self.assertTrue(all(item.recommendation_rank == 1 for item in selected))
        self.assertTrue(all(day.pickup_windows for day in report.days))
        self.assertTrue(all(day.pantry_windows for day in report.days))
        for epoch in epochs:
            serialized = epoch.as_dict()
            pickup_owners: dict[str, set[str]] = defaultdict(set)
            for driver in serialized["driverRecommendations"]:
                if driver["selectedRoute"] is not None:
                    self.assertEqual(
                        driver["selectedRoute"]["recommendationRank"],
                        1,
                    )
                for route in driver["routes"]:
                    pickup_owners[route["pickupId"]].add(driver["requestId"])
                    self.assertIn("acceptanceProbability", route)
                    self.assertIn("distanceMiles", route)
                    self.assertIn("totalTripMinutes", route)
                    self.assertIn("driverStart", route)
                    self.assertIn("bakeryLocation", route)
                    self.assertIn("pantryLocation", route)
            self.assertTrue(
                all(len(owners) == 1 for owners in pickup_owners.values())
            )

    def test_summary_csv_contains_one_row_per_horizon_and_policy(self) -> None:
        result = compare_horizons(
            self.snapshot,
            start_date=date(2026, 8, 24),
            horizons=(3, 4),
            seeds=(91,),
            policies=(
                RoutingPolicy.BAKEDBOSTON_MIP,
                RoutingPolicy.SHORTEST_ROUTE,
            ),
            drivers_per_day=3,
            bakery_food_probability=1,
            staffed_pantry_open_probability=1,
            acceptance_enabled=False,
        )

        with TemporaryDirectory() as directory:
            output = Path(directory) / "summary.csv"
            write_summary_csv(result, output)
            with output.open(newline="") as handle:
                rows = list(csv.DictReader(handle))

        self.assertEqual(len(rows), 4)
        self.assertEqual({row["horizonDays"] for row in rows}, {"3", "4"})
        self.assertEqual(
            {row["policy"] for row in rows},
            {"bakedboston_mip", "shortest_route"},
        )
        self.assertTrue(all(row["totalRouteQuality"] for row in rows))

    def test_elapsed_receiving_opportunity_updates_later_priority(self) -> None:
        zone = ZoneInfo("America/New_York")
        location = Location(
            address_entered="11 East Street, Boston, MA",
            formatted_address="11 East Street, Boston, MA",
            latitude=42.355,
            longitude=-71.025,
        )
        first = Pantry(
            id="pantry:11:window:morning",
            pantry_name="East Pantry",
            location=location,
            receiving_start=datetime(2026, 8, 24, 9, tzinfo=zone),
            receiving_end=datetime(2026, 8, 24, 10, tzinfo=zone),
            latest_permitted_arrival=datetime(2026, 8, 24, 9, 55, tzinfo=zone),
            priority_score=0.5,
        )
        later = Pantry(
            id="pantry:11:window:afternoon",
            pantry_name="East Pantry",
            location=location,
            receiving_start=datetime(2026, 8, 24, 13, tzinfo=zone),
            receiving_end=datetime(2026, 8, 24, 14, tzinfo=zone),
            latest_permitted_arrival=datetime(2026, 8, 24, 13, 55, tzinfo=zone),
            priority_score=0.5,
        )
        history: dict[int, deque[bool]] = {11: deque(maxlen=10)}
        totals: dict[str, dict[str, int]] = defaultdict(
            lambda: {"available": 0, "served": 0}
        )
        finalized: set[str] = set()

        _finalize_pantry_opportunities(
            (first, later),
            history,
            totals,
            served_windows=set(),
            finalized_windows=finalized,
            before=datetime(2026, 8, 24, 11, tzinfo=zone),
        )

        self.assertEqual(finalized, {first.id})
        self.assertEqual(list(history[11]), [False])
        self.assertEqual(totals["East Pantry"], {"available": 1, "served": 0})
        self.assertGreater(pantry_priority(tuple(history[11])), later.priority_score)

    def test_gini_handles_balanced_unbalanced_and_empty_samples(self) -> None:
        self.assertEqual(gini([]), 0)
        self.assertEqual(gini([2, 2, 2]), 0)
        self.assertGreater(gini([0, 0, 6]), 0.5)


if __name__ == "__main__":
    unittest.main()
