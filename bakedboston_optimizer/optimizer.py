from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from itertools import product
import atexit
import math
import os
from threading import RLock
from typing import TYPE_CHECKING, Sequence

from .environment import (
    DEFAULT_ENVIRONMENTAL_ASSUMPTIONS,
    EnvironmentalAssumptions,
    estimate_route_environmental_impact,
)
from .models import (
    AssignmentCandidate,
    BakeryPickup,
    DriverRequest,
    Location,
    NetworkOptimizationResult,
    Pantry,
    RouteCandidate,
    SolverDiagnostics,
)
from .travel import TravelTimeProvider, _haversine_miles

try:
    import gurobipy as gp
    from gurobipy import GRB
except ImportError:  # pragma: no cover - fallback only matters when Gurobi is unavailable at runtime
    gp = None
    GRB = None

if TYPE_CHECKING:
    import gurobipy as gp_typing


_SOLVER_LOCK = RLock()
_WLS_ENVIRONMENT: "gp_typing.Env | None" = None
_WLS_ENVIRONMENT_SIGNATURE: tuple[str, str, str] | None = None


@dataclass(frozen=True)
class OptimizationWeights:
    pantry_priority_reward: float = 45.0
    drive_minute_penalty: float = 1.0
    # Retained as zero-valued compatibility fields. Waiting safely before a
    # planned departure and waiting moved out of a facility are diagnostics,
    # not route-quality penalties.
    waiting_minute_penalty: float = 0.0
    facility_waiting_minute_penalty: float = 0.0
    # Kept for backwards compatibility with earlier saved experiment configs.
    # Geographic fit is now a normalized straight-line deviation, not a travel-
    # time reward or penalty.
    destination_minute_penalty: float = 0.0
    spatial_deviation_ratio_penalty: float = 18.0
    requested_time_deviation_minute_penalty: float = 0.0
    requested_time_deviation_ratio_penalty: float = 24.0
    # Retained for backwards-compatible saved configurations. The default is
    # zero because vehicle emissions are now included in the lifecycle-aware
    # net environmental benefit below rather than charged twice.
    route_mile_penalty: float = 0.0
    # Reward one estimated kilogram of net avoided CO2e. This lifecycle-aware
    # term credits usable food and avoided disposal, then subtracts transport
    # and residual redistribution-waste emissions.
    environmental_benefit_reward: float = 1.5


@dataclass(frozen=True)
class ParticipationModel:
    """Transparent scenario model for the probability a driver accepts a route.

    These coefficients are experimental assumptions, not learned behavioral
    estimates.  They make the participation hypothesis testable now and can be
    replaced with coefficients fitted to observed choices in future studies.
    """

    intercept: float = 2.2
    drive_minute_penalty: float = 0.045
    requested_time_deviation_ratio_penalty: float = 1.8
    spatial_deviation_ratio_penalty: float = 1.1


class GurobiUnavailableError(RuntimeError):
    """Raised when an optimization request cannot be solved by Gurobi."""


def active_solver_backend() -> str:
    """Describe the backend that will be reported for a successful solve."""

    if gp is not None:
        return "gurobi"
    if _development_fallback_enabled():
        return "development_deterministic_fallback"
    return "unavailable"


def solver_version() -> str | None:
    """Return the installed Gurobi version for reproducible experiment reports."""

    if gp is None:
        return None
    return ".".join(str(part) for part in gp.gurobi.version())


def rank_routes(
    pickups: list[BakeryPickup],
    pantries: list[Pantry],
    request: DriverRequest,
    travel: TravelTimeProvider,
    weights: OptimizationWeights = OptimizationWeights(),
    pickup_service_minutes: int = 5,
    dropoff_service_minutes: int = 5,
    environmental_assumptions: EnvironmentalAssumptions = DEFAULT_ENVIRONMENTAL_ASSUMPTIONS,
) -> list[RouteCandidate]:
    """Generate feasible routes and rank them with a Gurobi solution pool.

    This is the single-driver recommendation interface used by the app's browse
    and search experiences. It deliberately fails closed when Gurobi is not
    available unless the explicitly named development fallback is enabled.
    """

    feasible_candidates: list[RouteCandidate] = []
    for pickup, pantry in product(pickups, pantries):
        candidate = _candidate(
            pickup,
            pantry,
            request,
            travel,
            weights,
            pickup_service_minutes,
            dropoff_service_minutes,
            environmental_assumptions,
        )
        if candidate is not None:
            feasible_candidates.append(candidate)
    if not feasible_candidates:
        return []
    feasible_candidates = _normalize_spatial_deviation(
        feasible_candidates, request, weights
    )

    if gp is None:
        if _development_fallback_enabled():
            return _deterministic_rank(feasible_candidates)
        raise GurobiUnavailableError(
            "Gurobi is required, but gurobipy is not installed in the optimizer runtime."
        )

    with _SOLVER_LOCK:
        return _rank_with_gurobi(feasible_candidates)


def optimize_network(
    pickups: list[BakeryPickup],
    pantries: list[Pantry],
    requests: list[DriverRequest],
    travel: TravelTimeProvider,
    weights: OptimizationWeights = OptimizationWeights(),
    pickup_service_minutes: int = 5,
    dropoff_service_minutes: int = 5,
    environmental_assumptions: EnvironmentalAssumptions = DEFAULT_ENVIRONMENTAL_ASSUMPTIONS,
) -> NetworkOptimizationResult:
    """Select a globally consistent set of driver-pickup-pantry assignments.

    The first objective maximizes expected completed bakery pickups using the
    transparent route-acceptance estimate. The second objective, optimized
    within one percent of the best first-stage value, maximizes social and
    logistics quality: pantry priority is rewarded while driving, mileage,
    proportional requested-window deviation, and distance from the driver's
    requested ZIP areas are penalized. Net estimated lifecycle CO2e benefit is
    rewarded: usable food and avoided disposal count positively, while vehicle
    travel and residual redistribution waste count negatively. Pantries do not
    receive a capacity constraint because BakedBoston intentionally allows
    multiple deliveries during an open receiving window.
    """

    candidates = list(enumerate_assignment_candidates(
        pickups,
        pantries,
        requests,
        travel,
        weights,
        pickup_service_minutes,
        dropoff_service_minutes,
        environmental_assumptions,
    ))
    return optimize_assignment_candidates(candidates)


def optimize_assignment_candidates(
    candidates: list[AssignmentCandidate] | tuple[AssignmentCandidate, ...],
) -> NetworkOptimizationResult:
    """Solve a pre-enumerated feasible assignment set with the network MIP."""

    candidate_list = list(candidates)
    if not candidate_list:
        return NetworkOptimizationResult(
            assignments=(),
            diagnostics=SolverDiagnostics(
                backend="gurobi" if gp is not None else "unavailable",
                status="no_feasible_assignments",
                candidate_count=0,
                matched_count=0,
                route_quality=0.0,
                runtime_seconds=0.0,
            ),
        )
    if gp is None:
        if not _development_fallback_enabled():
            raise GurobiUnavailableError(
                "Gurobi is required, but gurobipy is not installed in the optimizer runtime."
            )
        assignments = _greedy_assignment(candidate_list)
        return NetworkOptimizationResult(
            assignments=tuple(assignments),
            diagnostics=SolverDiagnostics(
                backend="development_greedy_fallback",
                status="fallback",
                candidate_count=len(candidate_list),
                matched_count=len(assignments),
                route_quality=sum(item.route.score for item in assignments),
                runtime_seconds=0.0,
                expected_completed_deliveries=sum(
                    item.route.acceptance_probability for item in assignments
                ),
                route_distance_miles=sum(
                    item.route.route_distance_miles for item in assignments
                ),
                estimated_food_kg=sum(
                    item.route.estimated_food_kg for item in assignments
                ),
                usable_food_kg=sum(item.route.usable_food_kg for item in assignments),
                avoided_system_kg_co2e=sum(
                    item.route.avoided_system_kg_co2e for item in assignments
                ),
                transport_kg_co2e=sum(
                    item.route.transport_kg_co2e for item in assignments
                ),
                residual_waste_kg_co2e=sum(
                    item.route.residual_waste_kg_co2e for item in assignments
                ),
                net_environmental_benefit_kg_co2e=sum(
                    item.route.net_environmental_benefit_kg_co2e
                    for item in assignments
                ),
            ),
        )

    with _SOLVER_LOCK:
        return _optimize_network_with_gurobi(candidate_list)


def allocate_recommendation_layer(
    candidates: Sequence[AssignmentCandidate],
    utilities: dict[tuple[str, str, str], float] | None = None,
) -> tuple[AssignmentCandidate, ...]:
    """Allocate one conflict-free recommendation to as many drivers as possible.

    This auxiliary assignment is used to build simultaneous recommendation
    menus.  Its lexicographic objective is deliberately fairness-first:

    1. maximize the number of drivers receiving this recommendation rank;
    2. among those maximum-cardinality allocations, maximize route utility.

    A bakery pickup occurrence may be placed in only one driver's menu. Pantry
    windows are not exclusive because they can receive multiple deliveries.
    """

    candidate_list = list(candidates)
    if not candidate_list:
        return ()
    utility_by_key = utilities or {
        _assignment_key(candidate): candidate.route.score
        for candidate in candidate_list
    }
    if gp is None:
        return _exact_recommendation_layer(candidate_list, utility_by_key)

    with _SOLVER_LOCK:
        model = _model("bakedboston_recommendation_layer")
        try:
            route_vars = {
                index: model.addVar(vtype=GRB.BINARY, name=f"recommend_{index}")
                for index in range(len(candidate_list))
            }
            for request_id in sorted({item.request_id for item in candidate_list}):
                model.addConstr(
                    gp.quicksum(
                        route_vars[index]
                        for index, item in enumerate(candidate_list)
                        if item.request_id == request_id
                    ) <= 1,
                    name=f"request_{_safe_name(request_id)}_one_recommendation",
                )
            for driver_id in sorted({item.driver_id for item in candidate_list}):
                model.addConstr(
                    gp.quicksum(
                        route_vars[index]
                        for index, item in enumerate(candidate_list)
                        if item.driver_id == driver_id
                    ) <= 1,
                    name=f"driver_{_safe_name(driver_id)}_one_recommendation",
                )
            for pickup_id in sorted({item.route.bakery_id for item in candidate_list}):
                model.addConstr(
                    gp.quicksum(
                        route_vars[index]
                        for index, item in enumerate(candidate_list)
                        if item.route.bakery_id == pickup_id
                    ) <= 1,
                    name=f"pickup_{_safe_name(pickup_id)}_one_menu_owner",
                )

            model.ModelSense = GRB.MAXIMIZE
            model.setObjectiveN(
                gp.quicksum(route_vars.values()),
                index=0,
                priority=2,
                weight=1.0,
                name="maximize_drivers_with_recommendations",
            )
            model.setObjectiveN(
                gp.quicksum(
                    utility_by_key.get(_assignment_key(item), item.route.score)
                    * route_vars[index]
                    for index, item in enumerate(candidate_list)
                ),
                index=1,
                priority=1,
                weight=1.0,
                name="maximize_recommendation_quality",
            )
            model.optimize()
            if (
                model.Status not in {GRB.OPTIMAL, GRB.SUBOPTIMAL, GRB.TIME_LIMIT}
                or model.SolCount < 1
            ):
                raise RuntimeError(
                    "Gurobi could not allocate the recommendation layer "
                    f"(status {model.Status})."
                )
            return tuple(
                item
                for index, item in enumerate(candidate_list)
                if route_vars[index].X > 0.5
            )
        finally:
            model.dispose()


def enumerate_assignment_candidates(
    pickups: list[BakeryPickup],
    pantries: list[Pantry],
    requests: list[DriverRequest],
    travel: TravelTimeProvider,
    weights: OptimizationWeights = OptimizationWeights(),
    pickup_service_minutes: int = 5,
    dropoff_service_minutes: int = 5,
    environmental_assumptions: EnvironmentalAssumptions = DEFAULT_ENVIRONMENTAL_ASSUMPTIONS,
) -> tuple[AssignmentCandidate, ...]:
    """Enumerate and score every feasible driver-bakery-pantry route.

    This function is the common candidate-generation boundary for the Gurobi
    model and every experimental baseline.  Keeping feasibility and scoring in
    one place guarantees that comparison policies differ only in how they
    select assignments, not in which routes they are allowed to see.
    """

    return tuple(_assignment_candidates(
        pickups,
        pantries,
        requests,
        travel,
        weights,
        pickup_service_minutes,
        dropoff_service_minutes,
        environmental_assumptions,
    ))


def _optimize_network_with_gurobi(
    candidates: list[AssignmentCandidate],
) -> NetworkOptimizationResult:
    model = _model("bakedboston_network_assignment")
    try:
        return _solve_network_model(model, candidates)
    finally:
        model.dispose()


def _solve_network_model(
    model: "gp_typing.Model",
    candidates: list[AssignmentCandidate],
) -> NetworkOptimizationResult:
    route_vars = {
        index: model.addVar(vtype=GRB.BINARY, name=f"assign_{index}")
        for index in range(len(candidates))
    }
    for request_id in sorted({candidate.request_id for candidate in candidates}):
        model.addConstr(
            gp.quicksum(
                route_vars[index]
                for index, candidate in enumerate(candidates)
                if candidate.request_id == request_id
            ) <= 1,
            name=f"driver_request_{_safe_name(request_id)}_at_most_once",
        )
    for driver_id in sorted({candidate.driver_id for candidate in candidates}):
        model.addConstr(
            gp.quicksum(
                route_vars[index]
                for index, candidate in enumerate(candidates)
                if candidate.driver_id == driver_id
            ) <= 1,
            name=f"driver_{_safe_name(driver_id)}_at_most_once",
        )
    for pickup_id in sorted({candidate.route.bakery_id for candidate in candidates}):
        model.addConstr(
            gp.quicksum(
                route_vars[index]
                for index, candidate in enumerate(candidates)
                if candidate.route.bakery_id == pickup_id
            ) <= 1,
            name=f"pickup_{_safe_name(pickup_id)}_at_most_once",
        )

    model.ModelSense = GRB.MAXIMIZE
    model.setObjectiveN(
        gp.quicksum(
            candidate.route.acceptance_probability * route_vars[index]
            for index, candidate in enumerate(candidates)
        ),
        index=0,
        priority=2,
        weight=1.0,
        reltol=0.01,
        name="maximize_expected_completed_deliveries",
    )
    model.setObjectiveN(
        gp.quicksum(
            candidate.route.score * route_vars[index]
            for index, candidate in enumerate(candidates)
        ),
        index=1,
        priority=1,
        weight=1.0,
        name="maximize_priority_and_route_quality",
    )
    model.optimize()
    if model.Status not in {GRB.OPTIMAL, GRB.SUBOPTIMAL, GRB.TIME_LIMIT} or model.SolCount < 1:
        raise RuntimeError(f"Gurobi could not solve the network assignment model (status {model.Status}).")

    assignments = tuple(
        candidate
        for index, candidate in enumerate(candidates)
        if route_vars[index].X > 0.5
    )
    assignments = tuple(sorted(
        assignments,
        key=lambda item: (item.route.depart_at, -item.route.score, item.request_id),
    ))
    return NetworkOptimizationResult(
        assignments=assignments,
        diagnostics=SolverDiagnostics(
            backend="gurobi",
            status=_status_name(model.Status),
            candidate_count=len(candidates),
            matched_count=len(assignments),
            route_quality=sum(item.route.score for item in assignments),
            runtime_seconds=float(model.Runtime),
            # Gurobi does not expose MIPGap for every multi-objective solve,
            # including small models solved entirely during presolve.
            mip_gap=_optional_float_attribute(model, "MIPGap"),
            expected_completed_deliveries=sum(
                item.route.acceptance_probability for item in assignments
            ),
            route_distance_miles=sum(
                item.route.route_distance_miles for item in assignments
            ),
            estimated_food_kg=sum(
                item.route.estimated_food_kg for item in assignments
            ),
            usable_food_kg=sum(item.route.usable_food_kg for item in assignments),
            avoided_system_kg_co2e=sum(
                item.route.avoided_system_kg_co2e for item in assignments
            ),
            transport_kg_co2e=sum(
                item.route.transport_kg_co2e for item in assignments
            ),
            residual_waste_kg_co2e=sum(
                item.route.residual_waste_kg_co2e for item in assignments
            ),
            net_environmental_benefit_kg_co2e=sum(
                item.route.net_environmental_benefit_kg_co2e
                for item in assignments
            ),
        ),
    )


def _rank_with_gurobi(candidates: list[RouteCandidate]) -> list[RouteCandidate]:
    model = _model("bakedboston_single_driver_assignment")
    try:
        model.Params.PoolSearchMode = 2
        model.Params.PoolSolutions = len(candidates)

        route_vars = [
            model.addVar(vtype=GRB.BINARY, name=f"route_{index}")
            for index, _ in enumerate(candidates)
        ]
        model.addConstr(gp.quicksum(route_vars) == 1, name="choose_one_route")
        model.setObjective(
            gp.quicksum(route.score * route_vars[index] for index, route in enumerate(candidates)),
            GRB.MAXIMIZE,
        )
        model.optimize()

        if model.Status != GRB.OPTIMAL:
            raise RuntimeError(f"Gurobi could not rank route candidates (status {model.Status}).")

        ranked: list[RouteCandidate] = []
        seen_indices: set[int] = set()
        solution_count = min(model.SolCount, len(candidates))
        for solution_number in range(solution_count):
            model.Params.SolutionNumber = solution_number
            chosen_index = next(
                (
                    index
                    for index, variable in enumerate(route_vars)
                    if variable.Xn > 0.5
                ),
                None,
            )
            if chosen_index is None or chosen_index in seen_indices:
                continue
            ranked.append(candidates[chosen_index])
            seen_indices.add(chosen_index)

        if len(ranked) == len(candidates):
            return ranked

        remaining = [
            route
            for index, route in enumerate(candidates)
            if index not in seen_indices
        ]
        return ranked + _deterministic_rank(remaining)
    finally:
        model.dispose()


def _assignment_candidates(
    pickups: list[BakeryPickup],
    pantries: list[Pantry],
    requests: list[DriverRequest],
    travel: TravelTimeProvider,
    weights: OptimizationWeights,
    pickup_service_minutes: int,
    dropoff_service_minutes: int,
    environmental_assumptions: EnvironmentalAssumptions,
) -> list[AssignmentCandidate]:
    result: list[AssignmentCandidate] = []
    for request_index, request in enumerate(requests):
        request_id = request.id or f"request-{request_index}"
        driver_id = request.driver_id or request_id
        request_routes: list[RouteCandidate] = []
        for pickup, pantry in product(pickups, pantries):
            route = _candidate(
                pickup,
                pantry,
                request,
                travel,
                weights,
                pickup_service_minutes,
                dropoff_service_minutes,
                environmental_assumptions,
            )
            if route is not None:
                request_routes.append(route)
        for route in _normalize_spatial_deviation(request_routes, request, weights):
            result.append(AssignmentCandidate(
                    request_id=request_id,
                    driver_id=driver_id,
                    route=route,
                ))
    return result


def _greedy_assignment(candidates: list[AssignmentCandidate]) -> list[AssignmentCandidate]:
    """Explicit development-only fallback; never used silently in production."""

    chosen: list[AssignmentCandidate] = []
    used_requests: set[str] = set()
    used_drivers: set[str] = set()
    used_pickups: set[str] = set()
    for candidate in sorted(
        candidates,
        key=lambda item: (
            -item.route.acceptance_probability,
            -item.route.score,
            item.route.finish_at,
        ),
    ):
        if (
            candidate.request_id in used_requests
            or candidate.driver_id in used_drivers
            or candidate.route.bakery_id in used_pickups
        ):
            continue
        chosen.append(candidate)
        used_requests.add(candidate.request_id)
        used_drivers.add(candidate.driver_id)
        used_pickups.add(candidate.route.bakery_id)
    return chosen


def _exact_recommendation_layer(
    candidates: list[AssignmentCandidate],
    utilities: dict[tuple[str, str, str], float],
) -> tuple[AssignmentCandidate, ...]:
    """Small deterministic fallback for the auxiliary menu-allocation model."""

    # Only the best pantry route matters for a given request/pickup pair in one
    # menu layer. Collapsing those alternatives keeps the exact fallback small
    # when Gurobi is unavailable without changing the assignment decision.
    best_by_request_pickup: dict[tuple[str, str], AssignmentCandidate] = {}
    for item in candidates:
        pair = (item.request_id, item.route.bakery_id)
        incumbent = best_by_request_pickup.get(pair)
        item_key = (
            -utilities.get(_assignment_key(item), item.route.score),
            item.route.pantry_id,
        )
        incumbent_key = (
            -utilities.get(_assignment_key(incumbent), incumbent.route.score),
            incumbent.route.pantry_id,
        ) if incumbent is not None else None
        if incumbent_key is None or item_key < incumbent_key:
            best_by_request_pickup[pair] = item

    options_by_request: dict[str, list[AssignmentCandidate]] = {}
    for item in best_by_request_pickup.values():
        options_by_request.setdefault(item.request_id, []).append(item)
    for request_options in options_by_request.values():
        request_options.sort(
            key=lambda item: (
                -utilities.get(_assignment_key(item), item.route.score),
                item.route.bakery_id,
                item.route.pantry_id,
            )
        )
    request_ids = sorted(options_by_request)
    best_count = -1
    best_utility = float("-inf")
    best_keys: tuple[tuple[str, str, str], ...] = ()
    best_items: tuple[AssignmentCandidate, ...] = ()

    def visit(
        request_index: int,
        chosen: tuple[AssignmentCandidate, ...],
        used_requests: frozenset[str],
        used_drivers: frozenset[str],
        used_pickups: frozenset[str],
        total_utility: float,
    ) -> None:
        nonlocal best_count, best_utility, best_keys, best_items
        if len(chosen) + len(request_ids) - request_index < best_count:
            return
        if request_index == len(request_ids):
            keys = tuple(sorted(_assignment_key(item) for item in chosen))
            if (
                len(chosen) > best_count
                or (
                    len(chosen) == best_count
                    and (
                        total_utility > best_utility + 1e-9
                        or (
                            abs(total_utility - best_utility) <= 1e-9
                            and (not best_keys or keys < best_keys)
                        )
                    )
                )
            ):
                best_count = len(chosen)
                best_utility = total_utility
                best_keys = keys
                best_items = chosen
            return

        request_id = request_ids[request_index]
        visit(
            request_index + 1,
            chosen,
            used_requests,
            used_drivers,
            used_pickups,
            total_utility,
        )
        for item in options_by_request[request_id]:
            if (
                item.request_id in used_requests
                or item.driver_id in used_drivers
                or item.route.bakery_id in used_pickups
            ):
                continue
            visit(
                request_index + 1,
                (*chosen, item),
                used_requests | {item.request_id},
                used_drivers | {item.driver_id},
                used_pickups | {item.route.bakery_id},
                total_utility
                + utilities.get(_assignment_key(item), item.route.score),
            )

    visit(0, (), frozenset(), frozenset(), frozenset(), 0.0)
    return best_items


def _assignment_key(candidate: AssignmentCandidate) -> tuple[str, str, str]:
    return (
        candidate.request_id,
        candidate.route.bakery_id,
        candidate.route.pantry_id,
    )


def _model(name: str) -> "gp_typing.Model":
    environment = _environment()
    model = gp.Model(name, env=environment) if environment is not None else gp.Model(name)
    model.Params.OutputFlag = 0
    time_limit = float(os.getenv("GUROBI_TIME_LIMIT_SECONDS", "20"))
    model.Params.TimeLimit = max(1.0, time_limit)
    return model


def _environment() -> "gp_typing.Env | None":
    global _WLS_ENVIRONMENT, _WLS_ENVIRONMENT_SIGNATURE

    access_id = os.getenv("GUROBI_WLSACCESSID", "").strip()
    secret = os.getenv("GUROBI_WLSSECRET", "").strip()
    license_id = os.getenv("GUROBI_LICENSEID", "").strip()
    configured = [bool(access_id), bool(secret), bool(license_id)]
    if any(configured) and not all(configured):
        raise GurobiUnavailableError(
            "GUROBI_WLSACCESSID, GUROBI_WLSSECRET, and GUROBI_LICENSEID must be configured together."
        )
    if not all(configured):
        return None
    signature = (access_id, secret, license_id)
    if _WLS_ENVIRONMENT is not None and _WLS_ENVIRONMENT_SIGNATURE == signature:
        return _WLS_ENVIRONMENT
    _dispose_wls_environment()
    environment = gp.Env(empty=True)
    environment.setParam("WLSAccessID", access_id)
    environment.setParam("WLSSecret", secret)
    environment.setParam("LicenseID", int(license_id))
    environment.start()
    _WLS_ENVIRONMENT = environment
    _WLS_ENVIRONMENT_SIGNATURE = signature
    return _WLS_ENVIRONMENT


def _dispose_wls_environment() -> None:
    global _WLS_ENVIRONMENT, _WLS_ENVIRONMENT_SIGNATURE

    if _WLS_ENVIRONMENT is not None:
        _WLS_ENVIRONMENT.dispose()
    _WLS_ENVIRONMENT = None
    _WLS_ENVIRONMENT_SIGNATURE = None


atexit.register(_dispose_wls_environment)


def _development_fallback_enabled() -> bool:
    return os.getenv("ALLOW_NON_GUROBI_DEVELOPMENT_FALLBACK", "").lower() in {"1", "true", "yes"}


def _deterministic_rank(candidates: list[RouteCandidate]) -> list[RouteCandidate]:
    return sorted(candidates, key=lambda route: (-route.score, route.finish_at, route.drive_minutes))


def _safe_name(value: str) -> str:
    return "".join(character if character.isalnum() else "_" for character in value)[:80]


def _status_name(status: int) -> str:
    return {
        GRB.OPTIMAL: "optimal",
        GRB.SUBOPTIMAL: "suboptimal",
        GRB.TIME_LIMIT: "time_limit_with_solution",
    }.get(status, f"status_{status}")


def _optional_float_attribute(model: "gp_typing.Model", name: str) -> float | None:
    try:
        return float(model.getAttr(name))
    except (AttributeError, gp.GurobiError):
        return None


def _candidate(
    pickup: BakeryPickup,
    pantry: Pantry,
    request: DriverRequest,
    travel: TravelTimeProvider,
    weights: OptimizationWeights,
    pickup_service_minutes: int,
    dropoff_service_minutes: int,
    environmental_assumptions: EnvironmentalAssumptions,
) -> RouteCandidate | None:
    if pickup.claimed:
        return None

    # A driver entering the system creates a decision epoch; it does not force
    # an immediate departure.  Evaluate the scheduling breakpoints at which a
    # route can become just-in-time for the bakery, pantry, or requested trip
    # interval, then keep the highest-scoring feasible timing.
    login_at = request.login_time
    initial_to_bakery = travel.duration_minutes(request.start_location, pickup.location, login_at)
    initial_leave_bakery = pickup.ready_at + timedelta(minutes=pickup_service_minutes)
    initial_to_pantry = travel.duration_minutes(pickup.location, pantry.location, initial_leave_bakery)
    rough_route_minutes = initial_to_bakery + pickup_service_minutes + initial_to_pantry + dropoff_service_minutes
    proposed_departures = {
        login_at,
        request.preferred_start,
        pickup.ready_at - timedelta(minutes=initial_to_bakery),
        pickup.pickup_deadline - timedelta(minutes=initial_to_bakery),
        pantry.receiving_start - timedelta(minutes=rough_route_minutes - dropoff_service_minutes),
        request.preferred_finish - timedelta(minutes=rough_route_minutes),
    }

    timings: list[dict[str, object]] = []
    for proposed in proposed_departures:
        depart_at = max(login_at, proposed)
        # Traffic can change with departure time. Re-evaluate twice after
        # shifting any facility wait back into pre-departure time.
        for _ in range(3):
            timing = _evaluate_timing(
                pickup,
                pantry,
                request,
                travel,
                depart_at,
                pickup_service_minutes,
                dropoff_service_minutes,
            )
            if timing is None:
                break
            facility_wait = float(timing["facility_waiting_minutes"])
            if facility_wait <= 0.01:
                break
            shifted = depart_at + timedelta(minutes=facility_wait)
            if shifted <= depart_at:
                break
            depart_at = shifted
        timing = _evaluate_timing(
            pickup,
            pantry,
            request,
            travel,
            depart_at,
            pickup_service_minutes,
            dropoff_service_minutes,
        )
        if timing is not None:
            timings.append(timing)

    if not timings:
        return None

    def timing_score(item: dict[str, object]) -> float:
        return (
            - weights.drive_minute_penalty * float(item["drive_minutes"])
            - weights.requested_time_deviation_ratio_penalty
            * float(item["requested_time_deviation_ratio"])
        )

    best = max(
        timings,
        key=lambda item: (
            timing_score(item),
            -float(item["requested_time_deviation_ratio"]),
            -float(item["facility_waiting_minutes"]),
        ),
    )
    origin_deviation_miles = _distance_outside_preference_area(
        pickup.location,
        request.start_location,
        request.start_radius_miles,
    )
    destination_deviation_miles = (
        _distance_outside_preference_area(
            pantry.location,
            request.preferred_destination,
            request.destination_radius_miles,
        )
        if request.preferred_destination is not None
        else 0.0
    )
    route_distance_miles = _haversine_miles(
        request.start_location.latitude,
        request.start_location.longitude,
        pickup.location.latitude,
        pickup.location.longitude,
    ) + _haversine_miles(
        pickup.location.latitude,
        pickup.location.longitude,
        pantry.location.latitude,
        pantry.location.longitude,
    )
    environmental_impact = estimate_route_environmental_impact(
        pickup,
        route_distance_miles,
        environmental_assumptions,
    )
    score = (
        weights.pantry_priority_reward * pantry.priority_score
        + timing_score(best)
        - weights.route_mile_penalty * route_distance_miles
        + weights.environmental_benefit_reward
        * environmental_impact.net_environmental_benefit_kg_co2e
    )
    return RouteCandidate(
        bakery_id=pickup.id,
        bakery_name=pickup.bakery_name,
        bakery_address=pickup.location.formatted_address,
        pantry_id=pantry.id,
        pantry_name=pantry.pantry_name,
        pantry_address=pantry.location.formatted_address,
        depart_at=best["depart_at"],
        pickup_at=best["pickup_at"],
        pantry_arrival_at=best["pantry_arrival_at"],
        finish_at=best["finish_at"],
        drive_minutes=float(best["drive_minutes"]),
        waiting_minutes=float(best["predeparture_waiting_minutes"]),
        destination_minutes=0.0,
        pantry_priority=pantry.priority_score,
        score=score,
        explanation=_explanation(
            pantry.priority_score,
            float(best["drive_minutes"]),
            float(best["requested_time_deviation_minutes"]),
            float(best["requested_time_deviation_ratio"]),
            origin_deviation_miles,
            destination_deviation_miles,
            0.0,
            environmental_impact.net_environmental_benefit_kg_co2e,
        ),
        facility_waiting_minutes=float(best["facility_waiting_minutes"]),
        requested_time_deviation_minutes=float(best["requested_time_deviation_minutes"]),
        requested_window_minutes=float(best["requested_window_minutes"]),
        requested_time_deviation_ratio=float(best["requested_time_deviation_ratio"]),
        within_preferred_window=bool(best["within_preferred_window"]),
        origin_deviation_miles=origin_deviation_miles,
        destination_deviation_miles=destination_deviation_miles,
        route_distance_miles=route_distance_miles,
        estimated_food_kg=environmental_impact.estimated_food_kg,
        usable_food_kg=environmental_impact.usable_food_kg,
        avoided_system_kg_co2e=environmental_impact.avoided_system_kg_co2e,
        transport_kg_co2e=environmental_impact.transport_kg_co2e,
        residual_waste_kg_co2e=environmental_impact.residual_waste_kg_co2e,
        net_environmental_benefit_kg_co2e=(
            environmental_impact.net_environmental_benefit_kg_co2e
        ),
    )


def _evaluate_timing(
    pickup: BakeryPickup,
    pantry: Pantry,
    request: DriverRequest,
    travel: TravelTimeProvider,
    depart_at: datetime,
    pickup_service_minutes: int,
    dropoff_service_minutes: int,
) -> dict[str, object] | None:
    if depart_at < request.login_time or depart_at > request.hard_search_end:
        return None

    to_bakery = travel.duration_minutes(request.start_location, pickup.location, depart_at)
    arrival_at_bakery = depart_at + timedelta(minutes=to_bakery)
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
    if finish_at > request.hard_search_end:
        return None

    early_start = max(0.0, (request.preferred_start - depart_at).total_seconds() / 60)
    late_finish = max(0.0, (finish_at - request.preferred_finish).total_seconds() / 60)
    requested_time_deviation = early_start + late_finish
    requested_window_minutes = max(
        30.0,
        (request.preferred_finish - request.preferred_start).total_seconds() / 60,
    )
    requested_time_deviation_ratio = requested_time_deviation / requested_window_minutes
    return {
        "depart_at": depart_at,
        "pickup_at": pickup_at,
        "pantry_arrival_at": pantry_arrival,
        "finish_at": finish_at,
        "drive_minutes": to_bakery + to_pantry,
        "predeparture_waiting_minutes": (depart_at - request.login_time).total_seconds() / 60,
        "facility_waiting_minutes": waiting_at_bakery + waiting_at_pantry,
        "destination_minutes": 0.0,
        "requested_time_deviation_minutes": requested_time_deviation,
        "requested_window_minutes": requested_window_minutes,
        "requested_time_deviation_ratio": requested_time_deviation_ratio,
        "within_preferred_window": requested_time_deviation <= 0.01,
    }


def _explanation(
    priority: float,
    drive_minutes: float,
    requested_time_deviation_minutes: float,
    requested_time_deviation_ratio: float,
    origin_deviation_miles: float,
    destination_deviation_miles: float,
    normalized_spatial_deviation: float,
    net_environmental_benefit_kg_co2e: float,
) -> tuple[str, ...]:
    reasons: list[str] = []
    if requested_time_deviation_minutes <= 0.01:
        reasons.append("Fits the driver's requested time window")
    if priority >= 0.7:
        reasons.append("Serves a higher-priority pantry")
    if drive_minutes <= 30:
        reasons.append("Short total driving time")
    if net_environmental_benefit_kg_co2e > 0:
        reasons.append(
            f"Estimated net lifecycle benefit: {net_environmental_benefit_kg_co2e:.1f} kg CO2e avoided"
        )
    if origin_deviation_miles <= 0.01:
        reasons.append("Bakery is inside the requested starting ZIP area")
    else:
        reasons.append(
            f"Bakery is {origin_deviation_miles:.1f} mi outside the requested starting area"
        )
    if destination_deviation_miles <= 0.01:
        reasons.append("Pantry is inside the requested destination ZIP area")
    else:
        reasons.append(
            f"Pantry is {destination_deviation_miles:.1f} mi outside the requested destination area"
        )
    if requested_time_deviation_minutes > 0.01:
        reasons.append(
            f"Uses {requested_time_deviation_ratio:.0%} of the requested-window length outside the preferred time"
        )
    if not reasons:
        reasons.append("Best available balance of feasibility and priority")
    return tuple(reasons)


def _distance_outside_preference_area(
    facility: Location,
    preference_center: Location,
    radius_miles: float,
) -> float:
    """Shortest straight-line distance from a facility to a preference circle.

    A facility inside the requested ZIP approximation has zero deviation. A
    facility outside it is measured to the closest point on the circle, not to
    its center.
    """

    miles_to_center = _haversine_miles(
        facility.latitude,
        facility.longitude,
        preference_center.latitude,
        preference_center.longitude,
    )
    return max(0.0, miles_to_center - max(0.0, radius_miles))


def _normalize_spatial_deviation(
    routes: list[RouteCandidate],
    request: DriverRequest,
    weights: OptimizationWeights,
) -> list[RouteCandidate]:
    """Scale spatial misses against the feasible alternatives for one driver.

    Each component is divided by the largest feasible miss for that request.
    Exact ZIP-area matches therefore remain zero; lower ratios are always
    preferred, and no route receives a positive bonus for an exact match.
    """

    if not routes:
        return []
    max_origin = max(route.origin_deviation_miles for route in routes)
    max_destination = max(route.destination_deviation_miles for route in routes)
    has_destination = request.preferred_destination is not None
    normalized: list[RouteCandidate] = []
    for route in routes:
        origin_ratio = (
            route.origin_deviation_miles / max_origin if max_origin > 0 else 0.0
        )
        destination_ratio = (
            route.destination_deviation_miles / max_destination
            if has_destination and max_destination > 0
            else 0.0
        )
        component_count = 2 if has_destination else 1
        spatial_ratio = (origin_ratio + destination_ratio) / component_count
        score = route.score - weights.spatial_deviation_ratio_penalty * spatial_ratio
        updated = replace(
            route,
            score=score,
            normalized_origin_deviation=origin_ratio,
            normalized_destination_deviation=destination_ratio,
            normalized_spatial_deviation=spatial_ratio,
            explanation=_explanation(
                route.pantry_priority,
                route.drive_minutes,
                route.requested_time_deviation_minutes,
                route.requested_time_deviation_ratio,
                route.origin_deviation_miles,
                route.destination_deviation_miles,
                spatial_ratio,
                route.net_environmental_benefit_kg_co2e,
            ),
        )
        normalized.append(replace(
            updated,
            acceptance_probability=estimate_acceptance_probability(updated),
        ))
    return normalized


def estimate_acceptance_probability(
    route: RouteCandidate,
    model: ParticipationModel = ParticipationModel(),
) -> float:
    """Estimate route acceptance for an academic simulation scenario.

    The logistic form is deliberately inspectable.  Longer driving, a larger
    proportional miss of the requested time interval, and a larger normalized
    miss of the requested start/destination areas all reduce acceptance.  The
    output is bounded away from zero and one for numerically stable experiments.
    """

    logit = (
        model.intercept
        - model.drive_minute_penalty * route.drive_minutes
        - model.requested_time_deviation_ratio_penalty
        * route.requested_time_deviation_ratio
        - model.spatial_deviation_ratio_penalty
        * route.normalized_spatial_deviation
    )
    probability = 1.0 / (1.0 + math.exp(-logit))
    return min(0.98, max(0.02, probability))
