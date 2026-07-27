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
        })
        bakery = snapshot.bakeries[0]
        self.assertEqual(bakery.location().formatted_address, "1 Main St, Boston, MA 02110, USA")
        self.assertEqual(bakery.schedule["readyTime"], "{}")

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


if __name__ == "__main__":
    unittest.main()
