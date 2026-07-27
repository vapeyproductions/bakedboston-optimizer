from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
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
    earliest_start: datetime
    latest_finish: datetime
    start_location: Location
    preferred_destination: Location | None = None


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
