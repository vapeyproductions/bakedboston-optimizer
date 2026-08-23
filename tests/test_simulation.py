from __future__ import annotations

import unittest
from datetime import date

from bakedboston_optimizer.network import parse_snapshot
from bakedboston_optimizer.simulation import (
    SimulationConfig,
    _bakery_windows,
    _pantry_windows,
    pantry_priority,
    simulate_snapshot,
)
from bakedboston_optimizer.travel import HaversineTravelTimeProvider
from zoneinfo import ZoneInfo


class SimulationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.monday = date(2026, 8, 24)
        self.snapshot = parse_snapshot({
            "schemaVersion": 2,
            "generatedAt": "2026-08-20T12:00:00Z",
            "bakeries": [{
                "id": 1,
                "name": "Example Bakery",
                "address": "1 Example Street, Boston, MA",
                "formattedAddress": "1 Example Street, Boston, MA",
                "googlePlaceId": "bakery-place",
                "addressValidationStatus": "validated",
                "latitude": 42.355,
                "longitude": -71.065,
                "recurringDays": "Mon",
                "readyTime": '{"Mon":"17:00"}',
                "pickupDeadline": '{"Mon":"18:00"}',
            }],
            "pantries": [{
                "id": 2,
                "name": "Example Pantry",
                "address": "2 Example Street, Boston, MA",
                "formattedAddress": "2 Example Street, Boston, MA",
                "googlePlaceId": "pantry-place",
                "addressValidationStatus": "validated",
                "latitude": 42.360,
                "longitude": -71.060,
                "recurringDays": "Mon",
                "openTime": '[{"recurrence":"weekly","day":"Mon","time":"16:30"}]',
                "closeTime": '[{"recurrence":"weekly","day":"Mon","time":"19:00"}]',
                "latestPermittedArrival": '[{"recurrence":"weekly","day":"Mon","time":"18:45"}]',
                "serviceModes": '[{"mode":"unattended"}]',
            }],
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

    def test_priority_uses_receiving_opportunities_not_calendar_days(self) -> None:
        self.assertEqual(pantry_priority([]), 0.5)
        self.assertGreater(pantry_priority([False]), 0.5)
        self.assertLess(pantry_priority([True]), 0.5)
        self.assertGreater(
            pantry_priority([True, False, False]),
            pantry_priority([True, True, False]),
        )

    def test_schedule_templates_expand_into_occurrences(self) -> None:
        zone = ZoneInfo("America/New_York")

        pickups = _bakery_windows(self.snapshot, self.monday, zone)
        pantries = _pantry_windows(self.snapshot, self.monday, zone)

        self.assertEqual(len(pickups), 1)
        self.assertEqual(pickups[0].ready_at.hour, 17)
        self.assertEqual(len(pantries), 1)
        self.assertEqual(pantries[0][0].latest_permitted_arrival.minute, 45)
        self.assertEqual(pantries[0][1], "unattended")

    def test_seeded_simulation_produces_repeatable_assignments(self) -> None:
        config = SimulationConfig(
            start_date=self.monday,
            days=2,
            random_seed=99,
            drivers_per_day=2,
            bakery_food_probability=1,
            staffed_pantry_open_probability=1,
        )

        first = simulate_snapshot(self.snapshot, config, HaversineTravelTimeProvider())
        second = simulate_snapshot(self.snapshot, config, HaversineTravelTimeProvider())

        first_assignments = [item.as_dict() for day in first.days for item in day.assignments]
        second_assignments = [item.as_dict() for day in second.days for item in day.assignments]
        self.assertEqual(first_assignments, second_assignments)
        self.assertEqual(first.metrics()["matchedPickups"], 1)
        self.assertEqual(first.metrics()["pickupCoverage"], 1.0)
        self.assertEqual(first.as_dict()["mode"], "academic_schedule_simulation")

    def test_schedule_exception_removes_that_days_occurrences(self) -> None:
        payload = self.snapshot
        exception_snapshot = type(payload)(
            **{
                **payload.__dict__,
                "schedule_exceptions": ({
                    "organizationType": "bakery",
                    "organizationId": 1,
                    "exceptionDate": self.monday.isoformat(),
                },),
            }
        )

        pickups = _bakery_windows(
            exception_snapshot,
            self.monday,
            ZoneInfo("America/New_York"),
        )

        self.assertEqual(pickups, [])


if __name__ == "__main__":
    unittest.main()
