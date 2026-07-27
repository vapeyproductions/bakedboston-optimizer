from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from .models import AddressValidationStatus, BakeryPickup, DriverRequest, Location, Pantry
from .optimizer import rank_routes
from .travel import HaversineTravelTimeProvider


def location(address: str, latitude: float, longitude: float) -> Location:
    return Location(
        address_entered=address,
        formatted_address=address,
        latitude=latitude,
        longitude=longitude,
        validation_status=AddressValidationStatus.VALIDATED,
    )


def main() -> None:
    timezone = ZoneInfo("America/New_York")
    day = datetime(2026, 7, 27, tzinfo=timezone)
    bakery = BakeryPickup(
        id="bakery-1",
        bakery_name="Demonstration Bakery",
        location=location("1 Sample Street, Boston, MA", 42.3522, -71.0552),
        ready_at=day.replace(hour=17),
        pickup_deadline=day.replace(hour=18),
    )
    pantry = Pantry(
        id="pantry-1",
        pantry_name="Demonstration Pantry",
        location=location("2 Example Avenue, Boston, MA", 42.3248, -71.0846),
        receiving_start=day.replace(hour=17),
        receiving_end=day.replace(hour=19),
        latest_permitted_arrival=day.replace(hour=18, minute=45),
        priority_score=0.8,
    )
    request = DriverRequest(
        earliest_start=day.replace(hour=16, minute=45),
        latest_finish=day.replace(hour=19),
        start_location=location("Driver starting area", 42.3601, -71.0589),
        preferred_destination=location("Driver destination area", 42.3105, -71.1070),
    )
    routes = rank_routes([bakery], [pantry], request, HaversineTravelTimeProvider())
    for index, route in enumerate(routes, start=1):
        print(f"{index}. {route.bakery_name} -> {route.pantry_name}")
        print(f"   score={route.score:.2f}, finish={route.finish_at:%-I:%M %p}")
        print(f"   {'; '.join(route.explanation)}")


if __name__ == "__main__":
    main()
