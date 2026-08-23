from __future__ import annotations

import unittest
from datetime import datetime

from bakedboston_optimizer.network import parse_snapshot
from bakedboston_optimizer.service import _pantries


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


if __name__ == "__main__":
    unittest.main()
