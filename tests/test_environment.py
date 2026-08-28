from __future__ import annotations

import unittest
from datetime import datetime, timezone

from bakedboston_optimizer.environment import estimate_route_environmental_impact
from bakedboston_optimizer.models import BakeryPickup, DisposalPathway, Location


class EnvironmentalImpactTests(unittest.TestCase):
    @staticmethod
    def pickup(
        estimated_food_kg: float,
        usable_fraction: float,
        donor_disposal_baseline: DisposalPathway,
    ) -> BakeryPickup:
        timestamp = datetime(2026, 8, 28, 12, tzinfo=timezone.utc)
        return BakeryPickup(
            id="bakery-window",
            bakery_name="Academic bakery",
            location=Location(
                address_entered="1 Test Street",
                formatted_address="1 Test Street, Boston, MA",
                latitude=42.36,
                longitude=-71.06,
            ),
            ready_at=timestamp,
            pickup_deadline=timestamp,
            estimated_food_kg=estimated_food_kg,
            usable_fraction=usable_fraction,
            donor_disposal_baseline=donor_disposal_baseline,
        )

    def test_landfill_counterfactual_lifecycle_ledger(self) -> None:
        impact = estimate_route_environmental_impact(
            self.pickup(10, 0.8, DisposalPathway.LANDFILL),
            route_distance_miles=5,
        )

        self.assertAlmostEqual(impact.usable_food_kg, 8.0)
        self.assertAlmostEqual(impact.residual_waste_kg, 2.0)
        self.assertAlmostEqual(impact.avoided_production_kg_co2e, 3.36)
        self.assertAlmostEqual(impact.avoided_disposal_kg_co2e, 1.60)
        self.assertAlmostEqual(impact.avoided_system_kg_co2e, 4.96)
        self.assertAlmostEqual(impact.transport_kg_co2e, 1.60)
        self.assertAlmostEqual(impact.residual_waste_kg_co2e, 0.40)
        self.assertAlmostEqual(impact.net_environmental_benefit_kg_co2e, 2.96)

    def test_compost_counterfactual_has_smaller_avoided_disposal_credit(self) -> None:
        landfill = estimate_route_environmental_impact(
            self.pickup(10, 0.8, DisposalPathway.LANDFILL),
            route_distance_miles=5,
        )
        compost = estimate_route_environmental_impact(
            self.pickup(10, 0.8, DisposalPathway.COMPOST),
            route_distance_miles=5,
        )

        self.assertAlmostEqual(compost.net_environmental_benefit_kg_co2e, 1.76)
        self.assertGreater(landfill.net_environmental_benefit_kg_co2e, compost.net_environmental_benefit_kg_co2e)

    def test_long_route_can_have_negative_net_environmental_benefit(self) -> None:
        impact = estimate_route_environmental_impact(
            self.pickup(5, 0.65, DisposalPathway.COMPOST),
            route_distance_miles=30,
        )

        self.assertLess(impact.net_environmental_benefit_kg_co2e, 0)


if __name__ == "__main__":
    unittest.main()
