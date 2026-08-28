from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum


class AddressValidationStatus(StrEnum):
    UNVALIDATED = "unvalidated"
    VALIDATED = "validated"
    NEEDS_REVIEW = "needs_review"


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


@dataclass(frozen=True)
class Pantry:
    id: str
    pantry_name: str
    location: Location
    receiving_start: datetime
    receiving_end: datetime
    latest_permitted_arrival: datetime
    priority_score: float

    def __post_init__(self) -> None:
        if not 0 <= self.priority_score <= 1:
            raise ValueError("priority_score must be between 0 and 1")


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


@dataclass(frozen=True)
class NetworkOptimizationResult:
    assignments: tuple[AssignmentCandidate, ...]
    diagnostics: SolverDiagnostics
