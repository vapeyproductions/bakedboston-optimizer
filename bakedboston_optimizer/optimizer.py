from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from itertools import product

from .models import BakeryPickup, DriverRequest, Pantry, RouteCandidate
from .travel import TravelTimeProvider


@dataclass(frozen=True)
class OptimizationWeights:
    pantry_priority_reward: float = 45.0
    drive_minute_penalty: float = 1.0
    waiting_minute_penalty: float = 0.35
    destination_minute_penalty: float = 0.65


def rank_routes(
    pickups: list[BakeryPickup],
    pantries: list[Pantry],
    request: DriverRequest,
    travel: TravelTimeProvider,
    weights: OptimizationWeights = OptimizationWeights(),
    pickup_service_minutes: int = 15,
    dropoff_service_minutes: int = 15,
) -> list[RouteCandidate]:
    """Generate, filter, score, and rank complete bakery-to-pantry routes."""

    candidates: list[RouteCandidate] = []
    for pickup, pantry in product(pickups, pantries):
        candidate = _candidate(
            pickup,
            pantry,
            request,
            travel,
            weights,
            pickup_service_minutes,
            dropoff_service_minutes,
        )
        if candidate is not None:
            candidates.append(candidate)
    return sorted(candidates, key=lambda route: (-route.score, route.finish_at, route.drive_minutes))


def _candidate(
    pickup: BakeryPickup,
    pantry: Pantry,
    request: DriverRequest,
    travel: TravelTimeProvider,
    weights: OptimizationWeights,
    pickup_service_minutes: int,
    dropoff_service_minutes: int,
) -> RouteCandidate | None:
    if pickup.claimed:
        return None

    to_bakery = travel.duration_minutes(request.start_location, pickup.location, request.earliest_start)
    arrival_at_bakery = request.earliest_start + timedelta(minutes=to_bakery)
    pickup_at = max(arrival_at_bakery, pickup.ready_at)
    if pickup_at > pickup.pickup_deadline:
        return None

    waiting_at_bakery = max(0.0, (pickup.ready_at - arrival_at_bakery).total_seconds() / 60)
    leave_bakery = pickup_at + timedelta(minutes=pickup_service_minutes)
    to_pantry = travel.duration_minutes(pickup.location, pantry.location, leave_bakery)
    raw_pantry_arrival = leave_bakery + timedelta(minutes=to_pantry)
    pantry_arrival = max(raw_pantry_arrival, pantry.receiving_start)
    waiting_at_pantry = max(0.0, (pantry.receiving_start - raw_pantry_arrival).total_seconds() / 60)

    if pantry_arrival > pantry.latest_permitted_arrival or pantry_arrival > pantry.receiving_end:
        return None

    finish_at = pantry_arrival + timedelta(minutes=dropoff_service_minutes)
    if finish_at > request.latest_finish:
        return None

    destination_minutes = 0.0
    if request.preferred_destination is not None:
        destination_minutes = travel.duration_minutes(
            pantry.location,
            request.preferred_destination,
            finish_at,
        )

    drive_minutes = to_bakery + to_pantry
    waiting_minutes = waiting_at_bakery + waiting_at_pantry
    score = (
        weights.pantry_priority_reward * pantry.priority_score
        - weights.drive_minute_penalty * drive_minutes
        - weights.waiting_minute_penalty * waiting_minutes
        - weights.destination_minute_penalty * destination_minutes
    )
    return RouteCandidate(
        bakery_id=pickup.id,
        bakery_name=pickup.bakery_name,
        bakery_address=pickup.location.formatted_address,
        pantry_id=pantry.id,
        pantry_name=pantry.pantry_name,
        pantry_address=pantry.location.formatted_address,
        depart_at=request.earliest_start,
        pickup_at=pickup_at,
        pantry_arrival_at=pantry_arrival,
        finish_at=finish_at,
        drive_minutes=drive_minutes,
        waiting_minutes=waiting_minutes,
        destination_minutes=destination_minutes,
        pantry_priority=pantry.priority_score,
        score=score,
        explanation=_explanation(pantry.priority_score, drive_minutes, destination_minutes),
    )


def _explanation(priority: float, drive_minutes: float, destination_minutes: float) -> tuple[str, ...]:
    reasons: list[str] = []
    if priority >= 0.7:
        reasons.append("Serves a higher-priority pantry")
    if drive_minutes <= 30:
        reasons.append("Short total driving time")
    if destination_minutes and destination_minutes <= 15:
        reasons.append("Ends close to the preferred destination")
    if not reasons:
        reasons.append("Best available balance of feasibility and priority")
    return tuple(reasons)
