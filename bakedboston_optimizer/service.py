from __future__ import annotations

import json
import os
import random
import re
from dataclasses import replace
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .google_maps import GoogleMapsProvider
from .models import AddressValidationStatus, BakeryPickup, DriverRequest, Location, Pantry
from .network import BakedBostonNetworkClient, NetworkSnapshot, parse_snapshot
from .optimizer import active_solver_backend, optimize_network, rank_routes
from .simulation import SimulationConfig, simulate_snapshot
from .travel import HaversineTravelTimeProvider
from .web_export import build_web_payload


def recommend(payload: dict[str, Any]) -> dict[str, Any]:
    """Return ranked live recommendations using the same model as the hackathon demo."""

    preferred_start = _datetime(payload["earliestStart"])
    preferred_finish = _datetime(payload["latestFinish"])
    if preferred_finish <= preferred_start:
        raise ValueError("latestFinish must be after earliestStart")
    if preferred_finish - preferred_start < timedelta(minutes=30):
        raise ValueError("Requested delivery windows must be at least 30 minutes")
    logged_at = _datetime(payload.get("loggedAt") or payload["earliestStart"])
    search_until = _datetime(payload["searchUntil"]) if payload.get("searchUntil") else preferred_finish + timedelta(minutes=90)
    if search_until <= logged_at:
        raise ValueError("searchUntil must be after loggedAt")
    start = _location("driver-start", payload["startLocation"])
    preferred = _location("preferred-destination", payload["preferredDestination"]) if payload.get("preferredDestination") else None
    snapshot = _network()
    pickups = _pickups(snapshot, logged_at, search_until)
    pantries = _pantries(snapshot, logged_at, search_until)
    travel = GoogleMapsProvider(os.environ["GOOGLE_MAPS_API_KEY"]) if os.getenv("GOOGLE_MAPS_API_KEY") else HaversineTravelTimeProvider()
    request = DriverRequest(
        earliest_start=preferred_start,
        latest_finish=preferred_finish,
        start_location=start,
        preferred_destination=preferred,
        logged_at=logged_at,
        search_until=search_until,
        start_radius_miles=float(payload.get("startRadiusMiles") or 2.0),
        destination_radius_miles=float(payload.get("destinationRadiusMiles") or 2.0),
        start_zip_code=str(payload.get("startZipCode") or start.postal_code or ""),
        destination_zip_code=str(
            payload.get("destinationZipCode")
            or (preferred.postal_code if preferred is not None else "")
            or ""
        ),
    )
    routes = rank_routes(
        pickups,
        pantries,
        request,
        travel,
    )
    return {
        "generatedAt": datetime.now().astimezone().isoformat(),
        "solver": {
            "backend": active_solver_backend(),
            "status": "optimal" if active_solver_backend() == "gurobi" else "fallback",
            "candidateCount": len(routes),
        },
        "request": _request_payload(request),
        "routes": [_route_payload(snapshot, route) for route in routes[:10]],
    }


def recommend_network(payload: dict[str, Any]) -> dict[str, Any]:
    """Optimize active driver requests and confirmed pickups as one MIP."""

    snapshot = _network()
    maps = (
        GoogleMapsProvider(os.environ["GOOGLE_MAPS_API_KEY"])
        if os.getenv("GOOGLE_MAPS_API_KEY")
        else None
    )
    travel = maps or HaversineTravelTimeProvider()
    zip_locations: dict[str, Location | None] = {}
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
    request_rows = [
        item for item in request_rows
        if int(item["id"]) not in protected_request_ids
    ]
    driver_records = {record.id: record for record in snapshot.drivers if record.active}
    requests: list[DriverRequest] = []
    for item in request_rows:
        driver = driver_records.get(int(item["driverId"]))
        driver_location = driver.location() if driver else None
        preferred_start_location = _zip_location(
            maps,
            item.get("startZip"),
            zip_locations,
        )
        preferred_destination = _zip_location(
            maps,
            item.get("endZip"),
            zip_locations,
        )
        # A requested starting ZIP describes where the driver expects to begin
        # this particular trip. When it is omitted (or temporarily cannot be
        # geocoded), fall back to the validated location on the driver profile.
        start = preferred_start_location or driver_location
        if start is None:
            continue
        preferred_start = _datetime(item["earliestStart"])
        preferred_finish = _datetime(item["latestFinish"])
        if preferred_finish - preferred_start < timedelta(minutes=30):
            continue
        logged_at = _datetime(item.get("loggedAt") or item.get("createdAt") or item["earliestStart"])
        search_until = _datetime(item["searchUntil"]) if item.get("searchUntil") else preferred_finish + timedelta(minutes=90)
        if search_until <= logged_at:
            continue
        requests.append(DriverRequest(
            id=str(item["id"]),
            driver_id=str(item["driverId"]),
            earliest_start=preferred_start,
            latest_finish=preferred_finish,
            start_location=start,
            preferred_destination=preferred_destination,
            logged_at=logged_at,
            search_until=search_until,
            start_radius_miles=float(item.get("startRadiusMiles") or 2.0),
            destination_radius_miles=float(item.get("destinationRadiusMiles") or 2.0),
            start_zip_code=str(item.get("startZip") or start.postal_code or ""),
            destination_zip_code=str(
                item.get("endZip")
                or (preferred_destination.postal_code if preferred_destination else "")
                or ""
            ),
        ))

    if not requests:
        return {
            "generatedAt": datetime.now().astimezone().isoformat(),
            "solver": {
                "backend": active_solver_backend(),
                "status": "no_eligible_driver_requests",
                "candidateCount": 0,
                "matchedCount": 0,
            },
            "assignments": [],
        }
    horizon_start = min(request.login_time for request in requests)
    horizon_end = max(request.hard_search_end for request in requests)
    result = optimize_network(
        _pickups(snapshot, horizon_start, horizon_end),
        _pantries(snapshot, horizon_start, horizon_end),
        requests,
        travel,
    )
    request_by_id = {request.id: request for request in requests}
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
            "request": _request_payload(request_by_id[assignment.request_id]),
            **_route_payload(snapshot, assignment.route),
        } for assignment in result.assignments],
    }


def _zip_location(
    maps: GoogleMapsProvider | None,
    raw_zip: object,
    cache: dict[str, Location | None],
) -> Location | None:
    """Resolve a soft ZIP preference without making matching depend on geocoding.

    Saved ride requests may omit either ZIP. A temporary Google failure should
    degrade to the driver's profile origin or no destination preference rather
    than suppressing every otherwise feasible route.
    """

    zip_code = str(raw_zip or "").strip()
    if maps is None or not zip_code:
        return None
    if zip_code not in cache:
        try:
            cache[zip_code] = maps.validate_address(f"{zip_code}, USA")
        except (KeyError, RuntimeError, TimeoutError, ValueError, OSError):
            cache[zip_code] = None
    return cache[zip_code]


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


def simulate_custom_experiment(payload: dict[str, Any]) -> dict[str, Any]:
    """Run a bounded, reproducible public academic comparison.

    The public simulator may vary the size of the synthetic network, but every
    comparison model receives the exact same institutions, food draws, driver
    events, and random seed. The public payload currently compares BakedBoston
    with the Nair et al. (2018) distance-first adaptation.
    """

    # Public and saved academic comparisons use one consistent five-day horizon.
    days = 5
    drivers_per_day = _bounded_int(
        payload.get("driversPerDay", 6), "driversPerDay", 1, 12
    )
    bakery_count = _bounded_int(payload.get("bakeryCount", 9), "bakeryCount", 2, 9)
    pantry_count = _bounded_int(payload.get("pantryCount", 9), "pantryCount", 2, 9)
    random_seed = _bounded_int(
        payload.get("randomSeed", 2042), "randomSeed", 1, 999_999_999
    )
    max_simultaneous_drivers = _bounded_int(
        payload.get("maxSimultaneousDrivers", 3),
        "maxSimultaneousDrivers",
        2,
        3,
    )

    fixture_path = (
        Path(__file__).resolve().parents[1]
        / "data"
        / "academic_comparison_snapshot.json"
    )
    fixture = json.loads(fixture_path.read_text())
    if not isinstance(fixture, dict):
        raise TypeError("The academic network fixture must be a JSON object")
    source_snapshot = parse_snapshot(fixture)
    bakeries = list(source_snapshot.eligible_bakeries)
    pantries = list(source_snapshot.eligible_pantries)
    if bakery_count > len(bakeries) or pantry_count > len(pantries):
        raise ValueError("Requested network size exceeds the academic fixture")

    rng = random.Random(random_seed)
    selected_bakeries = tuple(
        sorted(rng.sample(bakeries, bakery_count), key=lambda item: item.id)
    )
    selected_pantries = tuple(
        sorted(rng.sample(pantries, pantry_count), key=lambda item: item.id)
    )
    snapshot = replace(
        source_snapshot,
        bakeries=selected_bakeries,
        pantries=selected_pantries,
    )
    result = build_web_payload(
        snapshot,
        start_date=date(2026, 8, 24),
        days=days,
        seed=random_seed,
        drivers_per_day=drivers_per_day,
        matching_interval_minutes=60,
        max_simultaneous_drivers=max_simultaneous_drivers,
    )
    result["displayMode"] = "live_custom_gurobi_experiment"
    result["scenario"].update({
        "bakeryCount": bakery_count,
        "pantryCount": pantry_count,
    })
    return result


def _service_mode(snapshot: NetworkSnapshot, pantry_window_id: str) -> str:
    parts = pantry_window_id.split(":")
    if len(parts) > 2 and parts[1] == "recurring":
        return parts[-1] if parts[-1] in {"staffed", "unattended"} else "staffed"
    _, _, window_id = pantry_window_id.partition(":")
    for window in snapshot.availability_windows:
        if str(window.get("id")) == window_id:
            return str(window.get("serviceMode") or "staffed")
    return "staffed"


def _request_payload(request: DriverRequest) -> dict[str, Any]:
    return {
        "loggedAt": request.login_time.isoformat(),
        "preferredStart": request.preferred_start.isoformat(),
        "preferredFinish": request.preferred_finish.isoformat(),
        "searchUntil": request.hard_search_end.isoformat(),
        "requestedWindowMinutes": (
            request.preferred_finish - request.preferred_start
        ).total_seconds() / 60,
        "startLocation": {
            "formattedAddress": request.start_location.formatted_address,
            "latitude": request.start_location.latitude,
            "longitude": request.start_location.longitude,
            "postalCode": request.start_location.postal_code or request.start_zip_code,
        },
        "startZipCode": request.start_zip_code or request.start_location.postal_code,
        "startRadiusMiles": request.start_radius_miles,
        "preferredDestination": (
            {
                "formattedAddress": request.preferred_destination.formatted_address,
                "latitude": request.preferred_destination.latitude,
                "longitude": request.preferred_destination.longitude,
                "postalCode": (
                    request.preferred_destination.postal_code
                    or request.destination_zip_code
                ),
            }
            if request.preferred_destination is not None
            else None
        ),
        "destinationZipCode": request.destination_zip_code,
        "destinationRadiusMiles": (
            request.destination_radius_miles
            if request.preferred_destination is not None
            else None
        ),
    }


def _route_payload(snapshot: NetworkSnapshot, route: Any) -> dict[str, Any]:
    """Serialize both the operational itinerary and its soft-window diagnostics."""

    return {
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
        # Kept for older mobile clients; this is waiting at the origin before
        # the just-in-time departure, not time spent idling at a facility.
        "waitingMinutes": route.waiting_minutes,
        "waitUntilDepartureMinutes": route.waiting_minutes,
        "predepartureWaitMinutes": route.waiting_minutes,
        "facilityWaitMinutes": route.facility_waiting_minutes,
        "requestedTimeDeviationMinutes": route.requested_time_deviation_minutes,
        "outsideRequestedWindowMinutes": route.requested_time_deviation_minutes,
        "requestedWindowMinutes": route.requested_window_minutes,
        "outsideRequestedWindowRatio": route.requested_time_deviation_ratio,
        "outsideRequestedWindowPercent": 100 * route.requested_time_deviation_ratio,
        "withinPreferredWindow": route.within_preferred_window,
        "originDeviationMiles": route.origin_deviation_miles,
        "destinationDeviationMiles": route.destination_deviation_miles,
        "normalizedOriginDeviation": route.normalized_origin_deviation,
        "normalizedDestinationDeviation": route.normalized_destination_deviation,
        "normalizedSpatialDeviation": route.normalized_spatial_deviation,
        "spatialDeviationPercent": 100 * route.normalized_spatial_deviation,
        "pantryPriority": route.pantry_priority,
        "score": route.score,
        "explanation": list(route.explanation),
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
    address = str(value.get("formattedAddress") or value.get("address") or identifier)
    postal_match = re.search(r"\b\d{5}(?:-\d{4})?\b", address)
    return Location(
        address_entered=str(value.get("address") or address),
        formatted_address=address,
        latitude=float(value["latitude"]),
        longitude=float(value["longitude"]),
        validation_status=AddressValidationStatus.VALIDATED,
        postal_code=str(value.get("postalCode") or (postal_match.group(0) if postal_match else "")),
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
