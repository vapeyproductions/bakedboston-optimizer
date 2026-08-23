from __future__ import annotations

import csv
import hashlib
import json
import math
import platform
import random
import sys
import time as clock
from collections import defaultdict, deque
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Any, Iterable, Sequence
from zoneinfo import ZoneInfo

from .models import (
    AddressValidationStatus,
    AssignmentCandidate,
    BakeryPickup,
    DriverRequest,
    Location,
    Pantry,
    SolverDiagnostics,
)
from .network import NetworkSnapshot
from .optimizer import (
    OptimizationWeights,
    active_solver_backend,
    enumerate_assignment_candidates,
    optimize_assignment_candidates,
    solver_version,
)
from .simulation import (
    SimulationConfig,
    _bakery_windows,
    _organization_id,
    _pantry_windows,
    _sample_food_availability,
    _sample_pantry_openings,
    pantry_priority,
)
from .travel import HaversineTravelTimeProvider, TravelTimeProvider


class RoutingPolicy(StrEnum):
    """Routing strategies evaluated on the same feasible candidate set."""

    BAKEDBOSTON_MIP = "bakedboston_mip"
    RANDOM_FEASIBLE = "random_feasible"
    SHORTEST_ROUTE = "shortest_route"
    EARLIEST_DEADLINE = "earliest_deadline"
    HIGHEST_PRIORITY = "highest_priority"
    DRIVER_FIT = "driver_fit"


DEFAULT_POLICIES: tuple[RoutingPolicy, ...] = tuple(RoutingPolicy)


@dataclass(frozen=True)
class ExperimentConfig:
    """Controls the rolling-horizon experiment and synthetic acceptance model."""

    simulation: SimulationConfig
    matching_interval_minutes: int = 15
    acceptance_enabled: bool = True
    acceptance_intercept: float = 2.2
    acceptance_drive_penalty: float = 0.045
    acceptance_wait_penalty: float = 0.030
    acceptance_destination_penalty: float = 0.035
    max_simultaneous_drivers: int = 3

    def __post_init__(self) -> None:
        if self.matching_interval_minutes < 1:
            raise ValueError("matching_interval_minutes must be at least 1")
        if self.max_simultaneous_drivers not in (2, 3):
            raise ValueError("max_simultaneous_drivers must be 2 or 3")
        for name, value in (
            ("acceptance_drive_penalty", self.acceptance_drive_penalty),
            ("acceptance_wait_penalty", self.acceptance_wait_penalty),
            ("acceptance_destination_penalty", self.acceptance_destination_penalty),
        ):
            if value < 0:
                raise ValueError(f"{name} cannot be negative")


@dataclass(frozen=True)
class ScenarioDay:
    service_date: date
    scheduled_pickup_count: int
    pickups: tuple[BakeryPickup, ...]
    scheduled_pantry_count: int
    pantries: tuple[Pantry, ...]
    requests: tuple[DriverRequest, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "serviceDate": self.service_date.isoformat(),
            "scheduledPickupWindows": self.scheduled_pickup_count,
            "foodAvailablePickups": len(self.pickups),
            "scheduledPantryWindows": self.scheduled_pantry_count,
            "openPantryWindows": len(self.pantries),
            "syntheticDriverRequests": len(self.requests),
        }


@dataclass(frozen=True)
class ExperimentScenario:
    config: ExperimentConfig
    days: tuple[ScenarioDay, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "seed": self.config.simulation.random_seed,
            "startDate": self.config.simulation.start_date.isoformat(),
            "horizonDays": self.config.simulation.days,
            "timezone": self.config.simulation.timezone,
            "matchingIntervalMinutes": self.config.matching_interval_minutes,
            "maxSimultaneousDrivers": self.config.max_simultaneous_drivers,
            "acceptanceEnabled": self.config.acceptance_enabled,
            "driversPerDay": self.config.simulation.drivers_per_day,
            "bakeryFoodProbability": self.config.simulation.bakery_food_probability,
            "staffedPantryOpenProbability": (
                self.config.simulation.staffed_pantry_open_probability
            ),
            "pantryHistorySize": self.config.simulation.pantry_history_size,
            "days": [day.as_dict() for day in self.days],
        }


@dataclass(frozen=True)
class ExperimentAssignment:
    service_date: date
    epoch: datetime
    request_id: str
    driver_id: str
    pickup_id: str
    bakery_name: str
    pantry_window_id: str
    pantry_name: str
    depart_at: datetime
    finish_at: datetime
    drive_minutes: float
    waiting_minutes: float
    destination_minutes: float
    pantry_priority: float
    route_score: float
    acceptance_probability: float
    distance_miles: float
    total_trip_minutes: float
    accepted: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "serviceDate": self.service_date.isoformat(),
            "decisionEpoch": self.epoch.isoformat(),
            "requestId": self.request_id,
            "driverId": self.driver_id,
            "pickupId": self.pickup_id,
            "bakeryName": self.bakery_name,
            "pantryWindowId": self.pantry_window_id,
            "pantryName": self.pantry_name,
            "departAt": self.depart_at.isoformat(),
            "finishAt": self.finish_at.isoformat(),
            "driveMinutes": round(self.drive_minutes, 3),
            "waitingMinutes": round(self.waiting_minutes, 3),
            "destinationMinutes": round(self.destination_minutes, 3),
            "pantryPriority": round(self.pantry_priority, 4),
            "routeScore": round(self.route_score, 4),
            "acceptanceProbability": round(self.acceptance_probability, 4),
            "distanceMiles": round(self.distance_miles, 3),
            "totalTripMinutes": round(self.total_trip_minutes, 3),
            "accepted": self.accepted,
        }


@dataclass(frozen=True)
class ExperimentCandidate:
    """One feasible driver-bakery-pantry option considered at an epoch."""

    request_id: str
    driver_id: str
    pickup_id: str
    bakery_name: str
    pantry_window_id: str
    pantry_name: str
    depart_at: datetime
    finish_at: datetime
    drive_minutes: float
    waiting_minutes: float
    destination_minutes: float
    pantry_priority: float
    route_score: float
    acceptance_probability: float
    distance_miles: float
    total_trip_minutes: float
    driver_start: tuple[float, float]
    bakery_location: tuple[float, float]
    pantry_location: tuple[float, float]
    recommendation_rank: int | None
    selected: bool
    accepted: bool | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "requestId": self.request_id,
            "driverId": self.driver_id,
            "pickupId": self.pickup_id,
            "bakeryName": self.bakery_name,
            "pantryWindowId": self.pantry_window_id,
            "pantryName": self.pantry_name,
            "departAt": self.depart_at.isoformat(),
            "finishAt": self.finish_at.isoformat(),
            "driveMinutes": round(self.drive_minutes, 3),
            "waitingMinutes": round(self.waiting_minutes, 3),
            "destinationMinutes": round(self.destination_minutes, 3),
            "pantryPriority": round(self.pantry_priority, 4),
            "routeScore": round(self.route_score, 4),
            "acceptanceProbability": round(self.acceptance_probability, 4),
            "distanceMiles": round(self.distance_miles, 3),
            "totalTripMinutes": round(self.total_trip_minutes, 3),
            "driverStart": _coordinate_dict(self.driver_start),
            "bakeryLocation": _coordinate_dict(self.bakery_location),
            "pantryLocation": _coordinate_dict(self.pantry_location),
            "recommended": self.recommendation_rank is not None,
            "recommendationRank": self.recommendation_rank,
            "selected": self.selected,
            "accepted": self.accepted,
        }


@dataclass(frozen=True)
class DecisionEpochResult:
    """Auditable candidate and selection trace for one rolling-horizon solve."""

    epoch: datetime
    requests: tuple[DriverRequest, ...]
    candidates: tuple[ExperimentCandidate, ...]
    diagnostics: SolverDiagnostics

    def as_dict(self) -> dict[str, Any]:
        driver_recommendations = []
        for request in self.requests:
            routes = sorted(
                (
                    item
                    for item in self.candidates
                    if item.request_id == request.id
                    and item.recommendation_rank is not None
                ),
                key=lambda item: item.recommendation_rank or 999,
            )
            selected = next((item for item in routes if item.selected), None)
            driver_recommendations.append({
                "requestId": request.id,
                "driverId": request.driver_id,
                "routes": [item.as_dict() for item in routes],
                "selectedRoute": selected.as_dict() if selected else None,
            })
        return {
            "decisionEpoch": self.epoch.isoformat(),
            "requests": [
                {
                    "requestId": request.id,
                    "driverId": request.driver_id,
                    "earliestStart": request.earliest_start.isoformat(),
                    "latestFinish": request.latest_finish.isoformat(),
                    "hasPreferredDestination": request.preferred_destination is not None,
                    "startLocation": {
                        "latitude": request.start_location.latitude,
                        "longitude": request.start_location.longitude,
                    },
                    "preferredDestination": (
                        {
                            "latitude": request.preferred_destination.latitude,
                            "longitude": request.preferred_destination.longitude,
                        }
                        if request.preferred_destination is not None
                        else None
                    ),
                }
                for request in self.requests
            ],
            "queueSize": len(self.requests),
            "candidateCount": len(self.candidates),
            "selectedCount": sum(item.selected for item in self.candidates),
            "acceptedCount": sum(item.accepted is True for item in self.candidates),
            "driverRecommendations": driver_recommendations,
            "candidates": [item.as_dict() for item in self.candidates],
            "solver": _solver_dict(self.diagnostics),
        }


@dataclass(frozen=True)
class PolicyDayResult:
    service_date: date
    scheduled_pickup_windows: int
    food_available_pickups: int
    open_pantry_windows: int
    driver_requests: int
    feasible_candidates: int
    offers: tuple[ExperimentAssignment, ...]
    solver_runs: tuple[SolverDiagnostics, ...]
    decision_epochs: tuple[DecisionEpochResult, ...]

    @property
    def completed(self) -> tuple[ExperimentAssignment, ...]:
        return tuple(item for item in self.offers if item.accepted)

    def as_dict(self) -> dict[str, Any]:
        return {
            "serviceDate": self.service_date.isoformat(),
            "scheduledPickupWindows": self.scheduled_pickup_windows,
            "foodAvailablePickups": self.food_available_pickups,
            "openPantryWindows": self.open_pantry_windows,
            "driverRequests": self.driver_requests,
            "feasibleCandidatesEvaluated": self.feasible_candidates,
            "routesOffered": len(self.offers),
            "routesAccepted": len(self.completed),
            "offers": [item.as_dict() for item in self.offers],
            "solverRuns": [_solver_dict(item) for item in self.solver_runs],
            "decisionEpochs": [item.as_dict() for item in self.decision_epochs],
        }


@dataclass(frozen=True)
class PolicyReport:
    policy: RoutingPolicy
    config: ExperimentConfig
    days: tuple[PolicyDayResult, ...]
    pantry_opportunities: dict[str, dict[str, int]]

    def metrics(self) -> dict[str, Any]:
        offers = [item for day in self.days for item in day.offers]
        completed = [item for item in offers if item.accepted]
        food_pickups = sum(day.food_available_pickups for day in self.days)
        open_pantries = sum(day.open_pantry_windows for day in self.days)
        pantry_counts = [item["served"] for item in self.pantry_opportunities.values()]
        never_served = sum(1 for count in pantry_counts if count == 0)
        available_pantry_count = len(pantry_counts)
        unique_pantries_served = len({item.pantry_name for item in completed})
        service_gap = (max(pantry_counts) - min(pantry_counts)) if pantry_counts else 0
        runtimes = [run.runtime_seconds for day in self.days for run in day.solver_runs]
        gaps = [run.mip_gap for day in self.days for run in day.solver_runs if run.mip_gap is not None]
        expected_acceptances = sum(item.acceptance_probability for item in offers)
        likely_rejections = sum(item.acceptance_probability < 0.5 for item in offers)
        total_route_quality = sum(item.route_score for item in completed)
        return {
            "scheduledPickupWindows": sum(day.scheduled_pickup_windows for day in self.days),
            "eligibleBakeryPickupOccurrences": food_pickups,
            "foodAvailablePickups": food_pickups,
            "completedDeliveries": len(completed),
            "bakeryPickupCoverage": _ratio(len(completed), food_pickups),
            "pickupCoverage": _ratio(len(completed), food_pickups),
            "unservedPickups": max(0, food_pickups - len(completed)),
            "routesOffered": len(offers),
            "routesRejected": len(offers) - len(completed),
            "driverAcceptanceRate": _ratio(len(completed), len(offers)),
            "acceptanceRate": _ratio(len(completed), len(offers)),
            "expectedDriverAcceptanceRate": round(
                expected_acceptances / len(offers), 4
            ) if offers else 0.0,
            "expectedRejectedOffers": round(len(offers) - expected_acceptances, 3),
            "offersLikelyRejected": likely_rejections,
            "likelyRejectionRate": _ratio(likely_rejections, len(offers)),
            "feasibleCandidatesEvaluated": sum(day.feasible_candidates for day in self.days),
            "openPantryWindows": open_pantries,
            "availablePantries": available_pantry_count,
            "uniquePantriesServed": unique_pantries_served,
            "pantryCoverageCount": unique_pantries_served,
            "pantryCoveragePercentage": _ratio(
                unique_pantries_served, available_pantry_count
            ),
            "pantriesNeverServedCount": never_served,
            "pantriesNeverServedPercentage": _ratio(
                never_served, available_pantry_count
            ),
            "fractionOpenPantriesNeverServed": _ratio(never_served, len(pantry_counts)),
            "pantryServiceGini": round(gini(pantry_counts), 4),
            "pantryServiceGap": service_gap,
            "averageDriveMinutes": _mean(item.drive_minutes for item in completed),
            "averageDistanceMiles": _mean(item.distance_miles for item in completed),
            "averageWaitingMinutes": _mean(item.waiting_minutes for item in completed),
            "averageDestinationMinutes": _mean(item.destination_minutes for item in completed),
            "averageTotalTripDurationMinutes": _mean(
                item.total_trip_minutes for item in completed
            ),
            "averageRouteBurdenMinutes": _mean(
                item.total_trip_minutes for item in completed
            ),
            "averagePantryPriorityServed": _mean(
                (item.pantry_priority for item in completed),
                digits=4,
            ),
            "systemObjectiveValue": round(total_route_quality, 4),
            "totalRouteQuality": round(total_route_quality, 4),
            "averageRouteQuality": _mean(
                (item.route_score for item in completed),
                digits=4,
            ),
            "totalSolverRuntimeSeconds": round(sum(runtimes), 6),
            "averageSolverRuntimeSeconds": _mean(runtimes, digits=6),
            "averageOptimalityGap": _mean(gaps, digits=6) if gaps else None,
            "maximumMipGap": round(max(gaps), 6) if gaps else None,
        }

    def as_dict(self) -> dict[str, Any]:
        return {
            "policy": self.policy.value,
            "metrics": self.metrics(),
            "pantryOpportunities": self.pantry_opportunities,
            "days": [day.as_dict() for day in self.days],
        }


@dataclass(frozen=True)
class ComparisonReport:
    scenario: ExperimentScenario
    policies: tuple[PolicyReport, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "mode": "academic_rolling_horizon_comparison",
            "runtime": _runtime_metadata(),
            "disclaimer": (
                "All surplus, attendance, driver, offer, acceptance, and delivery events are "
                "synthetic. Institution schedule geometry does not imply participation or affiliation."
            ),
            "scenario": self.scenario.as_dict(),
            "results": [report.as_dict() for report in self.policies],
        }


@dataclass(frozen=True)
class HorizonComparisonReport:
    start_date: date
    horizons: tuple[int, ...]
    seeds: tuple[int, ...]
    runs: tuple[ComparisonReport, ...]

    def summary(self) -> dict[str, Any]:
        grouped: dict[tuple[int, str], list[dict[str, Any]]] = defaultdict(list)
        for comparison in self.runs:
            horizon = comparison.scenario.config.simulation.days
            for report in comparison.policies:
                grouped[(horizon, report.policy.value)].append(report.metrics())
        return {
            str(horizon): {
                policy: _aggregate_metrics(values)
                for (candidate_horizon, policy), values in sorted(grouped.items())
                if candidate_horizon == horizon
            }
            for horizon in self.horizons
        }

    def as_dict(self) -> dict[str, Any]:
        return {
            "mode": "academic_multi_horizon_comparison",
            "runtime": _runtime_metadata(),
            "startDate": self.start_date.isoformat(),
            "horizons": list(self.horizons),
            "seeds": list(self.seeds),
            "summary": self.summary(),
            "runs": [run.as_dict() for run in self.runs],
        }


def build_scenario(
    snapshot: NetworkSnapshot,
    config: ExperimentConfig,
) -> ExperimentScenario:
    """Sample one immutable scenario that every routing policy will share."""

    simulation = config.simulation
    rng = random.Random(simulation.random_seed)
    zone = ZoneInfo(simulation.timezone)
    days: list[ScenarioDay] = []
    for offset in range(simulation.days):
        service_date = simulation.start_date + timedelta(days=offset)
        scheduled_pickups = _bakery_windows(snapshot, service_date, zone)
        pickups, _ = _sample_food_availability(
            scheduled_pickups,
            simulation.bakery_food_probability,
            rng,
        )
        scheduled_pantries = _pantry_windows(snapshot, service_date, zone)
        pantries, _ = _sample_pantry_openings(
            scheduled_pantries,
            simulation.staffed_pantry_open_probability,
            rng,
        )
        requests = _rolling_driver_requests(
            pickups,
            pantries,
            simulation.drivers_per_day,
            config.matching_interval_minutes,
            config.max_simultaneous_drivers,
            rng,
        )
        days.append(ScenarioDay(
            service_date=service_date,
            scheduled_pickup_count=len(scheduled_pickups),
            pickups=tuple(pickups),
            scheduled_pantry_count=len(scheduled_pantries),
            pantries=tuple(pantries),
            requests=tuple(requests),
        ))
    return ExperimentScenario(config=config, days=tuple(days))


def run_policy(
    scenario: ExperimentScenario,
    policy: RoutingPolicy,
    travel: TravelTimeProvider | None = None,
    weights: OptimizationWeights = OptimizationWeights(),
) -> PolicyReport:
    """Run one routing policy over the scenario's virtual event timeline."""

    provider = travel or HaversineTravelTimeProvider()
    history: dict[int, deque[bool]] = defaultdict(
        lambda: deque(maxlen=scenario.config.simulation.pantry_history_size)
    )
    opportunity_totals: dict[str, dict[str, int]] = defaultdict(
        lambda: {"available": 0, "served": 0}
    )
    results: list[PolicyDayResult] = []

    for day in scenario.days:
        available_pickups = {pickup.id: pickup for pickup in day.pickups}
        pickup_by_id = {pickup.id: pickup for pickup in day.pickups}
        pantry_by_id = {pantry.id: pantry for pantry in day.pantries}
        request_by_id = {request.id: request for request in day.requests}
        offers: list[ExperimentAssignment] = []
        diagnostics: list[SolverDiagnostics] = []
        decision_epochs: list[DecisionEpochResult] = []
        candidate_count = 0
        finalized_pantry_windows: set[str] = set()
        served_pantry_windows: set[str] = set()

        for epoch, requests in _request_epochs(day.requests):
            _finalize_pantry_opportunities(
                day.pantries,
                history,
                opportunity_totals,
                served_pantry_windows,
                finalized_pantry_windows,
                before=epoch,
            )
            prioritized_pantries = tuple(
                replace(
                    pantry,
                    priority_score=pantry_priority(
                        tuple(history[_organization_id(pantry.id)])
                    ),
                )
                for pantry in day.pantries
                if pantry.id not in finalized_pantry_windows
            )
            candidates = enumerate_assignment_candidates(
                list(available_pickups.values()),
                list(prioritized_pantries),
                list(requests),
                provider,
                weights=weights,
            )
            candidate_count += len(candidates)
            selected, diagnostic = _select_assignments(
                policy,
                candidates,
                available_pickups,
                scenario.config.simulation.random_seed,
                epoch,
            )
            diagnostics.append(diagnostic)
            selected_keys = {_candidate_key(candidate) for candidate in selected}
            recommendation_ranks = _recommendation_ranks(
                policy,
                candidates,
                selected,
                available_pickups,
                scenario.config.simulation.random_seed,
                epoch,
            )
            acceptance_by_key: dict[tuple[str, str, str], bool] = {}
            for candidate in selected:
                probability = acceptance_probability(candidate, scenario.config)
                accepted = (
                    True if not scenario.config.acceptance_enabled
                    else _stable_uniform(
                        scenario.config.simulation.random_seed,
                        "acceptance",
                        candidate.request_id,
                        candidate.route.bakery_id,
                        candidate.route.pantry_id,
                    ) < probability
                )
                offers.append(_experiment_assignment(
                    day.service_date,
                    epoch,
                    candidate,
                    request_by_id[candidate.request_id],
                    pickup_by_id[candidate.route.bakery_id],
                    pantry_by_id[candidate.route.pantry_id],
                    probability,
                    accepted,
                ))
                acceptance_by_key[_candidate_key(candidate)] = accepted
                if accepted:
                    available_pickups.pop(candidate.route.bakery_id, None)
                    served_pantry_windows.add(candidate.route.pantry_id)
            decision_epochs.append(DecisionEpochResult(
                epoch=epoch,
                requests=tuple(requests),
                candidates=tuple(
                    _experiment_candidate(
                        candidate,
                        request=request_by_id[candidate.request_id],
                        pickup=pickup_by_id[candidate.route.bakery_id],
                        pantry=pantry_by_id[candidate.route.pantry_id],
                        probability=acceptance_probability(
                            candidate, scenario.config
                        ),
                        recommendation_rank=recommendation_ranks.get(
                            _candidate_key(candidate)
                        ),
                        selected=_candidate_key(candidate) in selected_keys,
                        accepted=acceptance_by_key.get(_candidate_key(candidate)),
                    )
                    for candidate in candidates
                ),
                diagnostics=diagnostic,
            ))

        _finalize_pantry_opportunities(
            day.pantries,
            history,
            opportunity_totals,
            served_pantry_windows,
            finalized_pantry_windows,
        )
        results.append(PolicyDayResult(
            service_date=day.service_date,
            scheduled_pickup_windows=day.scheduled_pickup_count,
            food_available_pickups=len(day.pickups),
            open_pantry_windows=len(day.pantries),
            driver_requests=len(day.requests),
            feasible_candidates=candidate_count,
            offers=tuple(offers),
            solver_runs=tuple(diagnostics),
            decision_epochs=tuple(decision_epochs),
        ))

    return PolicyReport(
        policy=policy,
        config=scenario.config,
        days=tuple(results),
        pantry_opportunities=dict(sorted(opportunity_totals.items())),
    )


def _finalize_pantry_opportunities(
    pantries: Sequence[Pantry],
    history: dict[int, deque[bool]],
    opportunity_totals: dict[str, dict[str, int]],
    served_windows: set[str],
    finalized_windows: set[str],
    before: datetime | None = None,
) -> None:
    """Close elapsed receiving opportunities and expose them to future priority scores."""

    for pantry in sorted(
        pantries,
        key=lambda item: (item.latest_permitted_arrival, item.id),
    ):
        if pantry.id in finalized_windows:
            continue
        if before is not None and pantry.latest_permitted_arrival >= before:
            continue
        served = pantry.id in served_windows
        history[_organization_id(pantry.id)].append(served)
        opportunity_totals[pantry.pantry_name]["available"] += 1
        opportunity_totals[pantry.pantry_name]["served"] += int(served)
        finalized_windows.add(pantry.id)


def compare_policies(
    snapshot: NetworkSnapshot,
    config: ExperimentConfig,
    policies: Sequence[RoutingPolicy] = DEFAULT_POLICIES,
    travel: TravelTimeProvider | None = None,
    weights: OptimizationWeights = OptimizationWeights(),
) -> ComparisonReport:
    """Compare policies on one immutable, seeded scenario."""

    scenario = build_scenario(snapshot, config)
    reports = tuple(
        run_policy(scenario, policy, travel=travel, weights=weights)
        for policy in policies
    )
    return ComparisonReport(scenario=scenario, policies=reports)


def compare_horizons(
    snapshot: NetworkSnapshot,
    start_date: date,
    horizons: Sequence[int] = (3, 4, 5),
    seeds: Sequence[int] = (2026,),
    policies: Sequence[RoutingPolicy] = DEFAULT_POLICIES,
    drivers_per_day: int = 8,
    bakery_food_probability: float = 0.75,
    staffed_pantry_open_probability: float = 0.90,
    matching_interval_minutes: int = 15,
    max_simultaneous_drivers: int = 3,
    acceptance_enabled: bool = True,
    travel: TravelTimeProvider | None = None,
    weights: OptimizationWeights = OptimizationWeights(),
) -> HorizonComparisonReport:
    """Run reproducible 3/4/5-day experiments over identical policy inputs."""

    normalized_horizons = tuple(int(item) for item in horizons)
    normalized_seeds = tuple(int(item) for item in seeds)
    if not normalized_horizons or any(item < 1 for item in normalized_horizons):
        raise ValueError("horizons must contain positive day counts")
    if not normalized_seeds:
        raise ValueError("at least one random seed is required")
    runs: list[ComparisonReport] = []
    for horizon in normalized_horizons:
        for seed in normalized_seeds:
            config = ExperimentConfig(
                SimulationConfig(
                    start_date=start_date,
                    days=horizon,
                    random_seed=seed,
                    drivers_per_day=drivers_per_day,
                    bakery_food_probability=bakery_food_probability,
                    staffed_pantry_open_probability=staffed_pantry_open_probability,
                ),
                matching_interval_minutes=matching_interval_minutes,
                acceptance_enabled=acceptance_enabled,
                max_simultaneous_drivers=max_simultaneous_drivers,
            )
            runs.append(compare_policies(
                snapshot,
                config,
                policies=policies,
                travel=travel,
                weights=weights,
            ))
    return HorizonComparisonReport(
        start_date=start_date,
        horizons=normalized_horizons,
        seeds=normalized_seeds,
        runs=tuple(runs),
    )


def acceptance_probability(
    candidate: AssignmentCandidate,
    config: ExperimentConfig,
) -> float:
    """Transparent synthetic driver behavior; this is not a learned model."""

    route = candidate.route
    logit = (
        config.acceptance_intercept
        - config.acceptance_drive_penalty * route.drive_minutes
        - config.acceptance_wait_penalty * route.waiting_minutes
        - config.acceptance_destination_penalty * route.destination_minutes
    )
    return min(0.98, max(0.02, 1.0 / (1.0 + math.exp(-logit))))


def gini(values: Iterable[int | float]) -> float:
    """Return the Gini coefficient, with zero for an empty/all-zero sample."""

    ordered = sorted(max(0.0, float(item)) for item in values)
    if not ordered or sum(ordered) == 0:
        return 0.0
    count = len(ordered)
    weighted = sum((index + 1) * value for index, value in enumerate(ordered))
    return (2 * weighted) / (count * sum(ordered)) - (count + 1) / count


def write_report(report: ComparisonReport | HorizonComparisonReport, path: Path) -> None:
    path.write_text(f"{json.dumps(report.as_dict(), indent=2)}\n")


SUMMARY_CSV_FIELDS: tuple[str, ...] = (
    "bakeryPickupCoverage",
    "completedDeliveries",
    "unservedPickups",
    "pantryCoverageCount",
    "pantryCoveragePercentage",
    "pantriesNeverServedPercentage",
    "pantryServiceGini",
    "pantryServiceGap",
    "averageDriveMinutes",
    "averageDistanceMiles",
    "averageWaitingMinutes",
    "averageTotalTripDurationMinutes",
    "systemObjectiveValue",
    "driverAcceptanceRate",
    "expectedDriverAcceptanceRate",
    "likelyRejectionRate",
    "totalSolverRuntimeSeconds",
    "averageSolverRuntimeSeconds",
    "averageOptimalityGap",
    "maximumMipGap",
    "totalRouteQuality",
)


def write_summary_csv(report: HorizonComparisonReport, path: Path) -> None:
    """Write one analysis-ready row per horizon and routing policy."""

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=("horizonDays", "policy", *SUMMARY_CSV_FIELDS),
        )
        writer.writeheader()
        for horizon, policies in report.summary().items():
            for policy, metrics in policies.items():
                writer.writerow({
                    "horizonDays": int(horizon),
                    "policy": policy,
                    **{field: metrics[field] for field in SUMMARY_CSV_FIELDS},
                })


def _rolling_driver_requests(
    pickups: Sequence[BakeryPickup],
    pantries: Sequence[Pantry],
    count: int,
    interval_minutes: int,
    max_simultaneous_drivers: int,
    rng: random.Random,
) -> list[DriverRequest]:
    if not pickups or not pantries:
        return []
    locations = [item.location for item in pickups] + [item.location for item in pantries]
    center_latitude = sum(item.latitude for item in locations) / len(locations)
    center_longitude = sum(item.longitude for item in locations) / len(locations)
    requests: list[DriverRequest] = []
    epoch_counts: dict[datetime, int] = defaultdict(int)
    offsets = (60, 45, 30, 15, 0)
    for index in range(count):
        pickup_anchor = pickups[index % len(pickups)] if index < len(pickups) else rng.choice(pickups)
        raw_start = pickup_anchor.ready_at - timedelta(minutes=rng.choice(offsets))
        earliest = _floor_epoch(raw_start, interval_minutes)
        while epoch_counts[earliest] >= max_simultaneous_drivers:
            earliest += timedelta(minutes=interval_minutes)
        epoch_counts[earliest] += 1
        latest = earliest + timedelta(minutes=rng.choice((90, 120, 150, 180)))
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
    return sorted(requests, key=lambda item: (item.earliest_start, item.id))


def _floor_epoch(value: datetime, interval_minutes: int) -> datetime:
    minute = value.minute - value.minute % interval_minutes
    return value.replace(minute=minute, second=0, microsecond=0)


def _request_epochs(
    requests: Sequence[DriverRequest],
) -> list[tuple[datetime, tuple[DriverRequest, ...]]]:
    grouped: dict[datetime, list[DriverRequest]] = defaultdict(list)
    for request in requests:
        grouped[request.earliest_start].append(request)
    return [
        (epoch, tuple(sorted(items, key=lambda item: item.id)))
        for epoch, items in sorted(grouped.items())
    ]


def _select_assignments(
    policy: RoutingPolicy,
    candidates: Sequence[AssignmentCandidate],
    pickups: dict[str, BakeryPickup],
    seed: int,
    epoch: datetime,
) -> tuple[tuple[AssignmentCandidate, ...], SolverDiagnostics]:
    if policy == RoutingPolicy.BAKEDBOSTON_MIP:
        result = optimize_assignment_candidates(tuple(candidates))
        return result.assignments, result.diagnostics
    started = clock.perf_counter()
    key = _policy_sort_key(policy, pickups, seed, epoch)
    selected = _greedy_select(candidates, key)
    runtime = clock.perf_counter() - started
    return selected, SolverDiagnostics(
        backend=f"baseline:{policy.value}",
        status="complete",
        candidate_count=len(candidates),
        matched_count=len(selected),
        route_quality=sum(item.route.score for item in selected),
        runtime_seconds=runtime,
    )


def _policy_sort_key(
    policy: RoutingPolicy,
    pickups: dict[str, BakeryPickup],
    seed: int,
    epoch: datetime,
) -> Any:
    """Return a deterministic best-first key for a policy's route list."""

    if policy == RoutingPolicy.BAKEDBOSTON_MIP:
        return lambda item: (
            -item.route.score,
            item.route.finish_at,
            item.route.bakery_id,
            item.route.pantry_id,
        )
    if policy == RoutingPolicy.RANDOM_FEASIBLE:
        return lambda item: _stable_uniform(
            seed,
            "random-policy",
            epoch.isoformat(),
            item.request_id,
            item.route.bakery_id,
            item.route.pantry_id,
        )
    if policy == RoutingPolicy.SHORTEST_ROUTE:
        return lambda item: (
            item.route.drive_minutes + item.route.waiting_minutes,
            item.route.finish_at,
            -item.route.score,
        )
    if policy == RoutingPolicy.EARLIEST_DEADLINE:
        return lambda item: (
            pickups[item.route.bakery_id].pickup_deadline,
            item.route.finish_at,
            item.route.drive_minutes,
        )
    if policy == RoutingPolicy.HIGHEST_PRIORITY:
        return lambda item: (
            -item.route.pantry_priority,
            item.route.drive_minutes,
            item.route.finish_at,
        )
    if policy == RoutingPolicy.DRIVER_FIT:
        return lambda item: (
            item.route.destination_minutes,
            item.route.drive_minutes + item.route.waiting_minutes,
            item.route.finish_at,
        )
    raise ValueError(f"Unsupported routing policy: {policy}")


def _greedy_select(
    candidates: Sequence[AssignmentCandidate],
    key: Any,
) -> tuple[AssignmentCandidate, ...]:
    selected: list[AssignmentCandidate] = []
    requests: set[str] = set()
    drivers: set[str] = set()
    pickups: set[str] = set()
    for candidate in sorted(candidates, key=key):
        if (
            candidate.request_id in requests
            or candidate.driver_id in drivers
            or candidate.route.bakery_id in pickups
        ):
            continue
        selected.append(candidate)
        requests.add(candidate.request_id)
        drivers.add(candidate.driver_id)
        pickups.add(candidate.route.bakery_id)
    return tuple(selected)


def _experiment_assignment(
    service_date: date,
    epoch: datetime,
    candidate: AssignmentCandidate,
    request: DriverRequest,
    pickup: BakeryPickup,
    pantry: Pantry,
    probability: float,
    accepted: bool,
) -> ExperimentAssignment:
    route = candidate.route
    return ExperimentAssignment(
        service_date=service_date,
        epoch=epoch,
        request_id=candidate.request_id,
        driver_id=candidate.driver_id,
        pickup_id=route.bakery_id,
        bakery_name=route.bakery_name,
        pantry_window_id=route.pantry_id,
        pantry_name=route.pantry_name,
        depart_at=route.depart_at,
        finish_at=route.finish_at,
        drive_minutes=route.drive_minutes,
        waiting_minutes=route.waiting_minutes,
        destination_minutes=route.destination_minutes,
        pantry_priority=route.pantry_priority,
        route_score=route.score,
        acceptance_probability=probability,
        distance_miles=_route_distance_miles(request, pickup, pantry),
        total_trip_minutes=(
            route.finish_at - route.depart_at
        ).total_seconds() / 60,
        accepted=accepted,
    )


def _candidate_key(candidate: AssignmentCandidate) -> tuple[str, str, str]:
    return (
        candidate.request_id,
        candidate.route.bakery_id,
        candidate.route.pantry_id,
    )


def _experiment_candidate(
    candidate: AssignmentCandidate,
    *,
    request: DriverRequest,
    pickup: BakeryPickup,
    pantry: Pantry,
    probability: float,
    recommendation_rank: int | None,
    selected: bool,
    accepted: bool | None,
) -> ExperimentCandidate:
    route = candidate.route
    return ExperimentCandidate(
        request_id=candidate.request_id,
        driver_id=candidate.driver_id,
        pickup_id=route.bakery_id,
        bakery_name=route.bakery_name,
        pantry_window_id=route.pantry_id,
        pantry_name=route.pantry_name,
        depart_at=route.depart_at,
        finish_at=route.finish_at,
        drive_minutes=route.drive_minutes,
        waiting_minutes=route.waiting_minutes,
        destination_minutes=route.destination_minutes,
        pantry_priority=route.pantry_priority,
        route_score=route.score,
        acceptance_probability=probability,
        distance_miles=_route_distance_miles(request, pickup, pantry),
        total_trip_minutes=(
            route.finish_at - route.depart_at
        ).total_seconds() / 60,
        driver_start=(
            request.start_location.latitude,
            request.start_location.longitude,
        ),
        bakery_location=(pickup.location.latitude, pickup.location.longitude),
        pantry_location=(pantry.location.latitude, pantry.location.longitude),
        recommendation_rank=recommendation_rank,
        selected=selected,
        accepted=accepted,
    )


def _recommendation_ranks(
    policy: RoutingPolicy,
    candidates: Sequence[AssignmentCandidate],
    selected: Sequence[AssignmentCandidate],
    pickups: dict[str, BakeryPickup],
    seed: int,
    epoch: datetime,
    limit: int = 5,
) -> dict[tuple[str, str, str], int]:
    """Return the conflict-free, ranked routes actually shown to each driver.

    The joint solve may reserve a bakery for another simultaneous driver. Those
    conflicting routes remain in the auditable feasible set, but are not shown
    as selectable recommendations. Within each resulting driver list, the
    selected route is always rank one under the active policy.
    """

    selected_pickup_by_request = {
        item.request_id: item.route.bakery_id for item in selected
    }
    ranks: dict[tuple[str, str, str], int] = {}
    request_ids = sorted({item.request_id for item in candidates})
    for request_id in request_ids:
        pickups_reserved_elsewhere = {
            pickup_id
            for other_request, pickup_id in selected_pickup_by_request.items()
            if other_request != request_id
        }
        eligible = [
            item
            for item in candidates
            if item.request_id == request_id
            and item.route.bakery_id not in pickups_reserved_elsewhere
        ]
        ranked = sorted(
            eligible,
            key=_policy_sort_key(policy, pickups, seed, epoch),
        )
        selected_candidate = next(
            (item for item in selected if item.request_id == request_id),
            None,
        )
        if selected_candidate is not None:
            selected_key = _candidate_key(selected_candidate)
            ranked = [
                selected_candidate,
                *(item for item in ranked if _candidate_key(item) != selected_key),
            ]
        for rank, candidate in enumerate(ranked[:limit], start=1):
            ranks[_candidate_key(candidate)] = rank
    return ranks


def _stable_uniform(seed: int, *parts: str) -> float:
    value = "|".join((str(seed), *parts)).encode("utf-8")
    integer = int.from_bytes(hashlib.sha256(value).digest()[:8], "big")
    return integer / float(2**64)


def _route_distance_miles(
    request: DriverRequest,
    pickup: BakeryPickup,
    pantry: Pantry,
) -> float:
    return _haversine_miles(request.start_location, pickup.location) + _haversine_miles(
        pickup.location, pantry.location
    )


def _haversine_miles(origin: Location, destination: Location) -> float:
    radius_miles = 3958.7613
    origin_latitude = math.radians(origin.latitude)
    destination_latitude = math.radians(destination.latitude)
    latitude_delta = math.radians(destination.latitude - origin.latitude)
    longitude_delta = math.radians(destination.longitude - origin.longitude)
    value = (
        math.sin(latitude_delta / 2) ** 2
        + math.cos(origin_latitude)
        * math.cos(destination_latitude)
        * math.sin(longitude_delta / 2) ** 2
    )
    value = min(1.0, max(0.0, value))
    return radius_miles * 2 * math.atan2(math.sqrt(value), math.sqrt(1 - value))


def _coordinate_dict(coordinates: tuple[float, float]) -> dict[str, float]:
    return {
        "latitude": coordinates[0],
        "longitude": coordinates[1],
    }


def _ratio(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 4) if denominator else 0.0


def _mean(values: Iterable[float], digits: int = 2) -> float:
    materialized = list(values)
    return round(sum(materialized) / len(materialized), digits) if materialized else 0.0


def _solver_dict(diagnostics: SolverDiagnostics) -> dict[str, Any]:
    return {
        "backend": diagnostics.backend,
        "status": diagnostics.status,
        "candidateCount": diagnostics.candidate_count,
        "matchedCount": diagnostics.matched_count,
        "routeQuality": round(diagnostics.route_quality, 4),
        "runtimeSeconds": round(diagnostics.runtime_seconds, 6),
        "mipGap": diagnostics.mip_gap,
    }


def _runtime_metadata() -> dict[str, Any]:
    """Capture solver/runtime identity without adding a nondeterministic timestamp."""

    return {
        "solverBackend": active_solver_backend(),
        "gurobiVersion": solver_version(),
        "pythonVersion": platform.python_version(),
        "pythonImplementation": platform.python_implementation(),
        "pythonMajorMinor": f"{sys.version_info.major}.{sys.version_info.minor}",
    }


def _aggregate_metrics(values: Sequence[dict[str, Any]]) -> dict[str, Any]:
    if not values:
        return {}
    keys = (
        "bakeryPickupCoverage",
        "completedDeliveries",
        "pantryCoverageCount",
        "pantryCoveragePercentage",
        "pantriesNeverServedPercentage",
        "pantryServiceGini",
        "pantryServiceGap",
        "averageDriveMinutes",
        "averageDistanceMiles",
        "averageWaitingMinutes",
        "averageTotalTripDurationMinutes",
        "systemObjectiveValue",
        "driverAcceptanceRate",
        "expectedDriverAcceptanceRate",
        "likelyRejectionRate",
        "unservedPickups",
        "totalSolverRuntimeSeconds",
        "averageSolverRuntimeSeconds",
        "totalRouteQuality",
    )
    aggregated = {
        key: round(sum(float(item[key]) for item in values) / len(values), 4)
        for key in keys
    }
    for key in ("averageOptimalityGap", "maximumMipGap"):
        materialized = [float(item[key]) for item in values if item[key] is not None]
        aggregated[key] = (
            round(sum(materialized) / len(materialized), 6)
            if materialized
            else None
        )
    return aggregated
