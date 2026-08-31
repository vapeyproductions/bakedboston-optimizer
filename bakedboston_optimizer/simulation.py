from __future__ import annotations

import json
import hashlib
import random
from collections import defaultdict, deque
from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from .models import (
    AddressValidationStatus,
    BakeryPickup,
    DriverRequest,
    Location,
    NetworkOptimizationResult,
    Pantry,
    TriangularDistribution,
    WasteAllocation,
)
from .network import NetworkSnapshot, OrganizationRecord
from .optimizer import OptimizationWeights, optimize_network
from .travel import HaversineTravelTimeProvider, TravelTimeProvider


@dataclass(frozen=True)
class SimulationConfig:
    """Reproducible assumptions for a schedule-driven academic experiment."""

    start_date: date
    days: int = 7
    random_seed: int = 2026
    drivers_per_day: int = 8
    bakery_food_probability: float = 0.75
    staffed_pantry_open_probability: float = 0.90
    pantry_history_size: int = 10
    timezone: str = "America/New_York"

    def __post_init__(self) -> None:
        if self.days < 1:
            raise ValueError("days must be at least 1")
        if self.drivers_per_day < 1:
            raise ValueError("drivers_per_day must be at least 1")
        for name, value in (
            ("bakery_food_probability", self.bakery_food_probability),
            ("staffed_pantry_open_probability", self.staffed_pantry_open_probability),
        ):
            if not 0 <= value <= 1:
                raise ValueError(f"{name} must be between 0 and 1")
        if self.pantry_history_size < 1:
            raise ValueError("pantry_history_size must be at least 1")
        ZoneInfo(self.timezone)


@dataclass(frozen=True)
class SimulationEvent:
    occurred_at: datetime
    kind: str
    organization_type: str
    organization_id: str
    organization_name: str
    detail: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "occurredAt": self.occurred_at.isoformat(),
            "kind": self.kind,
            "organizationType": self.organization_type,
            "organizationId": self.organization_id,
            "organizationName": self.organization_name,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class SimulationAssignment:
    request_id: str
    driver_id: str
    pickup_id: str
    bakery_name: str
    pantry_window_id: str
    pantry_name: str
    depart_at: datetime
    pickup_at: datetime
    pantry_arrival_at: datetime
    finish_at: datetime
    drive_minutes: float
    waiting_minutes: float
    pantry_priority: float
    score: float
    estimated_food_kg: float
    usable_food_kg: float
    bakery_usable_fraction: float
    pantry_distribution_fraction: float
    food_saved_kg: float
    collected_not_distributed_kg: float
    avoided_system_kg_co2e: float
    transport_kg_co2e: float
    residual_waste_kg_co2e: float
    net_environmental_benefit_kg_co2e: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "requestId": self.request_id,
            "driverId": self.driver_id,
            "pickupId": self.pickup_id,
            "bakeryName": self.bakery_name,
            "pantryWindowId": self.pantry_window_id,
            "pantryName": self.pantry_name,
            "departAt": self.depart_at.isoformat(),
            "pickupAt": self.pickup_at.isoformat(),
            "pantryArrivalAt": self.pantry_arrival_at.isoformat(),
            "finishAt": self.finish_at.isoformat(),
            "driveMinutes": round(self.drive_minutes, 2),
            "waitingMinutes": round(self.waiting_minutes, 2),
            "pantryPriority": round(self.pantry_priority, 4),
            "score": round(self.score, 3),
            "bakeryUsableFraction": round(self.bakery_usable_fraction, 4),
            "pantryDistributionFraction": round(
                self.pantry_distribution_fraction, 4
            ),
            "foodSavedKg": round(self.food_saved_kg, 3),
            "collectedNotDistributedKg": round(
                self.collected_not_distributed_kg, 3
            ),
            "netEnvironmentalBenefitKgCO2e": round(
                self.net_environmental_benefit_kg_co2e, 3
            ),
        }


@dataclass(frozen=True)
class SimulationDayResult:
    service_date: date
    scheduled_pickup_windows: int
    food_available_pickups: int
    scheduled_pantry_windows: int
    open_pantry_windows: int
    synthetic_drivers: int
    uncollected_bakery_food_kg: float
    assignments: tuple[SimulationAssignment, ...]
    events: tuple[SimulationEvent, ...]
    solver: dict[str, Any]

    @property
    def missed_pickups(self) -> int:
        return max(0, self.food_available_pickups - len(self.assignments))

    def as_dict(self) -> dict[str, Any]:
        return {
            "serviceDate": self.service_date.isoformat(),
            "scheduledPickupWindows": self.scheduled_pickup_windows,
            "foodAvailablePickups": self.food_available_pickups,
            "scheduledPantryWindows": self.scheduled_pantry_windows,
            "openPantryWindows": self.open_pantry_windows,
            "syntheticDrivers": self.synthetic_drivers,
            "matchedPickups": len(self.assignments),
            "missedPickups": self.missed_pickups,
            "uncollectedBakeryFoodKg": round(self.uncollected_bakery_food_kg, 3),
            "solver": self.solver,
            "assignments": [assignment.as_dict() for assignment in self.assignments],
            "events": [event.as_dict() for event in self.events],
        }


@dataclass(frozen=True)
class SimulationReport:
    config: SimulationConfig
    days: tuple[SimulationDayResult, ...]
    pantry_opportunities: dict[str, dict[str, int]]

    def metrics(self) -> dict[str, Any]:
        food_pickups = sum(day.food_available_pickups for day in self.days)
        assignments = [assignment for day in self.days for assignment in day.assignments]
        open_pantry_windows = sum(day.open_pantry_windows for day in self.days)
        served_opportunities = sum(
            item["served"] for item in self.pantry_opportunities.values()
        )
        food_saved_kg = sum(item.food_saved_kg for item in assignments)
        collected_not_distributed_kg = sum(
            item.collected_not_distributed_kg for item in assignments
        )
        uncollected_bakery_food_kg = sum(
            day.uncollected_bakery_food_kg for day in self.days
        )
        net_environmental_benefit_kg_co2e = sum(
            item.net_environmental_benefit_kg_co2e for item in assignments
        )
        return {
            "scheduledPickupWindows": sum(day.scheduled_pickup_windows for day in self.days),
            "foodAvailablePickups": food_pickups,
            "matchedPickups": len(assignments),
            "missedPickups": max(0, food_pickups - len(assignments)),
            "pickupCoverage": round(len(assignments) / food_pickups, 4) if food_pickups else 0.0,
            "openPantryWindows": open_pantry_windows,
            "servedPantryOpportunities": served_opportunities,
            "pantryOpportunityCoverage": (
                round(served_opportunities / open_pantry_windows, 4)
                if open_pantry_windows else 0.0
            ),
            "uniquePantriesServed": len({item.pantry_name for item in assignments}),
            "averageDriveMinutes": (
                round(sum(item.drive_minutes for item in assignments) / len(assignments), 2)
                if assignments else 0.0
            ),
            "averageWaitingMinutes": (
                round(sum(item.waiting_minutes for item in assignments) / len(assignments), 2)
                if assignments else 0.0
            ),
            "foodSavedKg": round(food_saved_kg, 3),
            "uncollectedBakeryFoodKg": round(uncollected_bakery_food_kg, 3),
            "collectedNotDistributedKg": round(
                collected_not_distributed_kg, 3
            ),
            "netEnvironmentalBenefitKgCO2e": round(
                net_environmental_benefit_kg_co2e, 3
            ),
            "averageNetEnvironmentalBenefitKgCO2ePerDelivery": (
                round(net_environmental_benefit_kg_co2e / len(assignments), 3)
                if assignments else 0.0
            ),
            "totalSolverRuntimeSeconds": round(
                sum(float(day.solver.get("runtimeSeconds", 0.0)) for day in self.days), 4
            ),
        }

    def as_dict(self) -> dict[str, Any]:
        return {
            "mode": "academic_schedule_simulation",
            "scope": {
                "startDate": self.config.start_date.isoformat(),
                "days": self.config.days,
                "randomSeed": self.config.random_seed,
                "timezone": self.config.timezone,
                "driversPerDay": self.config.drivers_per_day,
                "bakeryFoodProbability": self.config.bakery_food_probability,
                "staffedPantryOpenProbability": self.config.staffed_pantry_open_probability,
                "pantryHistorySize": self.config.pantry_history_size,
            },
            "disclaimer": (
                "Institution names, locations, and public schedule templates are used for an academic "
                "simulation only. Inclusion does not imply participation or affiliation."
            ),
            "metrics": self.metrics(),
            "pantryOpportunities": self.pantry_opportunities,
            "days": [day.as_dict() for day in self.days],
        }


def pantry_priority(history: list[bool] | tuple[bool, ...]) -> float:
    """Estimate need from recent receiving opportunities instead of calendar days.

    A `True` value means the pantry received at least one simulated delivery during
    an open receiving opportunity. Laplace smoothing gives a new pantry a neutral
    0.5 priority. Repeated missed opportunities raise priority without excluding
    a pantry that recently received food.
    """

    served = sum(history)
    opportunities = len(history)
    return 1.0 - (served + 1.0) / (opportunities + 2.0)


def simulate_snapshot(
    snapshot: NetworkSnapshot,
    config: SimulationConfig,
    travel: TravelTimeProvider | None = None,
    weights: OptimizationWeights = OptimizationWeights(),
) -> SimulationReport:
    """Run a seeded, side-effect-free simulation over real schedule templates.

    The function reads the supplied snapshot but never writes to BakedBoston,
    sends notifications, reserves a live pickup, or changes an organization.
    """

    rng = random.Random(config.random_seed)
    zone = ZoneInfo(config.timezone)
    travel_provider = travel or HaversineTravelTimeProvider()
    history: dict[int, deque[bool]] = defaultdict(
        lambda: deque(maxlen=config.pantry_history_size)
    )
    opportunity_totals: dict[str, dict[str, int]] = defaultdict(
        lambda: {"available": 0, "served": 0}
    )
    pantry_raw_totals: dict[int, float] = defaultdict(float)
    pantry_saved_totals: dict[int, float] = defaultdict(float)
    day_results: list[SimulationDayResult] = []

    for offset in range(config.days):
        service_date = config.start_date + timedelta(days=offset)
        scheduled_pickups = _bakery_windows(snapshot, service_date, zone)
        pickups, pickup_events = _sample_food_availability(
            scheduled_pickups,
            config.bakery_food_probability,
            rng,
            random_seed=config.random_seed,
        )
        scheduled_pantries = _pantry_windows(snapshot, service_date, zone)
        open_pantries, pantry_events = _sample_pantry_openings(
            scheduled_pantries,
            config.staffed_pantry_open_probability,
            rng,
        )
        prioritized_pantries = [
            replace(
                pantry,
                priority_score=pantry_priority(tuple(history[_organization_id(pantry.id)])),
                historical_raw_food_kg=pantry_raw_totals[_organization_id(pantry.id)],
                historical_saved_food_kg=pantry_saved_totals[_organization_id(pantry.id)],
            )
            for pantry in open_pantries
        ]
        requests = _synthetic_driver_requests(
            pickups,
            prioritized_pantries,
            config.drivers_per_day,
            rng,
        )
        result = optimize_network(
            pickups,
            prioritized_pantries,
            requests,
            travel_provider,
            weights=weights,
        )
        assignments = _assignments(result)
        pickup_by_id = {pickup.id: pickup for pickup in pickups}
        collected_pickup_ids = {assignment.pickup_id for assignment in assignments}
        uncollected_bakery_food_kg = sum(
            pickup.estimated_food_kg
            for pickup in pickups
            if pickup.id not in collected_pickup_ids
        )
        served_windows = {assignment.pantry_window_id for assignment in assignments}
        for assignment in assignments:
            pantry_id = _organization_id(assignment.pantry_window_id)
            pantry_raw_totals[pantry_id] += pickup_by_id[
                assignment.pickup_id
            ].estimated_food_kg
            pantry_saved_totals[pantry_id] += assignment.food_saved_kg
        for pantry in prioritized_pantries:
            served = pantry.id in served_windows
            pantry_id = _organization_id(pantry.id)
            history[pantry_id].append(served)
            opportunity_totals[pantry.pantry_name]["available"] += 1
            opportunity_totals[pantry.pantry_name]["served"] += int(served)

        assignment_events = tuple(
            SimulationEvent(
                occurred_at=assignment.depart_at,
                kind="assignment_selected",
                organization_type="route",
                organization_id=assignment.pickup_id,
                organization_name=f"{assignment.bakery_name} → {assignment.pantry_name}",
                detail=(
                    f"Gurobi assigned {assignment.driver_id}; score {assignment.score:.2f}, "
                    f"priority {assignment.pantry_priority:.2f}."
                ),
            )
            for assignment in assignments
        )
        events = tuple(sorted(
            (*pickup_events, *pantry_events, *assignment_events),
            key=lambda event: (event.occurred_at, event.kind, event.organization_id),
        ))
        day_results.append(SimulationDayResult(
            service_date=service_date,
            scheduled_pickup_windows=len(scheduled_pickups),
            food_available_pickups=len(pickups),
            scheduled_pantry_windows=len(scheduled_pantries),
            open_pantry_windows=len(prioritized_pantries),
            synthetic_drivers=len(requests),
            uncollected_bakery_food_kg=uncollected_bakery_food_kg,
            assignments=assignments,
            events=events,
            solver=_solver_dict(result),
        ))

    return SimulationReport(
        config=config,
        days=tuple(day_results),
        pantry_opportunities=dict(sorted(opportunity_totals.items())),
    )


def _assignments(result: NetworkOptimizationResult) -> tuple[SimulationAssignment, ...]:
    return tuple(
        SimulationAssignment(
            request_id=assignment.request_id,
            driver_id=assignment.driver_id,
            pickup_id=assignment.route.bakery_id,
            bakery_name=assignment.route.bakery_name,
            pantry_window_id=assignment.route.pantry_id,
            pantry_name=assignment.route.pantry_name,
            depart_at=assignment.route.depart_at,
            pickup_at=assignment.route.pickup_at,
            pantry_arrival_at=assignment.route.pantry_arrival_at,
            finish_at=assignment.route.finish_at,
            drive_minutes=assignment.route.drive_minutes,
            waiting_minutes=assignment.route.waiting_minutes,
            pantry_priority=assignment.route.pantry_priority,
            score=assignment.route.score,
            estimated_food_kg=assignment.route.estimated_food_kg,
            usable_food_kg=assignment.route.usable_food_kg,
            bakery_usable_fraction=assignment.route.bakery_usable_fraction,
            pantry_distribution_fraction=(
                assignment.route.pantry_distribution_fraction
            ),
            food_saved_kg=assignment.route.food_saved_kg,
            collected_not_distributed_kg=(
                assignment.route.collected_not_distributed_kg
            ),
            avoided_system_kg_co2e=assignment.route.avoided_system_kg_co2e,
            transport_kg_co2e=assignment.route.transport_kg_co2e,
            residual_waste_kg_co2e=assignment.route.residual_waste_kg_co2e,
            net_environmental_benefit_kg_co2e=(
                assignment.route.net_environmental_benefit_kg_co2e
            ),
        )
        for assignment in result.assignments
    )


def _solver_dict(result: NetworkOptimizationResult) -> dict[str, Any]:
    diagnostics = result.diagnostics
    return {
        "backend": diagnostics.backend,
        "status": diagnostics.status,
        "candidateCount": diagnostics.candidate_count,
        "matchedCount": diagnostics.matched_count,
        "routeQuality": round(diagnostics.route_quality, 4),
        "expectedCompletedDeliveries": round(
            diagnostics.expected_completed_deliveries, 4
        ),
        "routeDistanceMiles": round(diagnostics.route_distance_miles, 4),
        "foodSavedKg": round(diagnostics.food_saved_kg, 4),
        "collectedNotDistributedKg": round(
            diagnostics.collected_not_distributed_kg, 4
        ),
        "netEnvironmentalBenefitKgCO2e": round(
            diagnostics.net_environmental_benefit_kg_co2e, 4
        ),
        "runtimeSeconds": round(diagnostics.runtime_seconds, 6),
        "mipGap": diagnostics.mip_gap,
    }


def _bakery_windows(
    snapshot: NetworkSnapshot,
    service_date: date,
    zone: ZoneInfo,
) -> list[BakeryPickup]:
    records = {record.id: record for record in snapshot.eligible_bakeries}
    result: list[BakeryPickup] = []
    day_name = service_date.strftime("%a")
    for record in records.values():
        if _schedule_cancelled(snapshot, "bakery", record.id, service_date):
            continue
        ready_by_day = _json_object(record.schedule.get("readyTime"))
        deadline_by_day = _json_object(record.schedule.get("pickupDeadline"))
        if day_name not in _recurring_days(record) and day_name not in ready_by_day:
            continue
        ready, deadline = _period(
            service_date,
            ready_by_day.get(day_name),
            deadline_by_day.get(day_name),
            zone,
        )
        if ready and deadline:
            result.append(BakeryPickup(
                id=f"bakery:{record.id}:recurring:{service_date.isoformat()}",
                bakery_name=record.name,
                location=record.location(),
                ready_at=ready,
                pickup_deadline=deadline,
                food_amount_distribution=record.food_amount_distribution,
                usable_fraction_distribution=record.usable_fraction_distribution,
                waste_allocation=record.waste_allocation or WasteAllocation(),
            ))
    for window in snapshot.availability_windows:
        if window.get("organizationType") != "bakery" or window.get("paused"):
            continue
        record = records.get(_int(window.get("organizationId")))
        if record is None:
            continue
        starts = _aware_datetime(window.get("startsAt"))
        ends = _aware_datetime(window.get("endsAt"))
        if starts is None or ends is None or starts.astimezone(zone).date() != service_date:
            continue
        result.append(BakeryPickup(
            id=f"bakery:{record.id}:one-time:{window.get('id')}",
            bakery_name=record.name,
            location=record.location(),
            ready_at=starts,
            pickup_deadline=ends,
            food_amount_distribution=record.food_amount_distribution,
            usable_fraction_distribution=record.usable_fraction_distribution,
            waste_allocation=record.waste_allocation or WasteAllocation(),
        ))
    return sorted(result, key=lambda item: (item.ready_at, item.id))


def _pantry_windows(
    snapshot: NetworkSnapshot,
    service_date: date,
    zone: ZoneInfo,
) -> list[tuple[Pantry, str]]:
    records = {record.id: record for record in snapshot.eligible_pantries}
    result: list[tuple[Pantry, str]] = []
    day_name = service_date.strftime("%a")
    for record in records.values():
        if _schedule_cancelled(snapshot, "pantry", record.id, service_date):
            continue
        opens = _json_list(record.schedule.get("openTime"))
        closes = _json_list(record.schedule.get("closeTime"))
        arrivals = _json_list(record.schedule.get("latestPermittedArrival"))
        modes = _json_list(record.schedule.get("serviceModes"))
        for index, opening in enumerate(opens):
            if not isinstance(opening, dict) or opening.get("day") != day_name:
                continue
            if index >= len(closes) or index >= len(arrivals):
                continue
            if opening.get("recurrence") == "monthly" and _int(opening.get("ordinal")) != _ordinal(service_date):
                continue
            close = closes[index] if isinstance(closes[index], dict) else {}
            arrival = arrivals[index] if isinstance(arrivals[index], dict) else {}
            starts, ends = _period(
                service_date,
                opening.get("time"),
                close.get("time"),
                zone,
            )
            latest = _local_datetime(service_date, arrival.get("time"), zone)
            if starts is None or ends is None or latest is None:
                continue
            if latest < starts:
                latest += timedelta(days=1)
            mode_item = modes[index] if index < len(modes) and isinstance(modes[index], dict) else {}
            mode = str(mode_item.get("mode") or "staffed")
            result.append((Pantry(
                id=f"pantry:{record.id}:recurring:{service_date.isoformat()}:{index}",
                pantry_name=record.name,
                location=record.location(),
                receiving_start=starts,
                receiving_end=ends,
                latest_permitted_arrival=min(latest, ends),
                priority_score=0.5,
                distribution_fraction=(
                    record.pantry_distribution_fraction
                    if record.pantry_distribution_fraction is not None
                    else 1.0
                ),
                waste_allocation=(
                    record.waste_allocation
                    or WasteAllocation(landfill=0.40, pig_farm=0.60, compost=0.0)
                ),
            ), mode))
    for window in snapshot.availability_windows:
        if window.get("organizationType") != "pantry" or window.get("paused"):
            continue
        record = records.get(_int(window.get("organizationId")))
        if record is None:
            continue
        starts = _aware_datetime(window.get("startsAt"))
        ends = _aware_datetime(window.get("endsAt"))
        latest = _aware_datetime(window.get("latestArrival"))
        if (
            starts is None or ends is None or latest is None
            or starts.astimezone(zone).date() != service_date
        ):
            continue
        result.append((Pantry(
            id=f"pantry:{record.id}:one-time:{window.get('id')}",
            pantry_name=record.name,
            location=record.location(),
            receiving_start=starts,
            receiving_end=ends,
            latest_permitted_arrival=min(latest, ends),
            priority_score=0.5,
            distribution_fraction=(
                record.pantry_distribution_fraction
                if record.pantry_distribution_fraction is not None
                else 1.0
            ),
            waste_allocation=(
                record.waste_allocation
                or WasteAllocation(landfill=0.40, pig_farm=0.60, compost=0.0)
            ),
        ), str(window.get("serviceMode") or "staffed")))
    return sorted(result, key=lambda item: (item[0].receiving_start, item[0].id))


def _sample_food_availability(
    scheduled: list[BakeryPickup],
    probability: float,
    rng: random.Random,
    *,
    random_seed: int = 0,
) -> tuple[list[BakeryPickup], tuple[SimulationEvent, ...]]:
    available: list[BakeryPickup] = []
    events: list[SimulationEvent] = []
    for pickup in scheduled:
        digest = hashlib.sha256(
            f"{random_seed}|bakery-surplus|{pickup.id}".encode()
        ).digest()
        pickup_rng = random.Random(int.from_bytes(digest[:8], "big"))
        has_food = pickup_rng.random() < probability
        if has_food:
            food_distribution = pickup.food_amount_distribution or (
                TriangularDistribution(8.0, 18.0, 28.0)
            )
            usability_distribution = pickup.usable_fraction_distribution or (
                TriangularDistribution(0.65, 0.80, 0.95)
            )
            estimated_food_kg = round(
                pickup_rng.triangular(
                    food_distribution.minimum,
                    food_distribution.maximum,
                    food_distribution.mode,
                ),
                2,
            )
            usable_fraction = round(
                pickup_rng.triangular(
                    usability_distribution.minimum,
                    usability_distribution.maximum,
                    usability_distribution.mode,
                ),
                3,
            )
            pickup = replace(
                pickup,
                estimated_food_kg=estimated_food_kg,
                usable_fraction=usable_fraction,
            )
            available.append(pickup)
        events.append(SimulationEvent(
            occurred_at=pickup.ready_at,
            kind="bakery_food_available" if has_food else "bakery_no_surplus",
            organization_type="bakery",
            organization_id=pickup.id,
            organization_name=pickup.bakery_name,
            detail=(
                (
                    f"Synthetic surplus: {pickup.estimated_food_kg:.1f} kg, "
                    f"{pickup.usable_fraction:.0%} usable."
                )
                if has_food else "No surplus was generated for this schedule occurrence."
            ),
        ))
    return available, tuple(events)


def _sample_pantry_openings(
    scheduled: list[tuple[Pantry, str]],
    staffed_probability: float,
    rng: random.Random,
) -> tuple[list[Pantry], tuple[SimulationEvent, ...]]:
    open_windows: list[Pantry] = []
    events: list[SimulationEvent] = []
    for pantry, service_mode in scheduled:
        is_open = service_mode == "unattended" or rng.random() < staffed_probability
        if is_open:
            open_windows.append(pantry)
        events.append(SimulationEvent(
            occurred_at=pantry.receiving_start,
            kind="pantry_window_open" if is_open else "pantry_staff_unavailable",
            organization_type="pantry",
            organization_id=pantry.id,
            organization_name=pantry.pantry_name,
            detail=(
                f"{service_mode.title()} receiving window is available."
                if is_open else "The synthetic staffed-attendance event was unavailable."
            ),
        ))
    return open_windows, tuple(events)


def _synthetic_driver_requests(
    pickups: list[BakeryPickup],
    pantries: list[Pantry],
    count: int,
    rng: random.Random,
) -> list[DriverRequest]:
    if not pickups or not pantries:
        return []
    locations = [pickup.location for pickup in pickups] + [pantry.location for pantry in pantries]
    center_latitude = sum(item.latitude for item in locations) / len(locations)
    center_longitude = sum(item.longitude for item in locations) / len(locations)
    earliest = min(item.ready_at for item in pickups) - timedelta(minutes=45)
    latest = max(item.receiving_end for item in pantries) + timedelta(minutes=15)
    requests: list[DriverRequest] = []
    for index in range(count):
        start = Location(
            address_entered=f"synthetic-driver-{index + 1}-origin",
            formatted_address="Synthetic Boston-area origin",
            latitude=center_latitude + rng.uniform(-0.025, 0.025),
            longitude=center_longitude + rng.uniform(-0.035, 0.035),
            validation_status=AddressValidationStatus.VALIDATED,
        )
        preferred: Location | None = None
        if rng.random() < 0.65:
            anchor = rng.choice(pantries).location
            preferred = Location(
                address_entered=f"synthetic-driver-{index + 1}-destination",
                formatted_address="Synthetic preferred destination",
                latitude=anchor.latitude + rng.uniform(-0.01, 0.01),
                longitude=anchor.longitude + rng.uniform(-0.01, 0.01),
                validation_status=AddressValidationStatus.VALIDATED,
            )
        requests.append(DriverRequest(
            id=f"request-{earliest.date().isoformat()}-{index + 1}",
            driver_id=f"synthetic-driver-{index + 1}",
            earliest_start=earliest,
            latest_finish=latest,
            start_location=start,
            preferred_destination=preferred,
        ))
    return requests


def _recurring_days(record: OrganizationRecord) -> set[str]:
    return {
        item.strip() for item in str(record.schedule.get("recurringDays") or "").split(",")
        if item.strip()
    }


def _schedule_cancelled(
    snapshot: NetworkSnapshot,
    organization_type: str,
    organization_id: int,
    service_date: date,
) -> bool:
    return any(
        item.get("organizationType") == organization_type
        and _int(item.get("organizationId")) == organization_id
        and item.get("exceptionDate") == service_date.isoformat()
        for item in snapshot.schedule_exceptions
    )


def _period(
    service_date: date,
    starts_value: Any,
    ends_value: Any,
    zone: ZoneInfo,
) -> tuple[datetime | None, datetime | None]:
    starts = _local_datetime(service_date, starts_value, zone)
    ends = _local_datetime(service_date, ends_value, zone)
    if starts is not None and ends is not None and ends <= starts:
        ends += timedelta(days=1)
    return starts, ends


def _local_datetime(service_date: date, value: Any, zone: ZoneInfo) -> datetime | None:
    if not value:
        return None
    try:
        parsed = time.fromisoformat(str(value))
    except ValueError:
        return None
    return datetime.combine(service_date, parsed, tzinfo=zone)


def _aware_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(str(value or "{}"))
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _json_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    try:
        parsed = json.loads(str(value or "[]"))
    except json.JSONDecodeError:
        return []
    return parsed if isinstance(parsed, list) else []


def _ordinal(value: date) -> int:
    return (value.day - 1) // 7 + 1


def _organization_id(value: str) -> int:
    parts = value.split(":")
    if len(parts) < 2:
        raise ValueError(f"Simulation occurrence has no organization id: {value}")
    return int(parts[1])


def _int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return -1
