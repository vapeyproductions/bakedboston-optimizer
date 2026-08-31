from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum


class AddressValidationStatus(StrEnum):
    UNVALIDATED = "unvalidated"
    VALIDATED = "validated"
    NEEDS_REVIEW = "needs_review"


class DisposalPathway(StrEnum):
    """Legacy single-pathway values retained for API compatibility."""

    LANDFILL = "landfill"
    PIG_FARM = "pig_farm"
    COMPOST = "compost"


@dataclass(frozen=True)
class TriangularDistribution:
    """Fixed, inspectable parameters for one bounded daily scenario draw."""

    minimum: float
    mode: float
    maximum: float

    def __post_init__(self) -> None:
        if not self.minimum <= self.mode <= self.maximum:
            raise ValueError("triangular distribution requires minimum <= mode <= maximum")


@dataclass(frozen=True)
class WasteAllocation:
    """Fixed fractions of food waste sent to each modeled pathway."""

    landfill: float = 1.0
    pig_farm: float = 0.0
    compost: float = 0.0

    def __post_init__(self) -> None:
        for name, value in self.__dict__.items():
            if not 0 <= value <= 1:
                raise ValueError(f"{name} waste fraction must be between 0 and 1")
        if abs(self.landfill + self.pig_farm + self.compost - 1.0) > 1e-6:
            raise ValueError("waste allocation fractions must sum to 1")


@dataclass(frozen=True)
class Location:
    """An address-first location with a cached computational geocode."""

    address_entered: str
    formatted_address: str
    latitude: float
    longitude: float
    google_place_id: str = ""
    validation_status: AddressValidationStatus = AddressValidationStatus.UNVALIDATED
    postal_code: str = ""


@dataclass(frozen=True)
class BakeryPickup:
    id: str
    bakery_name: str
    location: Location
    ready_at: datetime
    pickup_deadline: datetime
    claimed: bool = False
    # Academic scenario estimates used by the food and direct-CO2e calculation.
    # They are intentionally explicit rather than inferred from route miles.
    estimated_food_kg: float = 20.0
    usable_fraction: float = 0.80
    food_amount_distribution: TriangularDistribution | None = None
    usable_fraction_distribution: TriangularDistribution | None = None
    waste_allocation: WasteAllocation = WasteAllocation()
    # Deprecated single-pathway field retained so older operational payloads
    # can still be constructed. Academic scenarios use ``waste_allocation``.
    donor_disposal_baseline: DisposalPathway = DisposalPathway.LANDFILL

    def __post_init__(self) -> None:
        if self.estimated_food_kg < 0:
            raise ValueError("estimated_food_kg cannot be negative")
        if not 0 <= self.usable_fraction <= 1:
            raise ValueError("usable_fraction must be between 0 and 1")


@dataclass(frozen=True)
class Pantry:
    id: str
    pantry_name: str
    location: Location
    receiving_start: datetime
    receiving_end: datetime
    latest_permitted_arrival: datetime
    priority_score: float
    distribution_fraction: float = 1.0
    waste_allocation: WasteAllocation = WasteAllocation(
        landfill=0.40,
        pig_farm=0.60,
        compost=0.0,
    )
    historical_raw_food_kg: float = 0.0
    historical_saved_food_kg: float = 0.0

    def __post_init__(self) -> None:
        if not 0 <= self.priority_score <= 1:
            raise ValueError("priority_score must be between 0 and 1")
        if not 0 <= self.distribution_fraction <= 1:
            raise ValueError("distribution_fraction must be between 0 and 1")
        if abs(self.waste_allocation.compost) > 1e-9:
            raise ValueError("pantry waste allocation cannot include compost")
        if self.historical_raw_food_kg < 0 or self.historical_saved_food_kg < 0:
            raise ValueError("historical food totals cannot be negative")


@dataclass(frozen=True)
class DriverRequest:
    """A driver's arrival to the marketplace and their soft trip preferences.

    ``earliest_start`` and ``latest_finish`` remain the preferred trip interval
    for API compatibility.  Logging in is an information event, not a forced
    departure: ``logged_at`` is when routes may first be considered, while the
    optimizer is free to choose a later just-in-time departure.  ``search_until``
    is the outer operational horizon; missing the preferred interval is
    penalized rather than automatically making an otherwise useful route
    infeasible.
    """

    earliest_start: datetime
    latest_finish: datetime
    start_location: Location
    preferred_destination: Location | None = None
    id: str = ""
    driver_id: str = ""
    logged_at: datetime | None = None
    search_until: datetime | None = None
    start_radius_miles: float = 2.0
    destination_radius_miles: float = 2.0
    start_zip_code: str = ""
    destination_zip_code: str = ""

    def __post_init__(self) -> None:
        requested_minutes = (self.latest_finish - self.earliest_start).total_seconds() / 60
        if requested_minutes < 30:
            raise ValueError("Driver requested time windows must be at least 30 minutes")
        if self.start_radius_miles < 0 or self.destination_radius_miles < 0:
            raise ValueError("ZIP preference radii cannot be negative")

    @property
    def login_time(self) -> datetime:
        return self.logged_at or self.earliest_start

    @property
    def preferred_start(self) -> datetime:
        return self.earliest_start

    @property
    def preferred_finish(self) -> datetime:
        return self.latest_finish

    @property
    def hard_search_end(self) -> datetime:
        return self.search_until or self.latest_finish + timedelta(minutes=90)


@dataclass(frozen=True)
class RouteCandidate:
    bakery_id: str
    bakery_name: str
    bakery_address: str
    pantry_id: str
    pantry_name: str
    pantry_address: str
    depart_at: datetime
    pickup_at: datetime
    pantry_arrival_at: datetime
    finish_at: datetime
    drive_minutes: float
    waiting_minutes: float
    destination_minutes: float
    pantry_priority: float
    score: float
    explanation: tuple[str, ...]
    facility_waiting_minutes: float = 0.0
    requested_time_deviation_minutes: float = 0.0
    requested_window_minutes: float = 30.0
    requested_time_deviation_ratio: float = 0.0
    within_preferred_window: bool = True
    origin_deviation_miles: float = 0.0
    destination_deviation_miles: float = 0.0
    normalized_origin_deviation: float = 0.0
    normalized_destination_deviation: float = 0.0
    normalized_spatial_deviation: float = 0.0
    route_distance_miles: float = 0.0
    acceptance_probability: float = 1.0
    estimated_food_kg: float = 0.0
    usable_food_kg: float = 0.0
    bakery_usable_fraction: float = 0.0
    pantry_distribution_fraction: float = 0.0
    pantry_historical_raw_food_kg: float = 0.0
    pantry_historical_saved_food_kg: float = 0.0
    food_saved_kg: float = 0.0
    collected_not_distributed_kg: float = 0.0
    bakery_unusable_food_kg: float = 0.0
    pantry_undistributed_food_kg: float = 0.0
    bakery_route_waste_kg_co2e: float = 0.0
    pantry_route_waste_kg_co2e: float = 0.0
    pantry_landfill_fraction: float = 0.0
    pantry_pig_farm_fraction: float = 0.0
    counterfactual_waste_kg_co2e: float = 0.0
    route_waste_kg_co2e: float = 0.0
    avoided_system_kg_co2e: float = 0.0
    transport_kg_co2e: float = 0.0
    residual_waste_kg_co2e: float = 0.0
    net_environmental_benefit_kg_co2e: float = 0.0


@dataclass(frozen=True)
class AssignmentCandidate:
    """A feasible driver-request, bakery-pickup, and pantry assignment."""

    request_id: str
    driver_id: str
    route: RouteCandidate


@dataclass(frozen=True)
class SolverDiagnostics:
    backend: str
    status: str
    candidate_count: int
    matched_count: int
    route_quality: float
    runtime_seconds: float
    mip_gap: float | None = None
    expected_completed_deliveries: float = 0.0
    route_distance_miles: float = 0.0
    estimated_food_kg: float = 0.0
    usable_food_kg: float = 0.0
    food_saved_kg: float = 0.0
    collected_not_distributed_kg: float = 0.0
    avoided_system_kg_co2e: float = 0.0
    transport_kg_co2e: float = 0.0
    residual_waste_kg_co2e: float = 0.0
    net_environmental_benefit_kg_co2e: float = 0.0
    menu_size: int = 0
    menu_driver_count: int = 0
    willing_menu_options: int = 0
    unhappy_driver_count: int = 0
    training_scenario_count: int = 0


@dataclass(frozen=True)
class NetworkOptimizationResult:
    assignments: tuple[AssignmentCandidate, ...]
    diagnostics: SolverDiagnostics
    recommendations: tuple[AssignmentCandidate, ...] = ()
