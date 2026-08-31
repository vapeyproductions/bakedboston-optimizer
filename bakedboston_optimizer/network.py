from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from urllib.request import Request, urlopen

from .google_maps import GoogleMapsProvider
from .models import (
    AddressValidationStatus,
    Location,
    TriangularDistribution,
    WasteAllocation,
)


@dataclass(frozen=True)
class OrganizationRecord:
    id: int
    name: str
    address: str
    formatted_address: str
    google_place_id: str
    address_validation_status: str
    latitude: float | None
    longitude: float | None
    schedule: dict[str, Any]
    schedule_source_url: str = ""
    schedule_verified_at: str = ""
    postal_code: str = ""
    food_amount_distribution: TriangularDistribution | None = None
    usable_fraction_distribution: TriangularDistribution | None = None
    waste_allocation: WasteAllocation | None = None
    pantry_distribution_fraction: float | None = None

    @property
    def optimization_eligible(self) -> bool:
        """Only administrator-confirmed Google locations may enter route models."""
        return (
            self.address_validation_status == AddressValidationStatus.VALIDATED.value
            and self.latitude is not None
            and self.longitude is not None
            and bool(self.google_place_id)
        )

    def location(self, google: GoogleMapsProvider | None = None) -> Location:
        if self.latitude is not None and self.longitude is not None:
            try:
                status = AddressValidationStatus(self.address_validation_status)
            except ValueError:
                status = AddressValidationStatus.UNVALIDATED
            return Location(
                address_entered=self.address,
                formatted_address=self.formatted_address or self.address,
                latitude=self.latitude,
                longitude=self.longitude,
                google_place_id=self.google_place_id,
                validation_status=status,
                postal_code=(
                    self.postal_code
                    or _postal_code(self.formatted_address or self.address)
                ),
            )
        if google is None:
            raise ValueError(f"{self.name} needs geocoding before it can be optimized")
        return google.validate_address(self.address)


def _postal_code(value: str) -> str:
    """Extract a five-digit US ZIP code from a validated address."""

    match = re.search(r"\b(\d{5})(?:-\d{4})?\b", value)
    return match.group(1) if match else ""


@dataclass(frozen=True)
class DriverRecord:
    id: int
    active: bool
    latitude: float | None
    longitude: float | None
    last_location_at: datetime | None

    def location(self) -> Location | None:
        if self.latitude is None or self.longitude is None:
            return None
        return Location(
            address_entered=f"driver-{self.id}",
            formatted_address="Current driver location",
            latitude=self.latitude,
            longitude=self.longitude,
            validation_status=AddressValidationStatus.VALIDATED,
        )


@dataclass(frozen=True)
class NetworkSnapshot:
    schema_version: int
    generated_at: datetime
    bakeries: tuple[OrganizationRecord, ...]
    pantries: tuple[OrganizationRecord, ...]
    availability_windows: tuple[dict[str, Any], ...]
    availability_pauses: tuple[dict[str, Any], ...]
    schedule_exceptions: tuple[dict[str, Any], ...]
    routes: tuple[dict[str, Any], ...]
    pickup_occurrences: tuple[dict[str, Any], ...]
    ride_requests: tuple[dict[str, Any], ...]
    route_offers: tuple[dict[str, Any], ...]
    drivers: tuple[DriverRecord, ...] = ()
    pantry_window_confirmations: tuple[dict[str, Any], ...] = ()

    @property
    def eligible_bakeries(self) -> tuple[OrganizationRecord, ...]:
        return tuple(organization for organization in self.bakeries if organization.optimization_eligible)

    @property
    def eligible_pantries(self) -> tuple[OrganizationRecord, ...]:
        return tuple(organization for organization in self.pantries if organization.optimization_eligible)


class BakedBostonNetworkClient:
    """Read-only client for the private BakedBoston operational feed."""

    def __init__(self, base_url: str, api_key: str) -> None:
        if not base_url.startswith("https://"):
            raise ValueError("The network feed must use HTTPS")
        if not api_key:
            raise ValueError("An optimizer API key is required")
        self.url = f"{base_url.rstrip('/')}/api/optimizer/network"
        self.api_key = api_key

    def fetch(self) -> NetworkSnapshot:
        request = Request(
            self.url,
            headers={"Authorization": f"Bearer {self.api_key}", "Accept": "application/json"},
        )
        with urlopen(request, timeout=20) as response:
            payload = json.loads(response.read())
        return parse_snapshot(payload)

    def save_validated_location(self, organization_type: str, organization_id: int, location: Location) -> None:
        if organization_type not in {"bakery", "pantry"}:
            raise ValueError("organization_type must be bakery or pantry")
        payload = {
            "organizationType": organization_type,
            "organizationId": organization_id,
            "formattedAddress": location.formatted_address,
            "googlePlaceId": location.google_place_id,
            "latitude": location.latitude,
            "longitude": location.longitude,
            "addressValidationStatus": location.validation_status.value,
        }
        request = Request(
            f"{self.url.rsplit('/', 1)[0]}/locations",
            data=json.dumps(payload).encode(),
            method="PATCH",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )
        with urlopen(request, timeout=20) as response:
            response.read()


def parse_snapshot(payload: dict[str, Any]) -> NetworkSnapshot:
    if payload.get("schemaVersion") not in {1, 2}:
        raise ValueError(f"Unsupported optimizer feed schema: {payload.get('schemaVersion')}")
    return NetworkSnapshot(
        schema_version=int(payload["schemaVersion"]),
        generated_at=datetime.fromisoformat(payload["generatedAt"].replace("Z", "+00:00")),
        bakeries=tuple(_bakery(item) for item in payload.get("bakeries", [])),
        pantries=tuple(_pantry(item) for item in payload.get("pantries", [])),
        availability_windows=tuple(payload.get("availabilityWindows", [])),
        availability_pauses=tuple(payload.get("availabilityPauses", [])),
        schedule_exceptions=tuple(payload.get("scheduleExceptions", [])),
        routes=tuple(payload.get("routes", [])),
        pickup_occurrences=tuple(payload.get("pickupOccurrences", [])),
        ride_requests=tuple(payload.get("rideRequests", [])),
        route_offers=tuple(payload.get("routeOffers", [])),
        drivers=tuple(_driver(item) for item in payload.get("drivers", [])),
        pantry_window_confirmations=tuple(payload.get("pantryWindowConfirmations", [])),
    )


def _shared(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": int(item["id"]),
        "name": str(item["name"]),
        "address": str(item["address"]),
        "formatted_address": str(item.get("formattedAddress") or ""),
        "google_place_id": str(item.get("googlePlaceId") or ""),
        "address_validation_status": str(item.get("addressValidationStatus") or "unvalidated"),
        "latitude": float(item["latitude"]) if item.get("latitude") is not None else None,
        "longitude": float(item["longitude"]) if item.get("longitude") is not None else None,
        "schedule_source_url": str(item.get("scheduleSourceUrl") or ""),
        "schedule_verified_at": str(item.get("scheduleVerifiedAt") or ""),
        "postal_code": str(item.get("postalCode") or ""),
    }


def _bakery(item: dict[str, Any]) -> OrganizationRecord:
    return OrganizationRecord(
        **_shared(item),
        schedule={
            "recurringDays": item.get("recurringDays", ""),
            "businessOpenTime": item.get("businessOpenTime", "{}"),
            "businessCloseTime": item.get("businessCloseTime", "{}"),
            "readyTime": item.get("readyTime", ""),
            "pickupDeadline": item.get("pickupDeadline", ""),
        },
        food_amount_distribution=_triangular_distribution(
            item.get("foodAmountDistributionKg")
        ),
        usable_fraction_distribution=_triangular_distribution(
            item.get("usableFractionDistribution")
        ),
        waste_allocation=_waste_allocation(item.get("wasteAllocation")),
    )


def _pantry(item: dict[str, Any]) -> OrganizationRecord:
    distribution_value = item.get("distributionFraction")
    distribution_fraction = (
        float(distribution_value) if distribution_value is not None else None
    )
    if distribution_fraction is not None and not 0 <= distribution_fraction <= 1:
        raise ValueError("distributionFraction must be between 0 and 1")
    return OrganizationRecord(
        **_shared(item),
        schedule={
            "recurringDays": item.get("recurringDays", ""),
            "openTime": item.get("openTime", ""),
            "closeTime": item.get("closeTime", ""),
            "latestPermittedArrival": item.get("latestPermittedArrival", ""),
            "serviceModes": item.get("serviceModes", "[]"),
            "deliveriesSevenDays": item.get("deliveriesSevenDays", 0),
        },
        waste_allocation=_pantry_waste_allocation(item.get("wasteAllocation")),
        pantry_distribution_fraction=distribution_fraction,
    )


def _triangular_distribution(value: object) -> TriangularDistribution | None:
    if value is None:
        return None
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as error:
            raise ValueError("invalid triangular distribution JSON") from error
    if not isinstance(value, dict):
        raise ValueError("triangular distribution must be an object")
    return TriangularDistribution(
        minimum=float(value["minimum"]),
        mode=float(value["mode"]),
        maximum=float(value["maximum"]),
    )


def _waste_allocation(value: object) -> WasteAllocation | None:
    if value is None:
        return None
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as error:
            raise ValueError("invalid waste allocation JSON") from error
    if not isinstance(value, dict):
        raise ValueError("waste allocation must be an object")
    return WasteAllocation(
        landfill=float(value.get("landfill", 0.0)),
        pig_farm=float(value.get("pigFarm", 0.0)),
        compost=float(value.get("compost", 0.0)),
    )


def _pantry_waste_allocation(value: object) -> WasteAllocation | None:
    allocation = _waste_allocation(value)
    if allocation is not None and abs(allocation.compost) > 1e-9:
        raise ValueError("pantry waste allocation cannot include compost")
    return allocation


def _driver(item: dict[str, Any]) -> DriverRecord:
    location_value = item.get("lastLocationAt")
    return DriverRecord(
        id=int(item["id"]),
        active=bool(item.get("active", True)),
        latitude=float(item["lastLatitude"]) if item.get("lastLatitude") is not None else None,
        longitude=float(item["lastLongitude"]) if item.get("lastLongitude") is not None else None,
        last_location_at=(
            datetime.fromisoformat(str(location_value).replace("Z", "+00:00"))
            if location_value else None
        ),
    )
