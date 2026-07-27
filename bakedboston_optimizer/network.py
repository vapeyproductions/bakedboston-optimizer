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
        "readyTime": item.get("readyTime", ""),
        "pickupDeadline": item.get("pickupDeadline", ""),
    })


def _pantry(item: dict[str, Any]) -> OrganizationRecord:
    return OrganizationRecord(**_shared(item), schedule={
        "recurringDays": item.get("recurringDays", ""),
        "openTime": item.get("openTime", ""),
        "closeTime": item.get("closeTime", ""),
        "latestPermittedArrival": item.get("latestPermittedArrival", ""),
        "deliveriesSevenDays": item.get("deliveriesSevenDays", 0),
    })
