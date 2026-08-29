from __future__ import annotations

import unittest
from datetime import datetime, timezone

from bakedboston_optimizer.environment import EnvironmentalAssumptions, MILES_TO_KILOMETERS, estimate_route_environmental_impact
from bakedboston_optimizer.models import BakeryPickup, Location, WasteAllocation


class EnvironmentalImpactTests(unittest.TestCase):
    @staticmethod
    def pickup(food: float, usability: float, allocation: WasteAllocation) -> BakeryPickup:
        timestamp = datetime(2026, 8, 28, 12, tzinfo=timezone.utc)
        return BakeryPickup(
            id="bakery-window", bakery_name="Academic bakery",
            location=Location("1 Test Street", "1 Test Street, Boston, MA", 42.36, -71.06),
            ready_at=timestamp, pickup_deadline=timestamp,
            estimated_food_kg=food, usable_fraction=usability,
            waste_allocation=allocation,
        )

    def test_route_food_is_q_times_usability_times_pantry_distribution(self) -> None:
        impact = estimate_route_environmental_impact(
            self.pickup(10, 0.8, WasteAllocation()), 5,
            pantry_distribution_fraction=0.75,
        )
        expected_transport = 0.41947 * 0.01 * (5 * MILES_TO_KILOMETERS)
        self.assertAlmostEqual(impact.food_saved_kg, 6.0)
        self.assertAlmostEqual(impact.collected_not_distributed_kg, 4.0)
        self.assertAlmostEqual(impact.counterfactual_waste_kg_co2e, 3.6)
        self.assertAlmostEqual(impact.route_waste_kg_co2e, 1.44)
        self.assertAlmostEqual(impact.avoided_waste_kg_co2e, 2.16)
        self.assertAlmostEqual(impact.avoided_production_kg_co2e, 0.0)
        self.assertAlmostEqual(impact.transport_kg_co2e, expected_transport)
        self.assertAlmostEqual(impact.net_environmental_benefit_kg_co2e, 2.16 - expected_transport)

    def test_fixed_waste_mix_changes_counterfactual_value(self) -> None:
        landfill = estimate_route_environmental_impact(self.pickup(10, 0.8, WasteAllocation()), 5, pantry_distribution_fraction=0.75)
        compost = estimate_route_environmental_impact(self.pickup(10, 0.8, WasteAllocation(landfill=0, compost=1)), 5, pantry_distribution_fraction=0.75)
        self.assertGreater(landfill.net_environmental_benefit_kg_co2e, compost.net_environmental_benefit_kg_co2e)

    def test_long_route_can_have_negative_net_environmental_benefit(self) -> None:
        impact = estimate_route_environmental_impact(self.pickup(5, 0.65, WasteAllocation(landfill=0, compost=1)), 30, pantry_distribution_fraction=0.5)
        self.assertLess(impact.net_environmental_benefit_kg_co2e, 0)

    def test_equal_avoided_waste_and_transport_cancel(self) -> None:
        coefficient = 5.0 / (0.01 * 5 * MILES_TO_KILOMETERS)
        assumptions = EnvironmentalAssumptions(
            landfill_kg_co2e_per_kg_waste=1.0,
            pig_farm_kg_co2e_per_kg_waste=0.0,
            compost_kg_co2e_per_kg_waste=0.0,
            transport_kg_co2e_per_tonne_km=coefficient,
            avoided_production_kg_co2e_per_kg_food=0.0,
        )
        impact = estimate_route_environmental_impact(self.pickup(10, 0.5, WasteAllocation()), 5, assumptions, pantry_distribution_fraction=1.0)
        self.assertAlmostEqual(impact.avoided_waste_kg_co2e, 5.0)
        self.assertAlmostEqual(impact.transport_kg_co2e, 5.0)
        self.assertAlmostEqual(impact.net_environmental_benefit_kg_co2e, 0.0)


if __name__ == "__main__":
    unittest.main()
