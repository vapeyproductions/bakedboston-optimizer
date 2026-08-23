from __future__ import annotations

import os
import json
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from .google_maps import GoogleMapsProvider
from .models import AddressValidationStatus, BakeryPickup, DriverRequest, Location, Pantry
from .network import BakedBostonNetworkClient, NetworkSnapshot
from .optimizer import active_solver_backend, optimize_network, rank_routes
from .simulation import SimulationConfig, simulate_snapshot
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
        "solver": {
            "backend": active_solver_backend(),
            "status": "optimal" if active_solver_backend() == "gurobi" else "fallback",
            "candidateCount": len(routes),
        },
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


def recommend_network(payload: dict[str, Any]) -> dict[str, Any]:
    """Optimize active driver requests and confirmed pickups as one MIP."""

    snapshot = _network()
    request_rows = [
        item for item in snapshot.ride_requests
        if item.get("status") == "active"
    ]
    if payload.get("serviceDate"):
        request_rows = [
            item for item in request_rows
            if item.get("serviceDate") == payload["serviceDate"]
        ]
    protected_offer_statuses = {"offered", "accepted"}
    protected_request_ids = {
        int(item["driverRequestId"])
        for item in snapshot.route_offers
        if item.get("status") in protected_offer_statuses
    }


def simulate_network(payload: dict[str, Any]) -> dict[str, Any]:
    """Run a reproducible academic simulation without changing live state.

    Institution records are treated as read-only schedule templates. Driver
    availability, bakery surplus, and staffed-pantry attendance are synthetic
    events controlled by a saved random seed.
    """

    if not payload.get("startDate"):
        raise ValueError("startDate is required for a reproducible simulation")
    config = SimulationConfig(
        start_date=date.fromisoformat(str(payload["startDate"])),
        days=_bounded_int(payload.get("days", 7), "days", 1, 90),
        random_seed=int(payload.get("randomSeed", 2026)),
        drivers_per_day=_bounded_int(
            payload.get("driversPerDay", 8), "driversPerDay", 1, 100
        ),
        bakery_food_probability=_bounded_float(
            payload.get("bakeryFoodProbability", 0.75),
            "bakeryFoodProbability",
            0,
            1,
        ),
        staffed_pantry_open_probability=_bounded_float(
            payload.get("staffedPantryOpenProbability", 0.90),
            "staffedPantryOpenProbability",
            0,
            1,
        ),
        pantry_history_size=_bounded_int(
            payload.get("pantryHistorySize", 10), "pantryHistorySize", 1, 100
        ),
        timezone=str(payload.get("timezone") or "America/New_York"),
    )
    report = simulate_snapshot(
        _network(),
        config,
        # Reproducibility is more valuable than traffic volatility in the
        # academic simulator. A traffic experiment can inject another provider.
        HaversineTravelTimeProvider(),
    )
    return report.as_dict()
    request_rows = [
        item for item in request_rows
        if int(item["id"]) not in protected_request_ids
    ]
    driver_records = {record.id: record for record in snapshot.drivers if record.active}
    requests: list[DriverRequest] = []
    for item in request_rows:
        driver = driver_records.get(int(item["driverId"]))
        start = driver.location() if driver else None
        if start is None:
            continue
        earliest = _datetime(item["earliestStart"])
        latest = _datetime(item["latestFinish"])
        if latest <= earliest:
            continue
        requests.append(DriverRequest(
            id=str(item["id"]),
            driver_id=str(item["driverId"]),
            earliest_start=earliest,
            latest_finish=latest,
            start_location=start,
        ))

    if not requests:
        return {
            "generatedAt": datetime.now().astimezone().isoformat(),
            "solver": {
                "backend": "gurobi",
                "status": "no_eligible_driver_requests",
                "candidateCount": 0,
                "matchedCount": 0,
            },
            "assignments": [],
        }
    earliest = min(request.earliest_start for request in requests)
    latest = max(request.latest_finish for request in requests)
    travel = GoogleMapsProvider(os.environ["GOOGLE_MAPS_API_KEY"]) if os.getenv("GOOGLE_MAPS_API_KEY") else HaversineTravelTimeProvider()
    result = optimize_network(
        _pickups(snapshot, earliest, latest),
        _pantries(snapshot, earliest, latest),
        requests,
        travel,
    )
    return {
        "generatedAt": datetime.now().astimezone().isoformat(),
        "solver": {
            "backend": result.diagnostics.backend,
            "status": result.diagnostics.status,
            "candidateCount": result.diagnostics.candidate_count,
            "matchedCount": result.diagnostics.matched_count,
            "routeQuality": result.diagnostics.route_quality,
            "runtimeSeconds": result.diagnostics.runtime_seconds,
            "mipGap": result.diagnostics.mip_gap,
        },
        "assignments": [{
            "requestId": int(assignment.request_id),
            "driverId": int(assignment.driver_id),
            "pickupId": int(assignment.route.bakery_id),
            "bakeryName": assignment.route.bakery_name,
            "pantryId": int(assignment.route.pantry_id.split(":", 1)[0]),
            "pantryName": assignment.route.pantry_name,
            "pantryServiceMode": _service_mode(snapshot, assignment.route.pantry_id),
            "departAt": assignment.route.depart_at.isoformat(),
            "pickupAt": assignment.route.pickup_at.isoformat(),
            "pantryArrivalAt": assignment.route.pantry_arrival_at.isoformat(),
            "finishAt": assignment.route.finish_at.isoformat(),
            "driveMinutes": assignment.route.drive_minutes,
            "waitingMinutes": assignment.route.waiting_minutes,
            "pantryPriority": assignment.route.pantry_priority,
            "score": assignment.route.score,
            "explanation": list(assignment.route.explanation),
        } for assignment in result.assignments],
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
        if offer.get("status") in {"offered", "accepted"}
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
        if _organization_paused_at(snapshot, "pantry", pantry.id, starts):
            continue
        service_mode = str(item.get("serviceMode") or "staffed")
        window_key = f"one-time:{item['id']}"
        if not _pantry_window_confirmed(snapshot, pantry.id, window_key, service_mode):
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
        results.extend(_recurring_pantry_windows(snapshot, pantry, earliest, latest))
    return results


def _recurring_pantry_windows(snapshot: NetworkSnapshot, record: Any, earliest: datetime, latest: datetime) -> list[Pantry]:
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
        if _schedule_cancelled(snapshot, "pantry", record.id, current):
            current += timedelta(days=1)
            continue
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
            if _organization_paused_at(snapshot, "pantry", record.id, starts):
                continue
            window_key = f"recurring:{current.isoformat()}:{index}"
            if not _pantry_window_confirmed(snapshot, record.id, window_key, mode):
                continue
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


def _pantry_window_confirmed(
    snapshot: NetworkSnapshot,
    pantry_id: int,
    window_key: str,
    service_mode: str,
) -> bool:
    """Unattended windows are self-service; staffed windows require confirmation."""

    if service_mode == "unattended":
        return True
    expected = f"pantry-open:{pantry_id}:{window_key}"
    return any(
        item.get("recipientId") == pantry_id
        and item.get("actionKey") == expected
        and bool(item.get("acknowledgedAt"))
        for item in snapshot.pantry_window_confirmations
    )


def _organization_paused_at(
    snapshot: NetworkSnapshot,
    organization_type: str,
    organization_id: int,
    moment: datetime,
) -> bool:
    for pause in snapshot.availability_pauses:
        if pause.get("organizationType") != organization_type or int(pause.get("organizationId", -1)) != organization_id:
            continue
        try:
            created = _datetime(pause["createdAt"])
            ends = _datetime(pause["endsAt"])
        except (KeyError, TypeError, ValueError):
            continue
        if created <= moment <= ends:
            return True
    return False


def _schedule_cancelled(
    snapshot: NetworkSnapshot,
    organization_type: str,
    organization_id: int,
    service_date: date,
) -> bool:
    return any(
        item.get("organizationType") == organization_type
        and int(item.get("organizationId", -1)) == organization_id
        and item.get("exceptionDate") == service_date.isoformat()
        for item in snapshot.schedule_exceptions
    )


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


def _bounded_int(value: Any, name: str, minimum: int, maximum: int) -> int:
    parsed = int(value)
    if not minimum <= parsed <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return parsed


def _bounded_float(value: Any, name: str, minimum: float, maximum: float) -> float:
    parsed = float(value)
    if not minimum <= parsed <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return parsed
