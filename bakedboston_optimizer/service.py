from __future__ import annotations

import os
import json
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

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
            "pickupId": int(route.bakery_id),
            "bakeryName": route.bakery_name,
            "pantryId": int(route.pantry_id.split(":", 1)[0]),
            "pantryName": route.pantry_name,
            "pantryServiceMode": _service_mode(snapshot, route.pantry_id),
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


def _service_mode(snapshot: NetworkSnapshot, pantry_window_id: str) -> str:
    parts = pantry_window_id.split(":")
    if len(parts) > 2 and parts[1] == "recurring":
        return parts[-1] if parts[-1] in {"staffed", "unattended"} else "staffed"
    _, _, window_id = pantry_window_id.partition(":")
    for window in snapshot.availability_windows:
        if str(window.get("id")) == window_id:
            return str(window.get("serviceMode") or "staffed")
    return "staffed"


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
    for pantry in pantries.values():
        results.extend(_recurring_pantry_windows(pantry, earliest, latest))
    return results


def _recurring_pantry_windows(record: Any, earliest: datetime, latest: datetime) -> list[Pantry]:
    try:
        opens = json.loads(record.schedule.get("openTime") or "[]")
        closes = json.loads(record.schedule.get("closeTime") or "[]")
        arrivals = json.loads(record.schedule.get("latestPermittedArrival") or "[]")
        modes = json.loads(record.schedule.get("serviceModes") or "[]")
    except (TypeError, json.JSONDecodeError):
        return []
    if not isinstance(opens, list) or not isinstance(closes, list) or not isinstance(arrivals, list):
        return []
    eastern = ZoneInfo("America/New_York")
    first = earliest.astimezone(eastern).date()
    last = latest.astimezone(eastern).date() + timedelta(days=1)
    deliveries = float(record.schedule.get("deliveriesSevenDays", 0) or 0)
    results: list[Pantry] = []
    current = first
    while current <= last:
        day = current.strftime("%a")
        for index, opening in enumerate(opens):
            if opening.get("day") != day or index >= len(closes) or index >= len(arrivals):
                continue
            if opening.get("recurrence") == "monthly" and opening.get("ordinal") != (current.day - 1) // 7 + 1:
                continue
            starts = _local_datetime(current, opening.get("time"), eastern)
            ends = _local_datetime(current, closes[index].get("time"), eastern)
            latest_arrival = _local_datetime(current, arrivals[index].get("time"), eastern)
            if not starts or not ends or not latest_arrival or ends < earliest or starts > latest:
                continue
            mode = modes[index].get("mode", "staffed") if index < len(modes) and isinstance(modes[index], dict) else "staffed"
            results.append(Pantry(
                id=f"{record.id}:recurring:{current.isoformat()}:{index}:{mode}",
                pantry_name=record.name,
                location=record.location(),
                receiving_start=starts,
                receiving_end=ends,
                latest_permitted_arrival=latest_arrival,
                priority_score=1 / (1 + deliveries),
            ))
        current += timedelta(days=1)
    return results


def _local_datetime(day: date, value: Any, zone: ZoneInfo) -> datetime | None:
    try:
        parsed = time.fromisoformat(str(value))
    except ValueError:
        return None
    return datetime.combine(day, parsed, tzinfo=zone)


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
