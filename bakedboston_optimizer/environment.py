from __future__ import annotations

from dataclasses import dataclass

from .models import BakeryPickup, DisposalPathway


@dataclass(frozen=True)
class EnvironmentalAssumptions:
    """Transparent lifecycle assumptions for the academic simulation.

    The values are scenario parameters, not measured bakery-product emissions.
    Their structure follows Guo et al. (2026): environmental performance depends
    on usable donated food, the avoided disposal pathway, redistribution waste,
    and transportation.  Coefficients are configurable for sensitivity tests.
    """

    avoided_production_kg_co2e_per_usable_kg: float = 0.42
    avoided_landfill_kg_co2e_per_diverted_kg: float = 0.16
    avoided_compost_kg_co2e_per_diverted_kg: float = 0.04
    redistribution_waste_kg_co2e_per_kg: float = 0.20
    vehicle_kg_co2e_per_mile: float = 0.32

    def __post_init__(self) -> None:
        for name, value in self.__dict__.items():
            if value < 0:
                raise ValueError(f"{name} cannot be negative")


@dataclass(frozen=True)
class EnvironmentalImpact:
    estimated_food_kg: float
    usable_food_kg: float
    residual_waste_kg: float
    avoided_production_kg_co2e: float
    avoided_disposal_kg_co2e: float
    avoided_system_kg_co2e: float
    transport_kg_co2e: float
    residual_waste_kg_co2e: float
    net_environmental_benefit_kg_co2e: float

    @property
    def net_food_saving_benefit_kg_co2e(self) -> float:
        """Food-system benefit before transportation is considered."""

        return self.avoided_system_kg_co2e - self.residual_waste_kg_co2e


DEFAULT_ENVIRONMENTAL_ASSUMPTIONS = EnvironmentalAssumptions()


def estimate_route_environmental_impact(
    pickup: BakeryPickup,
    route_distance_miles: float,
    assumptions: EnvironmentalAssumptions = DEFAULT_ENVIRONMENTAL_ASSUMPTIONS,
) -> EnvironmentalImpact:
    """Estimate net lifecycle CO2e benefit for one feasible delivery route.

    Positive values represent estimated avoided emissions. The calculation first
    nets avoided production and donor disposal against residual redistribution
    waste to obtain the food-saving benefit. It then subtracts route
    transportation emissions in the same unit (kg CO2e):

        net environmental benefit = net food-saving benefit - transport emissions

    This is a signed term, not a threshold rule. Equal values cancel to zero;
    food-saving benefits larger than transport are rewarded, while routes whose
    transport emissions exceed their food-saving benefits are penalized.
    """

    if route_distance_miles < 0:
        raise ValueError("route_distance_miles cannot be negative")

    food_kg = pickup.estimated_food_kg
    usable_food_kg = food_kg * pickup.usable_fraction
    residual_waste_kg = max(0.0, food_kg - usable_food_kg)
    avoided_production = (
        usable_food_kg * assumptions.avoided_production_kg_co2e_per_usable_kg
    )
    avoided_disposal_rate = (
        assumptions.avoided_landfill_kg_co2e_per_diverted_kg
        if pickup.donor_disposal_baseline == DisposalPathway.LANDFILL
        else assumptions.avoided_compost_kg_co2e_per_diverted_kg
    )
    avoided_disposal = food_kg * avoided_disposal_rate
    avoided_system = avoided_production + avoided_disposal
    transport = route_distance_miles * assumptions.vehicle_kg_co2e_per_mile
    residual_waste = (
        residual_waste_kg * assumptions.redistribution_waste_kg_co2e_per_kg
    )
    net_food_saving_benefit = avoided_system - residual_waste
    net_benefit = net_food_saving_benefit - transport

    return EnvironmentalImpact(
        estimated_food_kg=food_kg,
        usable_food_kg=usable_food_kg,
        residual_waste_kg=residual_waste_kg,
        avoided_production_kg_co2e=avoided_production,
        avoided_disposal_kg_co2e=avoided_disposal,
        avoided_system_kg_co2e=avoided_system,
        transport_kg_co2e=transport,
        residual_waste_kg_co2e=residual_waste,
        net_environmental_benefit_kg_co2e=net_benefit,
    )
