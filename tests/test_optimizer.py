from __future__ import annotations

import unittest
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
    allocate_recommendation_layer,
    optimize_network,
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
        self.assertFalse(routes[0].within_preferred_window)

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

    def test_delivery_count_is_optimized_before_route_quality(self) -> None:
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
        self.assertLess(result.diagnostics.route_quality, 0)

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

    def assignment(
        self,
        request_id: str,
        driver_id: str,
        bakery_id: str,
        pantry_id: str,
        score: float,
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
            ),
        )


if __name__ == "__main__":
    unittest.main()
