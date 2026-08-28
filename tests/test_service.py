from __future__ import annotations

import unittest
from datetime import datetime
from unittest.mock import patch

from bakedboston_optimizer.network import parse_snapshot
from bakedboston_optimizer.service import _pantries, simulate_custom_experiment


class ServiceFeasibilityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.earliest = datetime.fromisoformat("2026-07-27T16:00:00-04:00")
        self.latest = datetime.fromisoformat("2026-07-27T20:00:00-04:00")

    @staticmethod
    def payload(service_mode: str, confirmed: bool = False) -> dict:
        confirmation = [{
            "recipientId": 4,
            "actionKey": "pantry-open:4:one-time:20",
            "acknowledgedAt": "2026-07-27T16:01:00-04:00",
        }] if confirmed else []
        return {
            "schemaVersion": 2,
            "generatedAt": "2026-07-27T16:05:00-04:00",
            "bakeries": [],
            "pantries": [{
                "id": 4,
                "name": "Pantry",
                "address": "1 Main Street, Boston, MA",
                "formattedAddress": "1 Main Street, Boston, MA 02110, USA",
                "googlePlaceId": "place-4",
                "addressValidationStatus": "validated",
                "latitude": 42.36,
                "longitude": -71.06,
                "openTime": "[]",
                "closeTime": "[]",
                "latestPermittedArrival": "[]",
                "serviceModes": "[]",
                "deliveriesSevenDays": 0,
            }],
            "availabilityWindows": [{
                "id": 20,
                "organizationType": "pantry",
                "organizationId": 4,
                "startsAt": "2026-07-27T16:00:00-04:00",
                "endsAt": "2026-07-27T19:00:00-04:00",
                "latestArrival": "2026-07-27T18:45:00-04:00",
                "serviceMode": service_mode,
                "paused": False,
            }],
            "availabilityPauses": [],
            "scheduleExceptions": [],
            "routes": [],
            "pickupOccurrences": [],
            "rideRequests": [],
            "routeOffers": [],
            "drivers": [],
            "pantryWindowConfirmations": confirmation,
        }

    def test_staffed_window_is_excluded_until_confirmed(self) -> None:
        unconfirmed = parse_snapshot(self.payload("staffed"))
        confirmed = parse_snapshot(self.payload("staffed", confirmed=True))

        self.assertEqual(_pantries(unconfirmed, self.earliest, self.latest), [])
        self.assertEqual(len(_pantries(confirmed, self.earliest, self.latest)), 1)

    def test_unattended_window_does_not_require_confirmation(self) -> None:
        snapshot = parse_snapshot(self.payload("unattended"))

        self.assertEqual(len(_pantries(snapshot, self.earliest, self.latest)), 1)

    def test_active_pause_excludes_one_time_window(self) -> None:
        payload = self.payload("unattended")
        payload["availabilityPauses"] = [{
            "organizationType": "pantry",
            "organizationId": 4,
            "createdAt": "2026-07-27T15:55:00-04:00",
            "endsAt": "2026-07-27T19:00:00-04:00",
        }]
        snapshot = parse_snapshot(payload)

        self.assertEqual(_pantries(snapshot, self.earliest, self.latest), [])


class CustomExperimentTests(unittest.TestCase):
    @patch("bakedboston_optimizer.service.build_web_payload")
    def test_custom_experiment_samples_network_and_enforces_five_day_horizon(self, mocked_build) -> None:
        mocked_build.return_value = {
            "scenario": {},
            "runtime": {"solverBackend": "gurobi"},
        }

        result = simulate_custom_experiment({
            "days": 4,
            "driversPerDay": 5,
            "bakeryCount": 4,
            "pantryCount": 3,
            "randomSeed": 77,
            "maxSimultaneousDrivers": 2,
        })

        sampled_snapshot = mocked_build.call_args.args[0]
        self.assertEqual(len(sampled_snapshot.eligible_bakeries), 4)
        self.assertEqual(len(sampled_snapshot.eligible_pantries), 3)
        self.assertEqual(mocked_build.call_args.kwargs["days"], 5)
        self.assertEqual(mocked_build.call_args.kwargs["drivers_per_day"], 5)
        self.assertEqual(mocked_build.call_args.kwargs["seed"], 77)
        self.assertEqual(mocked_build.call_args.kwargs["max_simultaneous_drivers"], 2)
        self.assertEqual(result["displayMode"], "live_custom_gurobi_experiment")
        self.assertEqual(result["scenario"]["bakeryCount"], 4)
        self.assertEqual(result["scenario"]["pantryCount"], 3)

    def test_custom_experiment_rejects_unbounded_driver_request(self) -> None:
        with self.assertRaisesRegex(ValueError, "driversPerDay"):
            simulate_custom_experiment({"driversPerDay": 13})


if __name__ == "__main__":
    unittest.main()
