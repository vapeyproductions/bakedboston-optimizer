from __future__ import annotations

import os
from datetime import datetime
from typing import Any

from .google_maps import GoogleMapsProvider
from .models import AddressValidationStatus, BakeryPickup, DriverRequest, Location, Pantry
from .network import BakedBostonNetworkClient, NetworkSnapshot
from .optimizer import rank_routes
from .travel import HaversineTravelTimeProvider


def recommend(payload: dict[str, Any]) -> dict[str, Any]:
    """Return ranked live recommendations using the same model as the hackathon demo."""

    earliest = _datetime(payload["earliestStart"])
    latest = _datetime(payload["latestFinish"])
    if latest <= earliest:
        raise ValueError("latestFinish must be after earliestStart")
    start = _location("driver-start", payload["startLocation"])
    preferred = _location("preferred-destination", payload["preferredDestination"]) if payload.get("preferredDestination") else None
    snapshot = _network()
    pickups = _pickups(snapshot, earliest, latest)
    pantries = _pantries(snapshot, earliest, latest)
    travel = GoogleMapsProvider(os.environ["GOOGLE_MAPS_API_KEY"]) if os.getenv("GOOGLE_MAPS_API_KEY") else HaversineTravelTimeProvider()
    routes = rank_routes(
        pickups,
        pantries,
        DriverRequest(earliest_start=earliest, latest_finish=latest, start_location=start, preferred_destination=preferred),
        travel,
    )
    return {
        "generatedAt": datetime.now().astimezone().isoformat(),
        "routes": [{
            "pickupId": route.bakery_id,
            "bakeryName": route.bakery_name,
            "pantryId": route.pantry_id.split(":", 1)[0],
            "pantryName": route.pantry_name,
            "departAt": route.depart_at.isoformat(),
            "pickupAt": route.pickup_at.isoformat(),
            "pantryArrivalAt": route.pantry_arrival_at.isoformat(),
            "finishAt": route.finish_at.isoformat(),
            "driveMinutes": route.drive_minutes,
            "waitingMinutes": route.waiting_minutes,
            "pantryPriority": route.pantry_priority,
            "score": route.score,
            "explanation": list(route.explanation),
        } for route in routes[:10]],
    }


def _network() -> NetworkSnapshot:
    return BakedBostonNetworkClient(
        os.environ["BAKEDBOSTON_BASE_URL"],
        os.environ["OPTIMIZER_API_KEY"],
    ).fetch()


def _pickups(snapshot: NetworkSnapshot, earliest: datetime, latest: datetime) -> list[BakeryPickup]:
    bakeries = {record.id: record for record in snapshot.eligible_bakeries}
    claimed = {
        int(offer["pickupOccurrenceId"])
        for offer in snapshot.route_offers
        if offer.get("status") == "accepted"
    }
    result: list[BakeryPickup] = []
    for item in snapshot.pickup_occurrences:
        bakery = bakeries.get(int(item["bakeryId"]))
        if not bakery or item.get("status") != "confirmed":
            continue
        ready = _datetime(item["pickupWindowStartsAt"])
        deadline = _datetime(item["pickupDeadline"])
        if deadline < earliest or ready > latest:
            continue
        result.append(BakeryPickup(
            id=str(item["id"]),
            bakery_name=bakery.name,
            location=bakery.location(),
            ready_at=ready,
            pickup_deadline=deadline,
            claimed=int(item["id"]) in claimed,
        ))
    return result


def _pantries(snapshot: NetworkSnapshot, earliest: datetime, latest: datetime) -> list[Pantry]:
    pantries = {record.id: record for record in snapshot.eligible_pantries}
    results: list[Pantry] = []
    for item in snapshot.availability_windows:
        if item.get("organizationType") != "pantry" or item.get("paused"):
            continue
        pantry = pantries.get(int(item["organizationId"]))
        if not pantry:
            continue
        starts = _datetime(item["startsAt"])
        ends = _datetime(item["endsAt"])
        latest_arrival = _datetime(item["latestArrival"])
        if ends < earliest or starts > latest:
            continue
        deliveries = float(pantry.schedule.get("deliveriesSevenDays", 0) or 0)
        results.append(Pantry(
            id=f"{pantry.id}:{item['id']}",
            pantry_name=pantry.name,
            location=pantry.location(),
            receiving_start=starts,
            receiving_end=ends,
            latest_permitted_arrival=latest_arrival,
            priority_score=1 / (1 + deliveries),
        ))
    return results


def _datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Times must include a timezone")
    return parsed


def _location(identifier: str, value: dict[str, Any]) -> Location:
    return Location(
        address_entered=str(value.get("address") or identifier),
        formatted_address=str(value.get("address") or identifier),
        latitude=float(value["latitude"]),
        longitude=float(value["longitude"]),
        validation_status=AddressValidationStatus.VALIDATED,
    )
