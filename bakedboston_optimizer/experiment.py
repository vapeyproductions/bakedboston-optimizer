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

from .environment import DEFAULT_ENVIRONMENTAL_ASSUMPTIONS
from .models import (
    AddressValidationStatus,
    AssignmentCandidate,
    BakeryPickup,
    DriverRequest,
    Location,
    NetworkOptimizationResult,
    Pantry,
    SolverDiagnostics,
)
from .network import NetworkSnapshot
from .optimizer import (
    OptimizationWeights,
    ParticipationModel,
    active_solver_backend,
    allocate_recommendation_layer,
    balanced_objective_value,
    enumerate_assignment_candidates,
    estimate_acceptance_probability,
    optimize_assignment_candidates,
    optimize_horner_slsf_noz_candidates,
    optimize_nair_distance_first_candidates,
    optimize_xue_zou_total_curb_candidates,
    score_assignment_candidates,
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
    NAIR_2018_DISTANCE_FIRST = "nair_2018_distance_first"
    XUE_ZOU_2025_TOTAL_CURB = "xue_zou_2025_total_curb"
    HORNER_2021_SLSF_NOZ = "horner_2021_slsf_noz"
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
    acceptance_predeparture_wait_penalty: float = 0.0
    acceptance_facility_wait_penalty: float = 0.0
    acceptance_requested_time_deviation_penalty: float = 0.0
    acceptance_requested_time_deviation_ratio_penalty: float = 1.8
    # Compatibility field for older saved configs. Destination travel minutes
    # are no longer used; spatial fit is measured as a normalized geographic
    # miss from the requested start/end ZIP areas.
    acceptance_destination_penalty: float = 0.0
    acceptance_spatial_deviation_ratio_penalty: float = 1.1
    max_simultaneous_drivers: int = 3

    def __post_init__(self) -> None:
        if self.matching_interval_minutes < 1:
            raise ValueError("matching_interval_minutes must be at least 1")
        if self.max_simultaneous_drivers not in (2, 3):
            raise ValueError("max_simultaneous_drivers must be 2 or 3")
        for name, value in (
            ("acceptance_drive_penalty", self.acceptance_drive_penalty),
            (
                "acceptance_predeparture_wait_penalty",
                self.acceptance_predeparture_wait_penalty,
            ),
            (
                "acceptance_facility_wait_penalty",
                self.acceptance_facility_wait_penalty,
            ),
            (
                "acceptance_requested_time_deviation_penalty",
                self.acceptance_requested_time_deviation_penalty,
            ),
            (
                "acceptance_requested_time_deviation_ratio_penalty",
                self.acceptance_requested_time_deviation_ratio_penalty,
            ),
            ("acceptance_destination_penalty", self.acceptance_destination_penalty),
            (
                "acceptance_spatial_deviation_ratio_penalty",
                self.acceptance_spatial_deviation_ratio_penalty,
            ),
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
    pickup_at: datetime
    pantry_arrival_at: datetime
    finish_at: datetime
    drive_minutes: float
    waiting_minutes: float
    facility_waiting_minutes: float
    requested_time_deviation_minutes: float
    requested_window_minutes: float
    requested_time_deviation_ratio: float
    within_preferred_window: bool
    destination_minutes: float
    origin_deviation_miles: float
    destination_deviation_miles: float
    normalized_origin_deviation: float
    normalized_destination_deviation: float
    normalized_spatial_deviation: float
    pantry_priority: float
    route_score: float
    acceptance_probability: float
    distance_miles: float
    total_trip_minutes: float
    estimated_food_kg: float
    usable_food_kg: float
    bakery_usable_fraction: float
    pantry_distribution_fraction: float
    food_saved_kg: float
    collected_not_distributed_kg: float
    bakery_unusable_food_kg: float
    pantry_undistributed_food_kg: float
    bakery_route_waste_kg_co2e: float
    pantry_route_waste_kg_co2e: float
    pantry_landfill_fraction: float
    pantry_pig_farm_fraction: float
    avoided_system_kg_co2e: float
    transport_kg_co2e: float
    residual_waste_kg_co2e: float
    net_environmental_benefit_kg_co2e: float
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
            "pickupAt": self.pickup_at.isoformat(),
            "pantryArrivalAt": self.pantry_arrival_at.isoformat(),
            "finishAt": self.finish_at.isoformat(),
            "driveMinutes": round(self.drive_minutes, 3),
            "waitingMinutes": round(self.waiting_minutes, 3),
            "predepartureWaitMinutes": round(self.waiting_minutes, 3),
            "requestedTimeDeviationMinutes": round(
                self.requested_time_deviation_minutes, 3
            ),
            "outsideRequestedWindowMinutes": round(
                self.requested_time_deviation_minutes, 3
            ),
            "requestedWindowMinutes": round(self.requested_window_minutes, 3),
            "outsideRequestedWindowRatio": round(
                self.requested_time_deviation_ratio, 4
            ),
            "outsideRequestedWindowPercent": round(
                100 * self.requested_time_deviation_ratio, 2
            ),
            "withinPreferredWindow": self.within_preferred_window,
            "destinationMinutes": round(self.destination_minutes, 3),
            "originDeviationMiles": round(self.origin_deviation_miles, 3),
            "destinationDeviationMiles": round(self.destination_deviation_miles, 3),
            "normalizedOriginDeviation": round(self.normalized_origin_deviation, 4),
            "normalizedDestinationDeviation": round(
                self.normalized_destination_deviation, 4
            ),
            "normalizedSpatialDeviation": round(
                self.normalized_spatial_deviation, 4
            ),
            "spatialDeviationPercent": round(
                100 * self.normalized_spatial_deviation, 2
            ),
            "pantryPriority": round(self.pantry_priority, 4),
            "routeScore": round(self.route_score, 4),
            "acceptanceProbability": round(self.acceptance_probability, 4),
            "distanceMiles": round(self.distance_miles, 3),
            "totalTripMinutes": round(self.total_trip_minutes, 3),
            "bakeryUsableFraction": round(self.bakery_usable_fraction, 4),
            "pantryDistributionFraction": round(self.pantry_distribution_fraction, 4),
            "foodSavedKg": round(self.food_saved_kg, 3),
            "collectedNotDistributedKg": round(self.collected_not_distributed_kg, 3),
            "bakeryUnusableFoodKg": round(self.bakery_unusable_food_kg, 3),
            "pantryUndistributedFoodKg": round(
                self.pantry_undistributed_food_kg, 3
            ),
            "bakeryRouteWasteKgCO2e": round(
                self.bakery_route_waste_kg_co2e, 4
            ),
            "pantryRouteWasteKgCO2e": round(
                self.pantry_route_waste_kg_co2e, 4
            ),
            "pantryLandfillFraction": round(self.pantry_landfill_fraction, 4),
            "pantryPigFarmFraction": round(self.pantry_pig_farm_fraction, 4),
            "netEnvironmentalBenefitKgCO2e": round(
                self.net_environmental_benefit_kg_co2e, 3
            ),
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
    pickup_at: datetime
    pantry_arrival_at: datetime
    finish_at: datetime
    drive_minutes: float
    waiting_minutes: float
    facility_waiting_minutes: float
    requested_time_deviation_minutes: float
    requested_window_minutes: float
    requested_time_deviation_ratio: float
    within_preferred_window: bool
    destination_minutes: float
    origin_deviation_miles: float
    destination_deviation_miles: float
    normalized_origin_deviation: float
    normalized_destination_deviation: float
    normalized_spatial_deviation: float
    pantry_priority: float
    route_score: float
    acceptance_probability: float
    distance_miles: float
    total_trip_minutes: float
    estimated_food_kg: float
    usable_food_kg: float
    bakery_usable_fraction: float
    pantry_distribution_fraction: float
    food_saved_kg: float
    collected_not_distributed_kg: float
    bakery_unusable_food_kg: float
    pantry_undistributed_food_kg: float
    bakery_route_waste_kg_co2e: float
    pantry_route_waste_kg_co2e: float
    pantry_landfill_fraction: float
    pantry_pig_farm_fraction: float
    avoided_system_kg_co2e: float
    transport_kg_co2e: float
    residual_waste_kg_co2e: float
    net_environmental_benefit_kg_co2e: float
    driver_start: tuple[float, float]
    bakery_location: tuple[float, float]
    pantry_location: tuple[float, float]
    bakery_postal_code: str
    pantry_postal_code: str
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
            "pickupAt": self.pickup_at.isoformat(),
            "pantryArrivalAt": self.pantry_arrival_at.isoformat(),
            "finishAt": self.finish_at.isoformat(),
            "driveMinutes": round(self.drive_minutes, 3),
            "waitingMinutes": round(self.waiting_minutes, 3),
            "predepartureWaitMinutes": round(self.waiting_minutes, 3),
            "requestedTimeDeviationMinutes": round(
                self.requested_time_deviation_minutes, 3
            ),
            "outsideRequestedWindowMinutes": round(
                self.requested_time_deviation_minutes, 3
            ),
            "requestedWindowMinutes": round(self.requested_window_minutes, 3),
            "outsideRequestedWindowRatio": round(
                self.requested_time_deviation_ratio, 4
            ),
            "outsideRequestedWindowPercent": round(
                100 * self.requested_time_deviation_ratio, 2
            ),
            "withinPreferredWindow": self.within_preferred_window,
            "destinationMinutes": round(self.destination_minutes, 3),
            "originDeviationMiles": round(self.origin_deviation_miles, 3),
            "destinationDeviationMiles": round(self.destination_deviation_miles, 3),
            "normalizedOriginDeviation": round(self.normalized_origin_deviation, 4),
            "normalizedDestinationDeviation": round(
                self.normalized_destination_deviation, 4
            ),
            "normalizedSpatialDeviation": round(
                self.normalized_spatial_deviation, 4
            ),
            "spatialDeviationPercent": round(
                100 * self.normalized_spatial_deviation, 2
            ),
            "pantryPriority": round(self.pantry_priority, 4),
            "routeScore": round(self.route_score, 4),
            "acceptanceProbability": round(self.acceptance_probability, 4),
            "distanceMiles": round(self.distance_miles, 3),
            "totalTripMinutes": round(self.total_trip_minutes, 3),
            "bakeryUsableFraction": round(self.bakery_usable_fraction, 4),
            "pantryDistributionFraction": round(self.pantry_distribution_fraction, 4),
            "foodSavedKg": round(self.food_saved_kg, 3),
            "collectedNotDistributedKg": round(self.collected_not_distributed_kg, 3),
            "bakeryUnusableFoodKg": round(self.bakery_unusable_food_kg, 3),
            "pantryUndistributedFoodKg": round(
                self.pantry_undistributed_food_kg, 3
            ),
            "bakeryRouteWasteKgCO2e": round(
                self.bakery_route_waste_kg_co2e, 4
            ),
            "pantryRouteWasteKgCO2e": round(
                self.pantry_route_waste_kg_co2e, 4
            ),
            "pantryLandfillFraction": round(self.pantry_landfill_fraction, 4),
            "pantryPigFarmFraction": round(self.pantry_pig_farm_fraction, 4),
            "netEnvironmentalBenefitKgCO2e": round(
                self.net_environmental_benefit_kg_co2e, 3
            ),
            "driverStart": _coordinate_dict(self.driver_start),
            "bakeryLocation": _coordinate_dict(self.bakery_location),
            "pantryLocation": _coordinate_dict(self.pantry_location),
            "bakeryPostalCode": self.bakery_postal_code,
            "pantryPostalCode": self.pantry_postal_code,
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
                    "loggedAt": request.login_time.isoformat(),
                    "preferredStart": request.preferred_start.isoformat(),
                    "preferredFinish": request.preferred_finish.isoformat(),
                    "requestedWindowMinutes": round(
                        (request.preferred_finish - request.preferred_start).total_seconds() / 60,
                        3,
                    ),
                    "searchUntil": request.hard_search_end.isoformat(),
                    "earliestStart": request.earliest_start.isoformat(),
                    "latestFinish": request.latest_finish.isoformat(),
                    "hasPreferredDestination": request.preferred_destination is not None,
                    "startLocation": {
                        "formattedAddress": request.start_location.formatted_address,
                        "latitude": request.start_location.latitude,
                        "longitude": request.start_location.longitude,
                        "postalCode": (
                            request.start_location.postal_code
                            or request.start_zip_code
                        ),
                    },
                    "startZipCode": (
                        request.start_zip_code or request.start_location.postal_code
                    ),
                    "startRadiusMiles": request.start_radius_miles,
                    "preferredDestination": (
                        {
                            "formattedAddress": (
                                request.preferred_destination.formatted_address
                            ),
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
    pickup_windows: tuple[BakeryPickup, ...]
    pantry_windows: tuple[Pantry, ...]

    @property
    def completed(self) -> tuple[ExperimentAssignment, ...]:
        return tuple(item for item in self.offers if item.accepted)

    def as_dict(self) -> dict[str, Any]:
        completed_pickup_ids = {item.pickup_id for item in self.completed}
        return {
            "serviceDate": self.service_date.isoformat(),
            "scheduledPickupWindows": self.scheduled_pickup_windows,
            "foodAvailablePickups": self.food_available_pickups,
            "openPantryWindows": self.open_pantry_windows,
            "driverRequests": self.driver_requests,
            "feasibleCandidatesEvaluated": self.feasible_candidates,
            "routesOffered": len(self.offers),
            "routesAccepted": len(self.completed),
            "foodSavedKg": round(sum(item.food_saved_kg for item in self.completed), 3),
            "collectedNotDistributedKg": round(sum(item.collected_not_distributed_kg for item in self.completed), 3),
            "uncollectedBakeryFoodKg": round(sum(
                item.estimated_food_kg for item in self.pickup_windows
                if item.id not in completed_pickup_ids
            ), 3),
            "pickupWindows": [
                {
                    "pickupId": item.id,
                    "bakeryName": item.bakery_name,
                    "readyAt": item.ready_at.isoformat(),
                    "pickupDeadline": item.pickup_deadline.isoformat(),
                }
                for item in self.pickup_windows
            ],
            "pantryWindows": [
                {
                    "pantryWindowId": item.id,
                    "pantryName": item.pantry_name,
                    "receivingStart": item.receiving_start.isoformat(),
                    "receivingEnd": item.receiving_end.isoformat(),
                    "latestPermittedArrival": (
                        item.latest_permitted_arrival.isoformat()
                    ),
                }
                for item in self.pantry_windows
            ],
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
        solver_runs = [run for day in self.days for run in day.solver_runs]
        runtimes = [run.runtime_seconds for run in solver_runs]
        gaps = [run.mip_gap for run in solver_runs if run.mip_gap is not None]
        menu_options = sum(run.menu_size for run in solver_runs)
        menu_drivers = sum(run.menu_driver_count for run in solver_runs)
        willing_menu_options = sum(
            run.willing_menu_options for run in solver_runs
        )
        unhappy_drivers = sum(run.unhappy_driver_count for run in solver_runs)
        expected_acceptances = sum(item.acceptance_probability for item in offers)
        likely_rejections = sum(item.acceptance_probability < 0.5 for item in offers)
        likely_acceptances = len(offers) - likely_rejections
        total_route_quality = sum(item.route_score for item in completed)
        food_saved_kg = sum(item.food_saved_kg for item in completed)
        collected_not_distributed_kg = sum(item.collected_not_distributed_kg for item in completed)
        completed_pickups = {(item.service_date, item.pickup_id) for item in completed}
        uncollected_bakery_food_kg = sum(
            pickup.estimated_food_kg for day in self.days for pickup in day.pickup_windows
            if (day.service_date, pickup.id) not in completed_pickups
        )
        uncollected_waste_kg_co2e = sum(
            pickup.estimated_food_kg
            * (
                pickup.waste_allocation.landfill
                * DEFAULT_ENVIRONMENTAL_ASSUMPTIONS.landfill_kg_co2e_per_kg_waste
                + pickup.waste_allocation.pig_farm
                * DEFAULT_ENVIRONMENTAL_ASSUMPTIONS.pig_farm_kg_co2e_per_kg_waste
                + pickup.waste_allocation.compost
                * DEFAULT_ENVIRONMENTAL_ASSUMPTIONS.compost_kg_co2e_per_kg_waste
            )
            for day in self.days
            for pickup in day.pickup_windows
            if (day.service_date, pickup.id) not in completed_pickups
        )
        residual_waste_kg_co2e = sum(
            item.residual_waste_kg_co2e for item in completed
        )
        transport_kg_co2e = sum(item.transport_kg_co2e for item in completed)
        waste_pathway_kg_co2e = (
            uncollected_waste_kg_co2e + residual_waste_kg_co2e
        )
        total_direct_kg_co2e = waste_pathway_kg_co2e + transport_kg_co2e
        raw_by_pantry = {name: 0.0 for name in self.pantry_opportunities}
        saved_by_pantry = {name: 0.0 for name in self.pantry_opportunities}
        for item in completed:
            raw_by_pantry[item.pantry_name] = raw_by_pantry.get(item.pantry_name, 0.0) + item.estimated_food_kg
            saved_by_pantry[item.pantry_name] = saved_by_pantry.get(item.pantry_name, 0.0) + item.food_saved_kg
        net_environmental_benefit_kg_co2e = sum(
            item.net_environmental_benefit_kg_co2e for item in completed
        )
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
            "offersLikelyAccepted": likely_acceptances,
            "likelyAcceptanceRate": _ratio(likely_acceptances, len(offers)),
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
            "averagePredepartureWaitMinutes": _mean(
                item.waiting_minutes for item in completed
            ),
            "averageRequestedTimeDeviationMinutes": _mean(
                item.requested_time_deviation_minutes for item in completed
            ),
            "averageOutsideRequestedWindowRatio": _mean(
                (item.requested_time_deviation_ratio for item in completed),
                digits=4,
            ),
            "averageOutsideRequestedWindowPercent": _mean(
                (100 * item.requested_time_deviation_ratio for item in completed),
                digits=2,
            ),
            "preferredWindowFitRate": _ratio(
                sum(item.within_preferred_window for item in completed),
                len(completed),
            ),
            "averageDestinationMinutes": _mean(item.destination_minutes for item in completed),
            "averageOriginDeviationMiles": _mean(
                item.origin_deviation_miles for item in completed
            ),
            "averageDestinationDeviationMiles": _mean(
                item.destination_deviation_miles for item in completed
            ),
            "averageNormalizedSpatialDeviation": _mean(
                (item.normalized_spatial_deviation for item in completed),
                digits=4,
            ),
            "averageTotalTripDurationMinutes": _mean(
                item.total_trip_minutes for item in completed
            ),
            "averageRouteBurdenMinutes": _mean(
                item.total_trip_minutes for item in completed
            ),
            "foodSavedKg": round(food_saved_kg, 3),
            "uncollectedBakeryFoodKg": round(uncollected_bakery_food_kg, 3),
            "collectedNotDistributedKg": round(collected_not_distributed_kg, 3),
            "foodWastedKg": round(
                uncollected_bakery_food_kg + collected_not_distributed_kg,
                3,
            ),
            "uncollectedWasteKgCO2e": round(uncollected_waste_kg_co2e, 3),
            "residualWasteKgCO2e": round(residual_waste_kg_co2e, 3),
            "wastePathwayKgCO2e": round(waste_pathway_kg_co2e, 3),
            "transportKgCO2e": round(transport_kg_co2e, 3),
            "totalDirectKgCO2e": round(total_direct_kg_co2e, 3),
            "rawDonationDistributionGini": round(gini(list(raw_by_pantry.values())), 4),
            "foodSavedDistributionGini": round(gini(list(saved_by_pantry.values())), 4),
            "netEnvironmentalBenefitKgCO2e": round(
                net_environmental_benefit_kg_co2e, 3
            ),
            "averageNetEnvironmentalBenefitKgCO2ePerDelivery": _mean(
                (item.net_environmental_benefit_kg_co2e for item in completed),
                digits=3,
            ),
            "averageElapsedFromLoginMinutes": _mean(
                item.waiting_minutes + item.total_trip_minutes
                for item in completed
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
            "menuOptionsOffered": menu_options,
            "averageMenuSize": round(menu_options / menu_drivers, 3)
            if menu_drivers else 0.0,
            "willingMenuOptions": willing_menu_options,
            "willingMenuOptionRate": _ratio(
                willing_menu_options,
                menu_options,
            ),
            "unhappyDrivers": unhappy_drivers,
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
            random_seed=simulation.random_seed,
        )
        scheduled_pantries = _pantry_windows(snapshot, service_date, zone)
        pantries, _ = _sample_pantry_openings(
            scheduled_pantries,
            simulation.staffed_pantry_open_probability,
            rng,
        )
        pickups = [
            replace(
                pickup,
                location=_location_with_estimated_zip(pickup.location),
            )
            for pickup in pickups
        ]
        pantries = [
            replace(
                pantry,
                location=_location_with_estimated_zip(pantry.location),
            )
            for pantry in pantries
        ]
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
    pantry_raw_totals: dict[int, float] = defaultdict(float)
    pantry_saved_totals: dict[int, float] = defaultdict(float)
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
                    historical_raw_food_kg=pantry_raw_totals[_organization_id(pantry.id)],
                    historical_saved_food_kg=pantry_saved_totals[_organization_id(pantry.id)],
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
            participation_model = _participation_model(scenario.config)
            candidates = score_assignment_candidates(
                [
                    replace(
                        candidate,
                        route=replace(
                            candidate.route,
                            acceptance_probability=estimate_acceptance_probability(
                                candidate.route,
                                participation_model,
                            ),
                        ),
                    )
                    for candidate in candidates
                ],
                weights,
            )
            candidate_count += len(candidates)
            realized_willingness = {
                _candidate_key(candidate): (
                    True
                    if not scenario.config.acceptance_enabled
                    else _stable_uniform(
                        scenario.config.simulation.random_seed,
                        "acceptance",
                        candidate.request_id,
                        candidate.route.bakery_id,
                        candidate.route.pantry_id,
                    ) < acceptance_probability(candidate, scenario.config)
                )
                for candidate in candidates
            }
            selection = _select_assignments(
                policy,
                candidates,
                available_pickups,
                scenario.config.simulation.random_seed,
                epoch,
                weights,
                realized_willingness,
            )
            selected = selection.assignments
            diagnostic = selection.diagnostics
            diagnostics.append(diagnostic)
            selected_keys = {_candidate_key(candidate) for candidate in selected}
            if policy == RoutingPolicy.HORNER_2021_SLSF_NOZ:
                recommendation_ranks = _paper_menu_ranks(
                    selection.recommendations
                )
            else:
                recommendation_ranks = _recommendation_ranks(
                    policy,
                    candidates,
                    selected,
                    available_pickups,
                    scenario.config.simulation.random_seed,
                    epoch,
                )
            recommendation_keys = set(recommendation_ranks)
            acceptance_by_key: dict[tuple[str, str, str], bool] = {}
            for candidate in selected:
                probability = acceptance_probability(candidate, scenario.config)
                accepted = realized_willingness[_candidate_key(candidate)]
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
                    pantry_id = _organization_id(candidate.route.pantry_id)
                    pantry_raw_totals[pantry_id] += candidate.route.estimated_food_kg
                    pantry_saved_totals[pantry_id] += candidate.route.food_saved_kg
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
                        accepted=(
                            realized_willingness[_candidate_key(candidate)]
                            if (
                                policy == RoutingPolicy.HORNER_2021_SLSF_NOZ
                                and _candidate_key(candidate) in recommendation_keys
                            )
                            else acceptance_by_key.get(_candidate_key(candidate))
                        ),
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
            pickup_windows=tuple(day.pickups),
            pantry_windows=tuple(day.pantries),
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
    horizons: Sequence[int] = (5,),
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
    """Run reproducible five-day experiments over identical policy inputs."""

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

    return estimate_acceptance_probability(
        candidate.route,
        _participation_model(config),
    )


def _participation_model(config: ExperimentConfig) -> ParticipationModel:
    """Translate experiment settings into the solver's shared behavior model."""

    return ParticipationModel(
        intercept=config.acceptance_intercept,
        drive_minute_penalty=config.acceptance_drive_penalty,
        requested_time_deviation_ratio_penalty=(
            config.acceptance_requested_time_deviation_ratio_penalty
        ),
        spatial_deviation_ratio_penalty=(
            config.acceptance_spatial_deviation_ratio_penalty
        ),
    )


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
    "averagePredepartureWaitMinutes",
    "averageRequestedTimeDeviationMinutes",
    "preferredWindowFitRate",
    "averageTotalTripDurationMinutes",
    "averageElapsedFromLoginMinutes",
    "foodSavedKg",
    "foodWastedKg",
    "uncollectedBakeryFoodKg",
    "collectedNotDistributedKg",
    "transportKgCO2e",
    "wastePathwayKgCO2e",
    "totalDirectKgCO2e",
    "netEnvironmentalBenefitKgCO2e",
    "systemObjectiveValue",
    "driverAcceptanceRate",
    "expectedDriverAcceptanceRate",
    "likelyAcceptanceRate",
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
    if not pickups or not pantries or count <= 0:
        return []
    locations = [item.location for item in pickups] + [item.location for item in pantries]
    center_latitude = sum(item.latitude for item in locations) / len(locations)
    center_longitude = sum(item.longitude for item in locations) / len(locations)
    requests: list[DriverRequest] = []
    active_count = rng.randint(1, count)
    request_index = 0
    used_epochs: set[datetime] = set()

    # Simultaneous arrivals should be an exception, not the default. Build one
    # shared epoch on days with at least two active drivers and occasionally a
    # second one on busier days. Every other driver enters at an independent
    # minute-level time. ``max_simultaneous_drivers`` remains a cap rather than
    # a target applied to every decision epoch.
    simultaneous_epochs = 0
    if active_count >= 2:
        simultaneous_epochs = 1
        if active_count >= 5 and rng.random() < 0.45:
            simultaneous_epochs = 2

    group_sizes = [2] * simultaneous_epochs
    ungrouped = active_count - sum(group_sizes)
    if (
        max_simultaneous_drivers == 3
        and group_sizes
        and ungrouped >= 1
        and rng.random() < 0.18
    ):
        group_sizes[rng.randrange(len(group_sizes))] = 3
        ungrouped -= 1
    group_sizes.extend([1] * ungrouped)
    rng.shuffle(group_sizes)

    for group_size in group_sizes:
        pickup_anchor = rng.choice(pickups)
        # Logging in is a marketplace event, not an implied departure.  Most
        # volunteers arrive at irregular minute-level times before a plausible
        # pickup.  Members of a rare simultaneous group share this login time.
        raw_login = pickup_anchor.ready_at - timedelta(minutes=rng.randint(20, 150))
        logged_at = raw_login.replace(second=0, microsecond=0)
        while logged_at in used_epochs:
            logged_at += timedelta(
                minutes=rng.randint(1, max(interval_minutes // 3, 1))
            )
        used_epochs.add(logged_at)

        for _ in range(group_size):
            request_index += 1
            # Some drivers browse for a trip they could do now, while others
            # request a future interval (for example, after work).  The
            # preferred interval is soft; the wider search horizon preserves
            # useful alternatives that miss the preference slightly.
            if rng.random() < 0.42:
                preferred_start = logged_at
            else:
                preferred_start = logged_at + timedelta(
                    minutes=rng.choice((30, 45, 60, 75, 90, 120))
                )
            preferred_finish = preferred_start + timedelta(
                minutes=rng.choice((30, 45, 60, 90, 120, 180, 240))
            )
            search_until = preferred_finish + timedelta(
                minutes=rng.choice((45, 60, 90))
            )
            start_latitude = center_latitude + rng.uniform(-0.025, 0.025)
            start_longitude = center_longitude + rng.uniform(-0.035, 0.035)
            start_zip_code = _nearest_zip_code(start_latitude, start_longitude)
            start = Location(
                address_entered=f"synthetic-driver-{request_index}-origin",
                formatted_address=f"Estimated center of ZIP {start_zip_code}",
                latitude=start_latitude,
                longitude=start_longitude,
                validation_status=AddressValidationStatus.VALIDATED,
                postal_code=start_zip_code,
            )
            preferred: Location | None = None
            destination_zip_code = ""
            if rng.random() < 0.65:
                anchor = rng.choice(pantries).location
                preferred_latitude = anchor.latitude + rng.uniform(-0.01, 0.01)
                preferred_longitude = anchor.longitude + rng.uniform(-0.01, 0.01)
                destination_zip_code = _nearest_zip_code(
                    preferred_latitude,
                    preferred_longitude,
                )
                preferred = Location(
                    address_entered=f"synthetic-driver-{request_index}-destination",
                    formatted_address=f"Estimated center of ZIP {destination_zip_code}",
                    latitude=preferred_latitude,
                    longitude=preferred_longitude,
                    validation_status=AddressValidationStatus.VALIDATED,
                    postal_code=destination_zip_code,
                )
            requests.append(DriverRequest(
                id=f"request-{logged_at.date().isoformat()}-{request_index}",
                driver_id=f"synthetic-driver-{request_index}",
                earliest_start=preferred_start,
                latest_finish=preferred_finish,
                start_location=start,
                preferred_destination=preferred,
                logged_at=logged_at,
                search_until=search_until,
                start_zip_code=start_zip_code,
                destination_zip_code=destination_zip_code,
            ))
    return sorted(requests, key=lambda item: (item.login_time, item.id))


def _floor_epoch(value: datetime, interval_minutes: int) -> datetime:
    minute = value.minute - value.minute % interval_minutes
    return value.replace(minute=minute, second=0, microsecond=0)


def _request_epochs(
    requests: Sequence[DriverRequest],
) -> list[tuple[datetime, tuple[DriverRequest, ...]]]:
    grouped: dict[datetime, list[DriverRequest]] = defaultdict(list)
    for request in requests:
        grouped[request.login_time].append(request)
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
    weights: OptimizationWeights,
    realized_willingness: dict[tuple[str, str, str], bool],
) -> NetworkOptimizationResult:
    if policy == RoutingPolicy.BAKEDBOSTON_MIP:
        return optimize_assignment_candidates(tuple(candidates), weights=weights)
    if policy == RoutingPolicy.NAIR_2018_DISTANCE_FIRST:
        return optimize_nair_distance_first_candidates(tuple(candidates))
    if policy == RoutingPolicy.XUE_ZOU_2025_TOTAL_CURB:
        return optimize_xue_zou_total_curb_candidates(tuple(candidates))
    if policy == RoutingPolicy.HORNER_2021_SLSF_NOZ:
        return optimize_horner_slsf_noz_candidates(
            tuple(candidates),
            scenario_seed=_stable_integer(
                seed,
                "horner-saa",
                epoch.isoformat(),
            ),
            realized_willingness=realized_willingness,
        )
    started = clock.perf_counter()
    key = _policy_sort_key(policy, pickups, seed, epoch)
    selected = _greedy_select(candidates, key)
    runtime = clock.perf_counter() - started
    return NetworkOptimizationResult(
        assignments=selected,
        diagnostics=SolverDiagnostics(
            backend=f"baseline:{policy.value}",
            status="complete",
            candidate_count=len(candidates),
            matched_count=len(selected),
            route_quality=balanced_objective_value(candidates, selected, weights),
            runtime_seconds=runtime,
            expected_completed_deliveries=sum(
                item.route.acceptance_probability for item in selected
            ),
            route_distance_miles=sum(
                item.route.route_distance_miles for item in selected
            ),
            estimated_food_kg=sum(item.route.estimated_food_kg for item in selected),
            usable_food_kg=sum(item.route.usable_food_kg for item in selected),
            food_saved_kg=sum(item.route.food_saved_kg for item in selected),
            collected_not_distributed_kg=sum(
                item.route.collected_not_distributed_kg for item in selected
            ),
            avoided_system_kg_co2e=sum(
                item.route.avoided_system_kg_co2e for item in selected
            ),
            transport_kg_co2e=sum(
                item.route.transport_kg_co2e for item in selected
            ),
            residual_waste_kg_co2e=sum(
                item.route.residual_waste_kg_co2e for item in selected
            ),
            net_environmental_benefit_kg_co2e=sum(
                item.route.net_environmental_benefit_kg_co2e for item in selected
            ),
        ),
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
    if policy == RoutingPolicy.NAIR_2018_DISTANCE_FIRST:
        return lambda item: (
            item.route.route_distance_miles,
            item.route.finish_at,
            item.route.bakery_id,
            item.route.pantry_id,
        )
    if policy == RoutingPolicy.XUE_ZOU_2025_TOTAL_CURB:
        return lambda item: (
            -(
                item.route.counterfactual_waste_kg_co2e
                - item.route.residual_waste_kg_co2e
                - item.route.transport_kg_co2e
            ),
            item.route.finish_at,
            item.route.bakery_id,
            item.route.pantry_id,
        )
    if policy == RoutingPolicy.HORNER_2021_SLSF_NOZ:
        return lambda item: (
            -item.route.acceptance_probability,
            item.route.route_distance_miles,
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
            item.route.drive_minutes,
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
            item.route.requested_time_deviation_ratio,
            item.route.normalized_spatial_deviation,
            item.route.drive_minutes,
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
        pickup_at=route.pickup_at,
        pantry_arrival_at=route.pantry_arrival_at,
        finish_at=route.finish_at,
        drive_minutes=route.drive_minutes,
        waiting_minutes=route.waiting_minutes,
        facility_waiting_minutes=route.facility_waiting_minutes,
        requested_time_deviation_minutes=(
            route.requested_time_deviation_minutes
        ),
        requested_window_minutes=route.requested_window_minutes,
        requested_time_deviation_ratio=route.requested_time_deviation_ratio,
        within_preferred_window=route.within_preferred_window,
        destination_minutes=route.destination_minutes,
        origin_deviation_miles=route.origin_deviation_miles,
        destination_deviation_miles=route.destination_deviation_miles,
        normalized_origin_deviation=route.normalized_origin_deviation,
        normalized_destination_deviation=route.normalized_destination_deviation,
        normalized_spatial_deviation=route.normalized_spatial_deviation,
        pantry_priority=route.pantry_priority,
        route_score=route.score,
        acceptance_probability=probability,
        distance_miles=route.route_distance_miles,
        total_trip_minutes=(
            route.finish_at - route.depart_at
        ).total_seconds() / 60,
        estimated_food_kg=route.estimated_food_kg,
        usable_food_kg=route.usable_food_kg,
        bakery_usable_fraction=route.bakery_usable_fraction,
        pantry_distribution_fraction=route.pantry_distribution_fraction,
        food_saved_kg=route.food_saved_kg,
        collected_not_distributed_kg=route.collected_not_distributed_kg,
        bakery_unusable_food_kg=route.bakery_unusable_food_kg,
        pantry_undistributed_food_kg=route.pantry_undistributed_food_kg,
        bakery_route_waste_kg_co2e=route.bakery_route_waste_kg_co2e,
        pantry_route_waste_kg_co2e=route.pantry_route_waste_kg_co2e,
        pantry_landfill_fraction=route.pantry_landfill_fraction,
        pantry_pig_farm_fraction=route.pantry_pig_farm_fraction,
        avoided_system_kg_co2e=route.avoided_system_kg_co2e,
        transport_kg_co2e=route.transport_kg_co2e,
        residual_waste_kg_co2e=route.residual_waste_kg_co2e,
        net_environmental_benefit_kg_co2e=(
            route.net_environmental_benefit_kg_co2e
        ),
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
        pickup_at=route.pickup_at,
        pantry_arrival_at=route.pantry_arrival_at,
        finish_at=route.finish_at,
        drive_minutes=route.drive_minutes,
        waiting_minutes=route.waiting_minutes,
        facility_waiting_minutes=route.facility_waiting_minutes,
        requested_time_deviation_minutes=(
            route.requested_time_deviation_minutes
        ),
        requested_window_minutes=route.requested_window_minutes,
        requested_time_deviation_ratio=route.requested_time_deviation_ratio,
        within_preferred_window=route.within_preferred_window,
        destination_minutes=route.destination_minutes,
        origin_deviation_miles=route.origin_deviation_miles,
        destination_deviation_miles=route.destination_deviation_miles,
        normalized_origin_deviation=route.normalized_origin_deviation,
        normalized_destination_deviation=route.normalized_destination_deviation,
        normalized_spatial_deviation=route.normalized_spatial_deviation,
        pantry_priority=route.pantry_priority,
        route_score=route.score,
        acceptance_probability=probability,
        distance_miles=route.route_distance_miles,
        total_trip_minutes=(
            route.finish_at - route.depart_at
        ).total_seconds() / 60,
        estimated_food_kg=route.estimated_food_kg,
        usable_food_kg=route.usable_food_kg,
        bakery_usable_fraction=route.bakery_usable_fraction,
        pantry_distribution_fraction=route.pantry_distribution_fraction,
        food_saved_kg=route.food_saved_kg,
        collected_not_distributed_kg=route.collected_not_distributed_kg,
        bakery_unusable_food_kg=route.bakery_unusable_food_kg,
        pantry_undistributed_food_kg=route.pantry_undistributed_food_kg,
        bakery_route_waste_kg_co2e=route.bakery_route_waste_kg_co2e,
        pantry_route_waste_kg_co2e=route.pantry_route_waste_kg_co2e,
        pantry_landfill_fraction=route.pantry_landfill_fraction,
        pantry_pig_farm_fraction=route.pantry_pig_farm_fraction,
        avoided_system_kg_co2e=route.avoided_system_kg_co2e,
        transport_kg_co2e=route.transport_kg_co2e,
        residual_waste_kg_co2e=route.residual_waste_kg_co2e,
        net_environmental_benefit_kg_co2e=(
            route.net_environmental_benefit_kg_co2e
        ),
        driver_start=(
            request.start_location.latitude,
            request.start_location.longitude,
        ),
        bakery_location=(pickup.location.latitude, pickup.location.longitude),
        pantry_location=(pantry.location.latitude, pantry.location.longitude),
        bakery_postal_code=pickup.location.postal_code,
        pantry_postal_code=pantry.location.postal_code,
        recommendation_rank=recommendation_rank,
        selected=selected,
        accepted=accepted,
    )


_BOSTON_ZIP_CENTERS: tuple[tuple[str, float, float], ...] = (
    ("02108", 42.3570, -71.0640),
    ("02114", 42.3611, -71.0680),
    ("02115", 42.3429, -71.0920),
    ("02118", 42.3387, -71.0726),
    ("02119", 42.3251, -71.0857),
    ("02120", 42.3328, -71.0971),
    ("02130", 42.3097, -71.1147),
    ("02134", 42.3533, -71.1329),
    ("02135", 42.3484, -71.1535),
    ("02139", 42.3647, -71.1042),
    ("02143", 42.3810, -71.0974),
    ("02458", 42.3543, -71.1886),
    ("02459", 42.3152, -71.1900),
)


def _nearest_zip_code(latitude: float, longitude: float) -> str:
    """Return the nearest representative Boston-area ZIP centroid."""

    return min(
        _BOSTON_ZIP_CENTERS,
        key=lambda item: (
            (latitude - item[1]) ** 2
            + ((longitude - item[2]) * math.cos(math.radians(latitude))) ** 2
        ),
    )[0]


def estimated_postal_code(location: Location) -> str:
    """Return a supplied ZIP or estimate one from a Boston-area centroid."""

    return location.postal_code or _nearest_zip_code(
        location.latitude,
        location.longitude,
    )


def _location_with_estimated_zip(location: Location) -> Location:
    """Fill missing facility ZIPs from the nearest Boston-area centroid."""

    if location.postal_code:
        return location
    return replace(
        location,
        postal_code=estimated_postal_code(location),
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
    """Build fair, conflict-free simultaneous recommendation menus.

    Primary assignments are rank one. Then recommendation ranks are allocated
    as global layers: maximize the number of drivers receiving rank N before
    maximizing that layer's route quality. A bakery pickup is owned by only one
    simultaneous driver's menu, while pantry destinations may repeat. This
    prevents one driver from receiving extra options while another receives no
    option whenever enough distinct feasible bakery pickups exist.
    """

    request_ids = sorted({item.request_id for item in candidates})
    ranks: dict[tuple[str, str, str], int] = {}
    shown_keys: set[tuple[str, str, str]] = set()
    pickup_owner: dict[str, str] = {}
    recommendation_counts = {request_id: 0 for request_id in request_ids}

    for item in selected:
        key = _candidate_key(item)
        ranks[key] = 1
        shown_keys.add(key)
        pickup_owner[item.route.bakery_id] = item.request_id
        recommendation_counts[item.request_id] = 1

    # A baseline policy can occasionally leave a feasible driver unmatched.
    # Repair rank one without disturbing any already-selected assignment.
    first_layer = _allocate_menu_layer(
        policy,
        candidates,
        pickups,
        seed,
        epoch,
        eligible_requests={
            request_id
            for request_id, count in recommendation_counts.items()
            if count == 0
        },
        pickup_owner=pickup_owner,
        shown_keys=shown_keys,
    )
    for item in first_layer:
        key = _candidate_key(item)
        ranks[key] = 1
        shown_keys.add(key)
        pickup_owner.setdefault(item.route.bakery_id, item.request_id)
        recommendation_counts[item.request_id] = 1

    for rank in range(2, limit + 1):
        eligible_requests = {
            request_id
            for request_id, count in recommendation_counts.items()
            if count == rank - 1
        }
        if not eligible_requests:
            break
        layer = _allocate_menu_layer(
            policy,
            candidates,
            pickups,
            seed,
            epoch,
            eligible_requests=eligible_requests,
            pickup_owner=pickup_owner,
            shown_keys=shown_keys,
        )
        if not layer:
            break
        for item in layer:
            key = _candidate_key(item)
            ranks[key] = rank
            shown_keys.add(key)
            pickup_owner.setdefault(item.route.bakery_id, item.request_id)
            recommendation_counts[item.request_id] = rank
    return ranks


def _paper_menu_ranks(
    recommendations: Sequence[AssignmentCandidate],
) -> dict[tuple[str, str, str], int]:
    """Give the paper-derived menu a stable display order, not a choice rule."""

    ranks: dict[tuple[str, str, str], int] = {}
    request_ids = sorted({item.request_id for item in recommendations})
    for request_id in request_ids:
        ordered = sorted(
            (
                item
                for item in recommendations
                if item.request_id == request_id
            ),
            key=lambda item: (
                -item.route.acceptance_probability,
                item.route.route_distance_miles,
                item.route.finish_at,
                item.route.bakery_id,
                item.route.pantry_id,
            ),
        )
        for rank, item in enumerate(ordered, start=1):
            ranks[_candidate_key(item)] = rank
    return ranks


def _allocate_menu_layer(
    policy: RoutingPolicy,
    candidates: Sequence[AssignmentCandidate],
    pickups: dict[str, BakeryPickup],
    seed: int,
    epoch: datetime,
    *,
    eligible_requests: set[str],
    pickup_owner: dict[str, str],
    shown_keys: set[tuple[str, str, str]],
) -> tuple[AssignmentCandidate, ...]:
    eligible = [
        item
        for item in candidates
        if item.request_id in eligible_requests
        and _candidate_key(item) not in shown_keys
        and pickup_owner.get(item.route.bakery_id, item.request_id)
        == item.request_id
    ]
    if not eligible:
        return ()
    utilities = _recommendation_utilities(
        policy,
        eligible,
        pickups,
        seed,
        epoch,
    )
    return allocate_recommendation_layer(eligible, utilities)


def _recommendation_utilities(
    policy: RoutingPolicy,
    candidates: Sequence[AssignmentCandidate],
    pickups: dict[str, BakeryPickup],
    seed: int,
    epoch: datetime,
) -> dict[tuple[str, str, str], float]:
    """Translate any comparison policy's best-first order into MIP utilities."""

    result: dict[tuple[str, str, str], float] = {}
    key = _policy_sort_key(policy, pickups, seed, epoch)
    grouped: dict[str, list[AssignmentCandidate]] = defaultdict(list)
    for item in candidates:
        grouped[item.request_id].append(item)
    for request_candidates in grouped.values():
        ordered = sorted(request_candidates, key=key)
        size = len(ordered)
        for index, item in enumerate(ordered):
            result[_candidate_key(item)] = float(size - index)
    return result


def _stable_uniform(seed: int, *parts: str) -> float:
    value = "|".join((str(seed), *parts)).encode("utf-8")
    integer = int.from_bytes(hashlib.sha256(value).digest()[:8], "big")
    return integer / float(2**64)


def _stable_integer(seed: int, *parts: str) -> int:
    value = "|".join((str(seed), *parts)).encode("utf-8")
    return int.from_bytes(hashlib.sha256(value).digest()[:8], "big")


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
        "expectedCompletedDeliveries": round(
            diagnostics.expected_completed_deliveries, 4
        ),
        "routeDistanceMiles": round(diagnostics.route_distance_miles, 4),
        "foodSavedKg": round(diagnostics.food_saved_kg, 4),
        "collectedNotDistributedKg": round(diagnostics.collected_not_distributed_kg, 4),
        "netEnvironmentalBenefitKgCO2e": round(
            diagnostics.net_environmental_benefit_kg_co2e, 4
        ),
        "runtimeSeconds": round(diagnostics.runtime_seconds, 6),
        "mipGap": diagnostics.mip_gap,
        "menuSize": diagnostics.menu_size,
        "menuDriverCount": diagnostics.menu_driver_count,
        "willingMenuOptions": diagnostics.willing_menu_options,
        "unhappyDriverCount": diagnostics.unhappy_driver_count,
        "trainingScenarioCount": diagnostics.training_scenario_count,
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
        "averagePredepartureWaitMinutes",
        "averageRequestedTimeDeviationMinutes",
        "preferredWindowFitRate",
        "averageTotalTripDurationMinutes",
        "averageElapsedFromLoginMinutes",
        "foodSavedKg",
        "foodWastedKg",
        "uncollectedBakeryFoodKg",
        "collectedNotDistributedKg",
        "transportKgCO2e",
        "wastePathwayKgCO2e",
        "totalDirectKgCO2e",
        "rawDonationDistributionGini",
        "foodSavedDistributionGini",
        "netEnvironmentalBenefitKgCO2e",
        "averageNetEnvironmentalBenefitKgCO2ePerDelivery",
        "systemObjectiveValue",
        "driverAcceptanceRate",
        "expectedDriverAcceptanceRate",
        "likelyAcceptanceRate",
        "likelyRejectionRate",
        "unservedPickups",
        "totalSolverRuntimeSeconds",
        "averageSolverRuntimeSeconds",
        "totalRouteQuality",
        "averageRouteQuality",
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
