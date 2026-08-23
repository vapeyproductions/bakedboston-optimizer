from __future__ import annotations

import unittest

from bakedboston_optimizer.network import parse_snapshot


class NetworkTests(unittest.TestCase):
    def test_private_feed_is_parsed_without_contact_data(self) -> None:
        snapshot = parse_snapshot({
            "schemaVersion": 1,
            "generatedAt": "2026-07-27T12:00:00Z",
            "bakeries": [{
                "id": 1,
                "name": "Bakery",
                "address": "1 Main St, Boston, MA",
                "formattedAddress": "1 Main St, Boston, MA 02110, USA",
                "googlePlaceId": "place-1",
                "addressValidationStatus": "validated",
                "latitude": 42.35,
                "longitude": -71.06,
                "recurringDays": "Mon",
                "readyTime": "{}",
                "pickupDeadline": "{}",
            }],
            "pantries": [],
            "availabilityWindows": [],
            "availabilityPauses": [],
            "scheduleExceptions": [],
            "routes": [],
            "pickupOccurrences": [{"id": 10, "status": "confirmed"}],
            "rideRequests": [{"id": 20, "status": "active"}],
            "routeOffers": [{"id": 30, "status": "offered"}],
        })
        bakery = snapshot.bakeries[0]
        self.assertEqual(bakery.location().formatted_address, "1 Main St, Boston, MA 02110, USA")
        self.assertEqual(bakery.schedule["readyTime"], "{}")
        self.assertTrue(bakery.optimization_eligible)
        self.assertEqual(snapshot.eligible_bakeries, snapshot.bakeries)
        self.assertEqual(snapshot.pickup_occurrences[0]["status"], "confirmed")
        self.assertEqual(snapshot.ride_requests[0]["status"], "active")
        self.assertEqual(snapshot.route_offers[0]["status"], "offered")

    def test_missing_coordinates_require_geocoding(self) -> None:
        snapshot = parse_snapshot({
            "schemaVersion": 1,
            "generatedAt": "2026-07-27T12:00:00Z",
            "bakeries": [{
                "id": 1, "name": "Bakery", "address": "Unknown", "latitude": None, "longitude": None,
            }],
            "pantries": [],
        })
        with self.assertRaisesRegex(ValueError, "needs geocoding"):
            snapshot.bakeries[0].location()
        self.assertEqual(snapshot.eligible_bakeries, ())

    def test_schema_two_includes_driver_locations_and_pantry_confirmations(self) -> None:
        snapshot = parse_snapshot({
            "schemaVersion": 2,
            "generatedAt": "2026-07-27T12:00:00Z",
            "bakeries": [],
            "pantries": [],
            "drivers": [{
                "id": 9,
                "active": True,
                "lastLatitude": 42.36,
                "lastLongitude": -71.06,
                "lastLocationAt": "2026-07-27T11:59:00Z",
            }],
            "pantryWindowConfirmations": [{
                "recipientId": 4,
                "actionKey": "pantry-open:4:one-time:20",
                "acknowledgedAt": "2026-07-27T12:01:00Z",
            }],
        })

        self.assertIsNotNone(snapshot.drivers[0].location())
        self.assertEqual(snapshot.pantry_window_confirmations[0]["recipientId"], 4)


if __name__ == "__main__":
    unittest.main()
