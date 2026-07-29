from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from urllib.request import Request, urlopen

from .google_maps import GoogleMapsProvider
from .models import AddressValidationStatus, Location


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
            )
        if google is None:
            raise ValueError(f"{self.name} needs geocoding before it can be optimized")
        return google.validate_address(self.address)


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
    if payload.get("schemaVersion") != 1:
        raise ValueError(f"Unsupported optimizer feed schema: {payload.get('schemaVersion')}")
    return NetworkSnapshot(
        schema_version=1,
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
    }


def _bakery(item: dict[str, Any]) -> OrganizationRecord:
    return OrganizationRecord(**_shared(item), schedule={
        "recurringDays": item.get("recurringDays", ""),
        "businessOpenTime": item.get("businessOpenTime", "{}"),
        "businessCloseTime": item.get("businessCloseTime", "{}"),
        "readyTime": item.get("readyTime", ""),
        "pickupDeadline": item.get("pickupDeadline", ""),
    })


def _pantry(item: dict[str, Any]) -> OrganizationRecord:
    return OrganizationRecord(**_shared(item), schedule={
        "recurringDays": item.get("recurringDays", ""),
        "openTime": item.get("openTime", ""),
        "closeTime": item.get("closeTime", ""),
        "latestPermittedArrival": item.get("latestPermittedArrival", ""),
        "serviceModes": item.get("serviceModes", "[]"),
        "deliveriesSevenDays": item.get("deliveriesSevenDays", 0),
    })
