from __future__ import annotations

import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from bakedboston_optimizer.batch import AssignmentCandidate, solve_assignment
from bakedboston_optimizer.models import RouteCandidate


class BatchAssignmentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.time = datetime(2026, 7, 27, 17, tzinfo=ZoneInfo("America/New_York"))

    def candidate(self, driver_id: str, pickup_id: str, pantry_id: str, score: float) -> AssignmentCandidate:
        route = RouteCandidate(
            bakery_id=pickup_id,
            bakery_name=f"Bakery {pickup_id}",
            bakery_address="1 Bakery Way",
            pantry_id=pantry_id,
            pantry_name=f"Pantry {pantry_id}",
            pantry_address="1 Pantry Way",
            depart_at=self.time,
            pickup_at=self.time,
            pantry_arrival_at=self.time,
            finish_at=self.time,
            drive_minutes=10,
            waiting_minutes=0,
            destination_minutes=0,
            pantry_priority=0.5,
            score=score,
            explanation=("Test candidate",),
        )
        return AssignmentCandidate(driver_id=driver_id, route=route)

    def test_global_assignment_beats_greedy_route_choice(self) -> None:
        result = solve_assignment([
            self.candidate("d1", "b1", "p1", 10),
            self.candidate("d1", "b2", "p1", 9),
            self.candidate("d2", "b1", "p2", 8),
        ], driver_ids=("d1", "d2"))

        selected = {(candidate.driver_id, candidate.route.bakery_id) for candidate in result.assignments}
        self.assertEqual(selected, {("d1", "b2"), ("d2", "b1")})
        self.assertEqual(result.assignment_count, 2)
        self.assertAlmostEqual(result.route_score, 17)
        self.assertEqual(result.status, "optimal")

    def test_feasible_delivery_is_selected_even_with_negative_score(self) -> None:
        result = solve_assignment([
            self.candidate("d1", "b1", "p1", -250),
        ], driver_ids=("d1",))

        self.assertEqual(result.assignment_count, 1)
        self.assertEqual(result.assignments[0].route.bakery_id, "b1")

    def test_driver_and_pickup_are_each_used_at_most_once(self) -> None:
        result = solve_assignment([
            self.candidate("d1", "b1", "p1", 9),
            self.candidate("d1", "b2", "p1", 8),
            self.candidate("d2", "b1", "p2", 7),
            self.candidate("d2", "b2", "p2", 6),
        ], driver_ids=("d1", "d2"))

        drivers = [candidate.driver_id for candidate in result.assignments]
        pickups = [candidate.route.bakery_id for candidate in result.assignments]
        self.assertEqual(len(drivers), len(set(drivers)))
        self.assertEqual(len(pickups), len(set(pickups)))

    def test_no_candidates_returns_all_drivers_unmatched(self) -> None:
        result = solve_assignment([], driver_ids=("d1", "d2"))
        self.assertEqual(result.assignments, ())
        self.assertEqual(result.unmatched_driver_ids, ("d1", "d2"))
        self.assertEqual(result.status, "optimal")


if __name__ == "__main__":
    unittest.main()
