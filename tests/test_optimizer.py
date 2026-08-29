from __future__ import annotations

import unittest
from dataclasses import replace
from datetime import datetime
from zoneinfo import ZoneInfo

from bakedboston_optimizer.models import (
    AssignmentCandidate,
    BakeryPickup,
    DriverRequest,
    Location,
    Pantry,
    RouteCandidate,
)
from bakedboston_optimizer.optimizer import (
    OptimizationWeights,
    _distance_outside_preference_area,
    _normalize_spatial_deviation,
    allocate_recommendation_layer,
    optimize_assignment_candidates,
    optimize_horner_slsf_noz_candidates,
    optimize_nair_distance_first_candidates,
    optimize_network,
    optimize_xue_zou_total_curb_candidates,
    rank_routes,
)
from bakedboston_optimizer.google_maps import _duration_minutes


class FixedTravel:
    def __init__(self, minutes: dict[tuple[str, str], float]) -> None:
        self.minutes = minutes

    def duration_minutes(self, origin: Location, destination: Location, departure_at: datetime) -> float:
        del departure_at
        return self.minutes[(origin.address_entered, destination.address_entered)]


class OptimizerTests(unittest.TestCase):
    def setUp(self) -> None:
        timezone = ZoneInfo("America/New_York")
        self.day = datetime(2026, 7, 27, tzinfo=timezone)
        self.start = self.location("start")
        self.bakery = BakeryPickup(
            id="b1",
            bakery_name="Bakery",
            location=self.location("bakery"),
            ready_at=self.day.replace(hour=17),
            pickup_deadline=self.day.replace(hour=18),
        )
        self.pantry = Pantry(
            id="p1",
            pantry_name="Pantry",
            location=self.location("pantry"),
            receiving_start=self.day.replace(hour=17),
            receiving_end=self.day.replace(hour=19),
            latest_permitted_arrival=self.day.replace(hour=18, minute=30),
            priority_score=0.8,
        )
        self.request = DriverRequest(
            earliest_start=self.day.replace(hour=16, minute=45),
            latest_finish=self.day.replace(hour=19),
            start_location=self.start,
        )

    @staticmethod
    def location(address: str) -> Location:
        return Location(address, address, 42.0, -71.0)

    def test_feasible_route_includes_service_times(self) -> None:
        routes = rank_routes(
            [self.bakery],
            [self.pantry],
            self.request,
            FixedTravel({("start", "bakery"): 10, ("bakery", "pantry"): 20}),
        )
        self.assertEqual(len(routes), 1)
        self.assertEqual(routes[0].pickup_at, self.day.replace(hour=17))
        self.assertEqual(routes[0].pantry_arrival_at, self.day.replace(hour=17, minute=25))
        self.assertEqual(routes[0].finish_at, self.day.replace(hour=17, minute=30))

    def test_login_time_is_not_forced_departure_time(self) -> None:
        request = DriverRequest(
            earliest_start=self.day.replace(hour=16),
            latest_finish=self.day.replace(hour=18),
            start_location=self.start,
            logged_at=self.day.replace(hour=14, minute=17),
            search_until=self.day.replace(hour=19),
        )
        routes = rank_routes(
            [self.bakery],
            [self.pantry],
            request,
            FixedTravel({("start", "bakery"): 10, ("bakery", "pantry"): 20}),
        )

        self.assertEqual(len(routes), 1)
        self.assertEqual(routes[0].depart_at, self.day.replace(hour=16, minute=50))
        self.assertEqual(routes[0].pickup_at, self.day.replace(hour=17))
        self.assertEqual(routes[0].finish_at, self.day.replace(hour=17, minute=30))
        self.assertEqual(routes[0].waiting_minutes, 153)
        self.assertEqual(routes[0].facility_waiting_minutes, 0)
        self.assertTrue(routes[0].within_preferred_window)

    def test_requested_time_window_is_soft_not_hard(self) -> None:
        request = DriverRequest(
            earliest_start=self.day.replace(hour=16),
            latest_finish=self.day.replace(hour=16, minute=30),
            start_location=self.start,
            logged_at=self.day.replace(hour=14, minute=17),
            search_until=self.day.replace(hour=18, minute=30),
        )
        routes = rank_routes(
            [self.bakery],
            [self.pantry],
            request,
            FixedTravel({("start", "bakery"): 10, ("bakery", "pantry"): 20}),
        )

        self.assertEqual(len(routes), 1)
        self.assertEqual(routes[0].depart_at, self.day.replace(hour=16, minute=50))
        self.assertEqual(routes[0].requested_time_deviation_minutes, 60)
        self.assertEqual(routes[0].requested_window_minutes, 30)
        self.assertEqual(routes[0].requested_time_deviation_ratio, 2.0)
        self.assertFalse(routes[0].within_preferred_window)

    def test_requested_window_must_be_at_least_thirty_minutes(self) -> None:
        with self.assertRaisesRegex(ValueError, "at least 30 minutes"):
            DriverRequest(
                earliest_start=self.day.replace(hour=16),
                latest_finish=self.day.replace(hour=16, minute=29),
                start_location=self.start,
            )

    def test_claimed_pickup_is_excluded(self) -> None:
        claimed = BakeryPickup(**{**self.bakery.__dict__, "claimed": True})
        routes = rank_routes(
            [claimed],
            [self.pantry],
            self.request,
            FixedTravel({("start", "bakery"): 10, ("bakery", "pantry"): 20}),
        )
        self.assertEqual(routes, [])

    def test_arrival_after_pantry_deadline_is_infeasible(self) -> None:
        routes = rank_routes(
            [self.bakery],
            [self.pantry],
            self.request,
            FixedTravel({("start", "bakery"): 10, ("bakery", "pantry"): 100}),
        )
        self.assertEqual(routes, [])

    def test_priority_can_outweigh_small_travel_difference(self) -> None:
        lower_priority = Pantry(
            **{**self.pantry.__dict__, "id": "p2", "pantry_name": "Nearby", "location": self.location("nearby"), "priority_score": 0.2}
        )
        routes = rank_routes(
            [self.bakery],
            [self.pantry, lower_priority],
            self.request,
            FixedTravel({("start", "bakery"): 10, ("bakery", "pantry"): 20, ("bakery", "nearby"): 15}),
        )
        self.assertEqual(routes[0].pantry_id, "p1")

    def test_google_duration_is_converted_to_minutes(self) -> None:
        self.assertAlmostEqual(_duration_minutes("150s"), 2.5)

    def test_network_model_assigns_each_driver_and_pickup_once(self) -> None:
        second_pickup = BakeryPickup(
            id="b2",
            bakery_name="Second Bakery",
            location=self.location("bakery-2"),
            ready_at=self.day.replace(hour=17),
            pickup_deadline=self.day.replace(hour=18),
        )
        second_request = DriverRequest(
            id="r2",
            driver_id="d2",
            earliest_start=self.day.replace(hour=16, minute=45),
            latest_finish=self.day.replace(hour=19),
            start_location=self.location("start-2"),
        )
        first_request = DriverRequest(
            **{**self.request.__dict__, "id": "r1", "driver_id": "d1"}
        )
        result = optimize_network(
            [self.bakery, second_pickup],
            [self.pantry],
            [first_request, second_request],
            FixedTravel({
                ("start", "bakery"): 10,
                ("start", "bakery-2"): 12,
                ("start-2", "bakery"): 12,
                ("start-2", "bakery-2"): 10,
                ("bakery", "pantry"): 15,
                ("bakery-2", "pantry"): 15,
            }),
        )

        self.assertEqual(result.diagnostics.backend, "gurobi")
        self.assertEqual(result.diagnostics.matched_count, 2)
        self.assertEqual(len({item.driver_id for item in result.assignments}), 2)
        self.assertEqual(len({item.route.bakery_id for item in result.assignments}), 2)
        # Pantries intentionally have no capacity constraint while open.
        self.assertEqual({item.route.pantry_id for item in result.assignments}, {"p1"})

    def test_multiple_requests_from_one_driver_produce_one_assignment(self) -> None:
        requests = [
            DriverRequest(**{**self.request.__dict__, "id": "r1", "driver_id": "d1"}),
            DriverRequest(**{**self.request.__dict__, "id": "r2", "driver_id": "d1"}),
        ]
        result = optimize_network(
            [self.bakery],
            [self.pantry],
            requests,
            FixedTravel({("start", "bakery"): 10, ("bakery", "pantry"): 15}),
        )

        self.assertEqual(result.diagnostics.matched_count, 1)
        self.assertEqual(len({item.driver_id for item in result.assignments}), 1)

    def test_expected_completions_are_optimized_before_route_quality(self) -> None:
        second_pickup = BakeryPickup(
            id="b2",
            bakery_name="Second Bakery",
            location=self.location("bakery-2"),
            ready_at=self.day.replace(hour=17),
            pickup_deadline=self.day.replace(hour=19),
        )
        requests = [
            DriverRequest(
                id="r1", driver_id="d1",
                earliest_start=self.day.replace(hour=16),
                latest_finish=self.day.replace(hour=22),
                start_location=self.location("start"),
            ),
            DriverRequest(
                id="r2", driver_id="d2",
                earliest_start=self.day.replace(hour=16),
                latest_finish=self.day.replace(hour=22),
                start_location=self.location("start-2"),
            ),
        ]
        late_pantry = Pantry(**{
            **self.pantry.__dict__,
            "receiving_end": self.day.replace(hour=22),
            "latest_permitted_arrival": self.day.replace(hour=21, minute=30),
        })
        result = optimize_network(
            [self.bakery, second_pickup],
            [late_pantry],
            requests,
            FixedTravel({
                ("start", "bakery"): 70,
                ("start", "bakery-2"): 75,
                ("start-2", "bakery"): 75,
                ("start-2", "bakery-2"): 70,
                ("bakery", "pantry"): 70,
                ("bakery-2", "pantry"): 70,
            }),
            weights=OptimizationWeights(pantry_priority_reward=0),
        )

        self.assertEqual(result.diagnostics.matched_count, 2)
        self.assertGreaterEqual(result.diagnostics.route_quality, 0)
        self.assertLessEqual(result.diagnostics.route_quality, 100)

    def test_participation_estimate_can_outweigh_route_quality(self) -> None:
        candidates = [
            self.assignment(
                "r1", "d1", "b1", "p1", 5,
                acceptance_probability=0.90,
            ),
            self.assignment(
                "r1", "d1", "b2", "p2", 100,
                acceptance_probability=0.40,
            ),
        ]

        result = optimize_assignment_candidates(candidates)

        self.assertEqual(result.diagnostics.matched_count, 1)
        self.assertEqual(result.assignments[0].route.bakery_id, "b1")
        self.assertAlmostEqual(
            result.diagnostics.expected_completed_deliveries,
            0.90,
        )

    def test_nair_adaptation_minimizes_distance_after_service_count(self) -> None:
        long_high_score = replace(
            self.assignment(
                "r1", "d1", "b1", "p1", 100,
                acceptance_probability=0.99,
            ),
            route=replace(
                self.assignment("r1", "d1", "b1", "p1", 100).route,
                route_distance_miles=12,
                food_saved_kg=100,
                net_environmental_benefit_kg_co2e=100,
            ),
        )
        short_low_score = replace(
            self.assignment(
                "r1", "d1", "b2", "p2", -100,
                acceptance_probability=0.01,
            ),
            route=replace(
                self.assignment("r1", "d1", "b2", "p2", -100).route,
                route_distance_miles=3,
                food_saved_kg=1,
                net_environmental_benefit_kg_co2e=-100,
            ),
        )

        result = optimize_nair_distance_first_candidates(
            [long_high_score, short_low_score]
        )

        self.assertEqual(result.diagnostics.matched_count, 1)
        self.assertEqual(result.assignments[0].route.bakery_id, "b2")
        self.assertAlmostEqual(result.diagnostics.route_distance_miles, 3)

    def test_horner_slsf_noz_builds_menu_then_assigns_only_willing_routes(self) -> None:
        candidates = [
            replace(
                self.assignment("r1", "d1", "b1", "p1", 100, 0.5),
                route=replace(
                    self.assignment("r1", "d1", "b1", "p1", 100, 0.5).route,
                    route_distance_miles=1,
                ),
            ),
            replace(
                self.assignment("r1", "d1", "b2", "p1", -100, 0.5),
                route=replace(
                    self.assignment("r1", "d1", "b2", "p1", -100, 0.5).route,
                    route_distance_miles=4,
                ),
            ),
            replace(
                self.assignment("r2", "d2", "b1", "p1", -100, 0.5),
                route=replace(
                    self.assignment("r2", "d2", "b1", "p1", -100, 0.5).route,
                    route_distance_miles=4,
                ),
            ),
            replace(
                self.assignment("r2", "d2", "b2", "p1", 100, 0.5),
                route=replace(
                    self.assignment("r2", "d2", "b2", "p1", 100, 0.5).route,
                    route_distance_miles=1,
                ),
            ),
        ]
        realized_willingness = {
            ("r1", "b1", "p1"): False,
            ("r1", "b2", "p1"): True,
            ("r2", "b1", "p1"): True,
            ("r2", "b2", "p1"): False,
        }

        result = optimize_horner_slsf_noz_candidates(
            candidates,
            scenario_seed=17,
            realized_willingness=realized_willingness,
        )

        self.assertEqual(result.diagnostics.training_scenario_count, 100)
        self.assertLessEqual(result.diagnostics.menu_size, 10)
        self.assertTrue(all(
            realized_willingness[
                (item.request_id, item.route.bakery_id, item.route.pantry_id)
            ]
            for item in result.assignments
        ))
        self.assertEqual(
            {(item.request_id, item.route.bakery_id) for item in result.assignments},
            {("r1", "b2"), ("r2", "b1")},
        )
        self.assertEqual(result.diagnostics.willing_menu_options, 2)
        self.assertEqual(result.diagnostics.unhappy_driver_count, 0)

    def test_horner_menu_contains_one_destination_per_driver_pickup(self) -> None:
        candidates = [
            self.assignment("r1", "d1", "b1", "p1", 1, 0.7),
            self.assignment("r1", "d1", "b1", "p2", 1, 0.7),
        ]
        result = optimize_horner_slsf_noz_candidates(
            candidates,
            scenario_seed=23,
            realized_willingness={
                ("r1", "b1", "p1"): True,
                ("r1", "b1", "p2"): True,
            },
        )

        self.assertEqual(len(result.recommendations), 1)
        self.assertEqual(result.diagnostics.menu_driver_count, 1)

    def test_nair_adaptation_protects_cardinality_before_distance(self) -> None:
        candidates = [
            replace(
                self.assignment("r1", "d1", "b1", "p1", 1),
                route=replace(
                    self.assignment("r1", "d1", "b1", "p1", 1).route,
                    route_distance_miles=1,
                ),
            ),
            replace(
                self.assignment("r1", "d1", "b2", "p1", 1),
                route=replace(
                    self.assignment("r1", "d1", "b2", "p1", 1).route,
                    route_distance_miles=20,
                ),
            ),
            replace(
                self.assignment("r2", "d2", "b1", "p1", 1),
                route=replace(
                    self.assignment("r2", "d2", "b1", "p1", 1).route,
                    route_distance_miles=1,
                ),
            ),
        ]

        result = optimize_nair_distance_first_candidates(candidates)

        self.assertEqual(result.diagnostics.matched_count, 2)
        self.assertEqual(
            {(item.request_id, item.route.bakery_id) for item in result.assignments},
            {("r1", "b2"), ("r2", "b1")},
        )

    def test_xue_zou_total_curb_minimizes_direct_system_emissions(self) -> None:
        lower_direct_emissions = replace(
            self.assignment(
                "r1", "d1", "b1", "p1", -100,
                acceptance_probability=0.01,
            ),
            route=replace(
                self.assignment("r1", "d1", "b1", "p1", -100).route,
                route_distance_miles=12,
                counterfactual_waste_kg_co2e=10,
                residual_waste_kg_co2e=1,
                transport_kg_co2e=2,
            ),
        )
        higher_direct_emissions = replace(
            self.assignment(
                "r1", "d1", "b2", "p2", 100,
                acceptance_probability=0.99,
            ),
            route=replace(
                self.assignment("r1", "d1", "b2", "p2", 100).route,
                route_distance_miles=1,
                counterfactual_waste_kg_co2e=10,
                residual_waste_kg_co2e=8,
                transport_kg_co2e=0.5,
            ),
        )

        result = optimize_xue_zou_total_curb_candidates(
            [lower_direct_emissions, higher_direct_emissions]
        )

        self.assertEqual(result.diagnostics.matched_count, 1)
        self.assertEqual(result.assignments[0].route.bakery_id, "b1")
        self.assertAlmostEqual(result.diagnostics.route_quality, 7)

    def test_xue_zou_total_curb_protects_cardinality_before_emissions(self) -> None:
        def with_direct_benefit(
            candidate: AssignmentCandidate,
            benefit: float,
        ) -> AssignmentCandidate:
            return replace(
                candidate,
                route=replace(
                    candidate.route,
                    counterfactual_waste_kg_co2e=max(0, benefit),
                    residual_waste_kg_co2e=max(0, -benefit),
                    transport_kg_co2e=0,
                ),
            )

        candidates = [
            with_direct_benefit(
                self.assignment("r1", "d1", "b1", "p1", 1),
                100,
            ),
            with_direct_benefit(
                self.assignment("r1", "d1", "b2", "p1", 1),
                -100,
            ),
            with_direct_benefit(
                self.assignment("r2", "d2", "b1", "p1", 1),
                100,
            ),
        ]

        result = optimize_xue_zou_total_curb_candidates(candidates)

        self.assertEqual(result.diagnostics.matched_count, 2)
        self.assertEqual(
            {(item.request_id, item.route.bakery_id) for item in result.assignments},
            {("r1", "b2"), ("r2", "b1")},
        )

    def test_route_quality_breaks_ties_between_equal_match_counts(self) -> None:
        lower_priority = Pantry(
            **{
                **self.pantry.__dict__,
                "id": "p2",
                "pantry_name": "Lower Priority",
                "location": self.location("pantry-2"),
                "priority_score": 0.1,
            }
        )
        request = DriverRequest(**{**self.request.__dict__, "id": "r1", "driver_id": "d1"})
        result = optimize_network(
            [self.bakery],
            [self.pantry, lower_priority],
            [request],
            FixedTravel({
                ("start", "bakery"): 10,
                ("bakery", "pantry"): 15,
                ("bakery", "pantry-2"): 15,
            }),
        )

        self.assertEqual(result.assignments[0].route.pantry_id, "p1")

    def test_normalized_stage_two_expands_pantry_coverage(self) -> None:
        candidates = [
            self.assignment("r1", "d1", "b1", "p1", 10),
            self.assignment("r1", "d1", "b1", "p2", 10),
            self.assignment("r2", "d2", "b2", "p1", 10),
            self.assignment("r2", "d2", "b2", "p2", 10),
        ]
        result = optimize_assignment_candidates(candidates)
        self.assertEqual(result.diagnostics.matched_count, 2)
        self.assertEqual({item.route.pantry_id for item in result.assignments}, {"p1", "p2"})
        self.assertGreaterEqual(result.diagnostics.route_quality, 0)
        self.assertLessEqual(result.diagnostics.route_quality, 100)

    def test_recommendation_layer_serves_more_drivers_before_adding_quality(self) -> None:
        candidates = [
            self.assignment("r1", "d1", "b1", "p1", 100),
            self.assignment("r1", "d1", "b2", "p1", 90),
            self.assignment("r2", "d2", "b1", "p1", 80),
        ]

        allocated = allocate_recommendation_layer(candidates)

        self.assertEqual(len(allocated), 2)
        self.assertEqual(
            {(item.request_id, item.route.bakery_id) for item in allocated},
            {("r1", "b2"), ("r2", "b1")},
        )
        # The same pantry can appear in both menus; only bakery pickups are scarce.
        self.assertEqual({item.route.pantry_id for item in allocated}, {"p1"})

    def test_spatial_deviation_is_measured_to_zip_area_edge(self) -> None:
        preference_center = Location("ZIP center", "ZIP center", 42.0, -71.0)
        inside = Location("inside", "inside", 42.0, -71.01)
        outside = Location("outside", "outside", 42.0, -70.9)

        self.assertEqual(
            _distance_outside_preference_area(inside, preference_center, 2.0),
            0.0,
        )
        # At this latitude, 0.1 degrees of longitude is about 5.1 miles. The
        # deviation is measured to the nearest edge of the two-mile circle.
        self.assertAlmostEqual(
            _distance_outside_preference_area(outside, preference_center, 2.0),
            3.15,
            delta=0.15,
        )

    def test_spatial_misses_are_normalized_without_exact_match_reward(self) -> None:
        base_route = self.assignment("r1", "d1", "b1", "p1", 10).route
        routes = [
            replace(base_route, bakery_id="exact", origin_deviation_miles=0, destination_deviation_miles=0),
            replace(base_route, bakery_id="middle", origin_deviation_miles=1, destination_deviation_miles=1),
            replace(base_route, bakery_id="furthest", origin_deviation_miles=2, destination_deviation_miles=4),
        ]
        request = replace(
            self.request,
            preferred_destination=self.location("destination ZIP"),
        )

        normalized = _normalize_spatial_deviation(
            routes,
            request,
            OptimizationWeights(spatial_deviation_ratio_penalty=18),
        )
        by_bakery = {route.bakery_id: route for route in normalized}

        self.assertEqual(by_bakery["exact"].normalized_spatial_deviation, 0.0)
        self.assertEqual(by_bakery["exact"].score, 10)
        self.assertAlmostEqual(by_bakery["middle"].normalized_spatial_deviation, 0.375)
        self.assertAlmostEqual(by_bakery["middle"].score, 3.25)
        self.assertEqual(by_bakery["furthest"].normalized_spatial_deviation, 1.0)
        self.assertEqual(by_bakery["furthest"].score, -8)

    def assignment(
        self,
        request_id: str,
        driver_id: str,
        bakery_id: str,
        pantry_id: str,
        score: float,
        acceptance_probability: float = 1.0,
    ) -> AssignmentCandidate:
        timestamp = self.day.replace(hour=17)
        return AssignmentCandidate(
            request_id=request_id,
            driver_id=driver_id,
            route=RouteCandidate(
                bakery_id=bakery_id,
                bakery_name=bakery_id,
                bakery_address=f"{bakery_id} address",
                pantry_id=pantry_id,
                pantry_name=pantry_id,
                pantry_address=f"{pantry_id} address",
                depart_at=timestamp,
                pickup_at=timestamp,
                pantry_arrival_at=timestamp,
                finish_at=timestamp,
                drive_minutes=10,
                waiting_minutes=0,
                destination_minutes=0,
                pantry_priority=0.5,
                score=score,
                explanation=(),
                acceptance_probability=acceptance_probability,
            ),
        )


if __name__ == "__main__":
    unittest.main()
