from __future__ import annotations

from datetime import datetime
from math import asin, cos, radians, sin, sqrt
from typing import Protocol

from .models import Location


class TravelTimeProvider(Protocol):
    """Travel-time boundary that Google Routes can implement later."""

    def duration_minutes(
        self,
        origin: Location,
        destination: Location,
        departure_at: datetime,
    ) -> float:
        ...


class HaversineTravelTimeProvider:
    """Deterministic fallback for tests and offline hackathon demonstrations."""

    def __init__(self, average_speed_mph: float = 22.0, road_factor: float = 1.25) -> None:
        if average_speed_mph <= 0 or road_factor < 1:
            raise ValueError("Travel assumptions must be positive")
        self.average_speed_mph = average_speed_mph
        self.road_factor = road_factor

    def duration_minutes(
        self,
        origin: Location,
        destination: Location,
        departure_at: datetime,
    ) -> float:
        del departure_at
        miles = _haversine_miles(
            origin.latitude,
            origin.longitude,
            destination.latitude,
            destination.longitude,
        )
        return miles * self.road_factor / self.average_speed_mph * 60


def _haversine_miles(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    earth_radius_miles = 3958.8
    lat_delta = radians(lat2 - lat1)
    lon_delta = radians(lon2 - lon1)
    a = sin(lat_delta / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(lon_delta / 2) ** 2
    return 2 * earth_radius_miles * asin(sqrt(a))
