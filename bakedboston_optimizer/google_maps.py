from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal
from urllib.request import Request, urlopen

from .models import AddressValidationStatus, Location


class GoogleMapsProvider:
    """Server-side Google address validation and traffic-aware travel provider."""

    address_validation_url = "https://addressvalidation.googleapis.com/v1:validateAddress"
    compute_routes_url = "https://routes.googleapis.com/directions/v2:computeRoutes"

    def __init__(self, api_key: str) -> None:
        if not api_key:
            raise ValueError("A Google Maps API key is required")
        self.api_key = api_key

    def validate_address(self, address: str, region_code: str = "US") -> Location:
        payload = {
            "address": {
                "regionCode": region_code,
                "addressLines": [address],
            }
        }
        response = self._post(
            f"{self.address_validation_url}?key={self.api_key}",
            payload,
            {"Content-Type": "application/json"},
        )
        result = response["result"]
        verdict = result.get("verdict", {})
        postal_address = result["address"]
        geocode = result["geocode"]
        coordinates = geocode["location"]
        complete = verdict.get("addressComplete", False) and not verdict.get("hasUnconfirmedComponents", False)
        return Location(
            address_entered=address,
            formatted_address=postal_address["formattedAddress"],
            latitude=float(coordinates["latitude"]),
            longitude=float(coordinates["longitude"]),
            google_place_id=geocode.get("placeId", ""),
            validation_status=AddressValidationStatus.VALIDATED if complete else AddressValidationStatus.NEEDS_REVIEW,
        )

    def duration_minutes(
        self,
        origin: Location,
        destination: Location,
        departure_at: datetime,
    ) -> float:
        if departure_at.tzinfo is None:
            raise ValueError("departure_at must include a timezone")
        payload = {
            "origin": _waypoint(origin),
            "destination": _waypoint(destination),
            "travelMode": "DRIVE",
            "routingPreference": "TRAFFIC_AWARE_OPTIMAL",
            "departureTime": departure_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        }
        response = self._post(
            self.compute_routes_url,
            payload,
            {
                "Content-Type": "application/json",
                "X-Goog-Api-Key": self.api_key,
                "X-Goog-FieldMask": "routes.duration",
            },
        )
        routes = response.get("routes", [])
        if not routes:
            raise RuntimeError("Google Routes returned no driving route")
        return _duration_minutes(routes[0]["duration"])

    @staticmethod
    def _post(url: str, payload: dict[str, object], headers: dict[str, str]) -> dict[str, object]:
        request = Request(url, data=json.dumps(payload).encode(), headers=headers, method="POST")
        with urlopen(request, timeout=20) as response:
            return json.loads(response.read())


def _waypoint(location: Location) -> dict[str, object]:
    return {
        "location": {
            "latLng": {
                "latitude": location.latitude,
                "longitude": location.longitude,
            }
        }
    }


def _duration_minutes(value: str) -> float:
    if not value.endswith("s"):
        raise ValueError(f"Unexpected Google duration: {value}")
    return float(Decimal(value[:-1]) / Decimal(60))
