from __future__ import annotations

from dataclasses import dataclass

from .models import BakeryPickup


MILES_TO_KILOMETERS = 1.60934


@dataclass(frozen=True)
class EnvironmentalAssumptions:
    """Fixed food-waste and freight coefficients used by the academic model."""

    landfill_kg_co2e_per_kg_waste: float = 0.36
    pig_farm_kg_co2e_per_kg_waste: float = -0.12
    compost_kg_co2e_per_kg_waste: float = 0.00581
    transport_kg_co2e_per_tonne_km: float = 0.41947
    avoided_production_kg_co2e_per_kg_food: float = 0.38
    production_substitution_fraction: float = 0.0

    def __post_init__(self) -> None:
        for name in (
            "landfill_kg_co2e_per_kg_waste",
            "compost_kg_co2e_per_kg_waste",
            "transport_kg_co2e_per_tonne_km",
            "avoided_production_kg_co2e_per_kg_food",
        ):
            if getattr(self, name) < 0:
                raise ValueError(f"{name} cannot be negative")
        if not 0 <= self.production_substitution_fraction <= 1:
            raise ValueError("production_substitution_fraction must be between 0 and 1")


@dataclass(frozen=True)
class EnvironmentalImpact:
    bakery_food_kg: float
    bakery_usable_fraction: float
    pantry_distribution_fraction: float
    food_saved_kg: float
    collected_not_distributed_kg: float
    counterfactual_waste_kg_co2e: float
    route_waste_kg_co2e: float
    avoided_waste_kg_co2e: float
    avoided_production_kg_co2e: float
    transport_kg_co2e: float
    net_environmental_benefit_kg_co2e: float

    @property
    def estimated_food_kg(self) -> float:
        return self.bakery_food_kg

    @property
    def usable_food_kg(self) -> float:
        return self.food_saved_kg

    @property
    def residual_waste_kg(self) -> float:
        return self.collected_not_distributed_kg

    @property
    def avoided_disposal_kg_co2e(self) -> float:
        return self.avoided_waste_kg_co2e

    @property
    def avoided_system_kg_co2e(self) -> float:
        return self.avoided_waste_kg_co2e + self.avoided_production_kg_co2e

    @property
    def residual_waste_kg_co2e(self) -> float:
        return self.route_waste_kg_co2e

    @property
    def net_food_saving_benefit_kg_co2e(self) -> float:
        return self.avoided_system_kg_co2e


DEFAULT_ENVIRONMENTAL_ASSUMPTIONS = EnvironmentalAssumptions()


def estimate_route_environmental_impact(
    pickup: BakeryPickup,
    route_distance_miles: float,
    assumptions: EnvironmentalAssumptions = DEFAULT_ENVIRONMENTAL_ASSUMPTIONS,
    *,
    pantry_distribution_fraction: float = 1.0,
) -> EnvironmentalImpact:
    """Estimate food saved and net direct environmental benefit for one route.

    ``food saved = bakery food × bakery usability × pantry distribution``.
    Without a completed route, all bakery food follows its fixed waste mix. With
    a completed route, only food not ultimately distributed follows that mix.
    Transport uses tonne-kilometres and the collected bakery food as cargo mass.
    """

    if route_distance_miles < 0:
        raise ValueError("route_distance_miles cannot be negative")
    if not 0 <= pantry_distribution_fraction <= 1:
        raise ValueError("pantry_distribution_fraction must be between 0 and 1")

    food_kg = pickup.estimated_food_kg
    food_saved_kg = food_kg * pickup.usable_fraction * pantry_distribution_fraction
    collected_not_distributed_kg = max(0.0, food_kg - food_saved_kg)
    allocation = pickup.waste_allocation
    mixed_waste_coefficient = (
        allocation.landfill * assumptions.landfill_kg_co2e_per_kg_waste
        + allocation.pig_farm * assumptions.pig_farm_kg_co2e_per_kg_waste
        + allocation.compost * assumptions.compost_kg_co2e_per_kg_waste
    )
    counterfactual_waste = food_kg * mixed_waste_coefficient
    route_waste = collected_not_distributed_kg * mixed_waste_coefficient
    avoided_waste = counterfactual_waste - route_waste
    avoided_production = (
        food_saved_kg
        * assumptions.avoided_production_kg_co2e_per_kg_food
        * assumptions.production_substitution_fraction
    )
    transport = (
        assumptions.transport_kg_co2e_per_tonne_km
        * (food_kg / 1_000.0)
        * (route_distance_miles * MILES_TO_KILOMETERS)
    )
    net_benefit = avoided_waste + avoided_production - transport

    return EnvironmentalImpact(
        bakery_food_kg=food_kg,
        bakery_usable_fraction=pickup.usable_fraction,
        pantry_distribution_fraction=pantry_distribution_fraction,
        food_saved_kg=food_saved_kg,
        collected_not_distributed_kg=collected_not_distributed_kg,
        counterfactual_waste_kg_co2e=counterfactual_waste,
        route_waste_kg_co2e=route_waste,
        avoided_waste_kg_co2e=avoided_waste,
        avoided_production_kg_co2e=avoided_production,
        transport_kg_co2e=transport,
        net_environmental_benefit_kg_co2e=net_benefit,
    )
