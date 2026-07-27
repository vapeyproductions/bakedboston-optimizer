from __future__ import annotations

import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from bakedboston_optimizer.models import BakeryPickup, DriverRequest, Location, Pantry
from bakedboston_optimizer.optimizer import rank_routes
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
        self.assertEqual(routes[0].pantry_arrival_at, self.day.replace(hour=17, minute=35))
        self.assertEqual(routes[0].finish_at, self.day.replace(hour=17, minute=50))

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


if __name__ == "__main__":
    unittest.main()
