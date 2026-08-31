from __future__ import annotations

import json
import unittest
from pathlib import Path

from bakedboston_optimizer.network import parse_snapshot


class NetworkTests(unittest.TestCase):
    def test_academic_pantries_have_unique_two_pathway_waste_allocations(self) -> None:
        payload = json.loads(
            (Path(__file__).resolve().parents[1] / "data" / "academic_comparison_snapshot.json").read_text()
        )
        snapshot = parse_snapshot(payload)
        allocations = [pantry.waste_allocation for pantry in snapshot.pantries]

        self.assertEqual(len({item.landfill for item in allocations}), len(allocations))
        for allocation in allocations:
            self.assertAlmostEqual(allocation.landfill + allocation.pig_farm, 1.0)
            self.assertEqual(allocation.compost, 0.0)

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

    def test_academic_food_parameters_are_parsed(self) -> None:
        snapshot = parse_snapshot({
            "schemaVersion": 2,
            "generatedAt": "2026-07-27T12:00:00Z",
            "bakeries": [{
                "id": 1,
                "name": "Bakery",
                "address": "1 Main St, Boston, MA 02110",
                "postalCode": "02110",
                "latitude": 42.35,
                "longitude": -71.06,
                "foodAmountDistributionKg": {"minimum": 10, "mode": 15, "maximum": 20},
                "usableFractionDistribution": {"minimum": 0.7, "mode": 0.8, "maximum": 0.9},
                "wasteAllocation": {"landfill": 0.4, "pigFarm": 0.35, "compost": 0.25},
            }],
            "pantries": [{
                "id": 2,
                "name": "Pantry",
                "address": "2 Main St, Boston, MA 02111",
                "latitude": 42.36,
                "longitude": -71.05,
                "distributionFraction": 0.76,
                "wasteAllocation": {"landfill": 0.35, "pigFarm": 0.65, "compost": 0.0},
            }],
        })

        bakery = snapshot.bakeries[0]
        pantry = snapshot.pantries[0]
        self.assertEqual(bakery.postal_code, "02110")
        self.assertEqual(bakery.food_amount_distribution.mode, 15)
        self.assertEqual(bakery.usable_fraction_distribution.mode, 0.8)
        self.assertEqual(bakery.waste_allocation.pig_farm, 0.35)
        self.assertEqual(pantry.pantry_distribution_fraction, 0.76)
        self.assertEqual(pantry.waste_allocation.landfill, 0.35)
        self.assertEqual(pantry.waste_allocation.pig_farm, 0.65)
        self.assertEqual(pantry.waste_allocation.compost, 0.0)


if __name__ == "__main__":
    unittest.main()
