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
    """Weights for the normalized 100-point second-stage objective."""

    pantry_coverage_reward: float = 10.0
    raw_food_volume_reward: float = 10.0
    raw_food_evenness_reward: float = 10.0
    saved_food_volume_reward: float = 10.0
    saved_food_evenness_reward: float = 10.0
    pantry_priority_reward: float = 10.0
    environmental_benefit_reward: float = 20.0
    driver_fit_reward: float = 20.0
    # The following fields retain the route-level timing model used to choose
    # each feasible route's best departure time and rank a single-driver menu.
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
    # zero because vehicle emissions are already included in the direct
    # net environmental benefit below rather than charged twice.
    route_mile_penalty: float = 0.0


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

    Stage one maximizes expected completed pickups. Within one percent of that
    optimum, stage two balances eight normalized terms: pantry coverage, raw
    food volume and evenness, ultimately saved food volume and evenness,
    historical pantry opportunity priority, direct net CO2e benefit, and driver
    fit. Pantries may receive multiple deliveries during an open window.
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
    return optimize_assignment_candidates(candidates, weights=weights)


def optimize_assignment_candidates(
    candidates: list[AssignmentCandidate] | tuple[AssignmentCandidate, ...],
    weights: OptimizationWeights = OptimizationWeights(),
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
                route_quality=balanced_objective_value(
                    candidate_list, assignments, weights
                ),
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
                food_saved_kg=sum(item.route.food_saved_kg for item in assignments),
                collected_not_distributed_kg=sum(
                    item.route.collected_not_distributed_kg for item in assignments
                ),
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
        return _optimize_network_with_gurobi(candidate_list, weights)


def optimize_nair_distance_first_candidates(
    candidates: list[AssignmentCandidate] | tuple[AssignmentCandidate, ...],
) -> NetworkOptimizationResult:
    """Solve the minimal volunteer-route adaptation of Nair et al. (2018).

    The original PU-PDVRP minimizes transportation cost while requiring its
    scheduled pickup and delivery nodes to be served. BakedBoston has a scarce,
    request-driven volunteer fleet, so mandatory service is relaxed only as far
    as necessary: first maximize the number of assigned food-ready pickups,
    then minimize total route distance. Current driver location, facility
    windows, and the driver's hard search horizon are already embedded in the
    shared feasible candidate set. Soft driver preferences, acceptance,
    fairness, food-distribution, and environmental terms do not enter this
    comparator's objective.
    """

    candidate_list = list(candidates)
    if not candidate_list:
        return NetworkOptimizationResult(
            assignments=(),
            diagnostics=SolverDiagnostics(
                backend=(
                    "gurobi:nair_2018_distance_first_adaptation"
                    if gp is not None
                    else "unavailable:nair_2018_distance_first_adaptation"
                ),
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
        utilities = {
            _assignment_key(item): -item.route.route_distance_miles
            for item in candidate_list
        }
        assignments = _exact_recommendation_layer(candidate_list, utilities)
        assignments = tuple(sorted(
            assignments,
            key=lambda item: (
                item.route.depart_at,
                item.route.route_distance_miles,
                item.request_id,
            ),
        ))
        return NetworkOptimizationResult(
            assignments=assignments,
            diagnostics=_nair_assignment_diagnostics(
                candidate_list,
                assignments,
                backend="development_exact:nair_2018_distance_first_adaptation",
                status="fallback",
                runtime_seconds=0.0,
            ),
        )

    with _SOLVER_LOCK:
        model = _model("nair_2018_distance_first_adaptation")
        try:
            route_vars = {
                index: model.addVar(vtype=GRB.BINARY, name=f"assign_{index}")
                for index in range(len(candidate_list))
            }
            for request_id in sorted({item.request_id for item in candidate_list}):
                model.addConstr(
                    gp.quicksum(
                        route_vars[index]
                        for index, item in enumerate(candidate_list)
                        if item.request_id == request_id
                    ) <= 1,
                    name=f"request_{_safe_name(request_id)}_at_most_once",
                )
            for driver_id in sorted({item.driver_id for item in candidate_list}):
                model.addConstr(
                    gp.quicksum(
                        route_vars[index]
                        for index, item in enumerate(candidate_list)
                        if item.driver_id == driver_id
                    ) <= 1,
                    name=f"driver_{_safe_name(driver_id)}_at_most_once",
                )
            for pickup_id in sorted({item.route.bakery_id for item in candidate_list}):
                model.addConstr(
                    gp.quicksum(
                        route_vars[index]
                        for index, item in enumerate(candidate_list)
                        if item.route.bakery_id == pickup_id
                    ) <= 1,
                    name=f"pickup_{_safe_name(pickup_id)}_at_most_once",
                )

            model.ModelSense = GRB.MAXIMIZE
            model.setObjectiveN(
                gp.quicksum(route_vars.values()),
                index=0,
                priority=2,
                weight=1.0,
                name="maximize_served_food_ready_pickups",
            )
            model.setObjectiveN(
                -gp.quicksum(
                    item.route.route_distance_miles * route_vars[index]
                    for index, item in enumerate(candidate_list)
                ),
                index=1,
                priority=1,
                weight=1.0,
                name="minimize_total_route_distance",
            )
            model.optimize()
            if (
                model.Status not in {GRB.OPTIMAL, GRB.SUBOPTIMAL, GRB.TIME_LIMIT}
                or model.SolCount < 1
            ):
                raise RuntimeError(
                    "Gurobi could not solve the Nair et al. distance-first "
                    f"adaptation (status {model.Status})."
                )
            assignments = tuple(
                item
                for index, item in enumerate(candidate_list)
                if route_vars[index].X > 0.5
            )
            assignments = tuple(sorted(
                assignments,
                key=lambda item: (
                    item.route.depart_at,
                    item.route.route_distance_miles,
                    item.request_id,
                ),
            ))
            return NetworkOptimizationResult(
                assignments=assignments,
                diagnostics=_nair_assignment_diagnostics(
                    candidate_list,
                    assignments,
                    backend="gurobi:nair_2018_distance_first_adaptation",
                    status=_status_name(model.Status),
                    runtime_seconds=float(model.Runtime),
                    mip_gap=_optional_float_attribute(model, "MIPGap"),
                ),
            )
        finally:
            model.dispose()


def _nair_assignment_diagnostics(
    candidates: Sequence[AssignmentCandidate],
    assignments: Sequence[AssignmentCandidate],
    *,
    backend: str,
    status: str,
    runtime_seconds: float,
    mip_gap: float | None = None,
) -> SolverDiagnostics:
    """Report the comparator objective and common post-hoc impact ledger."""

    total_distance = sum(item.route.route_distance_miles for item in assignments)
    return SolverDiagnostics(
        backend=backend,
        status=status,
        candidate_count=len(candidates),
        matched_count=len(assignments),
        route_quality=-total_distance,
        runtime_seconds=runtime_seconds,
        mip_gap=mip_gap,
        expected_completed_deliveries=sum(
            item.route.acceptance_probability for item in assignments
        ),
        route_distance_miles=total_distance,
        estimated_food_kg=sum(item.route.estimated_food_kg for item in assignments),
        usable_food_kg=sum(item.route.usable_food_kg for item in assignments),
        food_saved_kg=sum(item.route.food_saved_kg for item in assignments),
        collected_not_distributed_kg=sum(
            item.route.collected_not_distributed_kg for item in assignments
        ),
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
            item.route.net_environmental_benefit_kg_co2e for item in assignments
        ),
    )


def optimize_xue_zou_total_curb_candidates(
    candidates: list[AssignmentCandidate] | tuple[AssignmentCandidate, ...],
) -> NetworkOptimizationResult:
    """Solve the minimal Total-Curb adaptation of Xue and Zou (2025).

    The source model requires all known pickup-delivery orders to be served and
    minimizes total meal, waste, and vehicle emissions. BakedBoston has a
    request-driven volunteer fleet, so the adaptation first maximizes the
    number of assigned food-ready pickups. It then minimizes the current
    system's direct emissions: uncollected bakery-waste emissions for pickups
    left unassigned plus residual-waste and transport emissions for selected
    routes. Since the all-uncollected outcome is constant within an epoch, that
    second objective is equivalent to maximizing the direct emissions avoided
    by selected routes.

    The shared candidate generator already enforces current driver origin,
    facility windows, and the hard search horizon. The comparator does not use
    soft preferences, acceptance, pantry fairness or priority, avoided
    production, meal packaging, or invented driver-familiarity values.
    """

    candidate_list = list(candidates)
    if not candidate_list:
        return NetworkOptimizationResult(
            assignments=(),
            diagnostics=SolverDiagnostics(
                backend=(
                    "gurobi:xue_zou_2025_total_curb_adaptation"
                    if gp is not None
                    else "unavailable:xue_zou_2025_total_curb_adaptation"
                ),
                status="no_feasible_assignments",
                candidate_count=0,
                matched_count=0,
                route_quality=0.0,
                runtime_seconds=0.0,
            ),
        )

    utilities = {
        _assignment_key(item): _direct_system_benefit_kg_co2e(item)
        for item in candidate_list
    }
    if gp is None:
        if not _development_fallback_enabled():
            raise GurobiUnavailableError(
                "Gurobi is required, but gurobipy is not installed in the optimizer runtime."
            )
        assignments = _exact_recommendation_layer(candidate_list, utilities)
        assignments = tuple(sorted(
            assignments,
            key=lambda item: (
                item.route.depart_at,
                -_direct_system_benefit_kg_co2e(item),
                item.request_id,
            ),
        ))
        return NetworkOptimizationResult(
            assignments=assignments,
            diagnostics=_xue_zou_assignment_diagnostics(
                candidate_list,
                assignments,
                backend="development_exact:xue_zou_2025_total_curb_adaptation",
                status="fallback",
                runtime_seconds=0.0,
            ),
        )

    with _SOLVER_LOCK:
        model = _model("xue_zou_2025_total_curb_adaptation")
        try:
            route_vars = {
                index: model.addVar(vtype=GRB.BINARY, name=f"assign_{index}")
                for index in range(len(candidate_list))
            }
            for request_id in sorted({item.request_id for item in candidate_list}):
                model.addConstr(
                    gp.quicksum(
                        route_vars[index]
                        for index, item in enumerate(candidate_list)
                        if item.request_id == request_id
                    ) <= 1,
                    name=f"request_{_safe_name(request_id)}_at_most_once",
                )
            for driver_id in sorted({item.driver_id for item in candidate_list}):
                model.addConstr(
                    gp.quicksum(
                        route_vars[index]
                        for index, item in enumerate(candidate_list)
                        if item.driver_id == driver_id
                    ) <= 1,
                    name=f"driver_{_safe_name(driver_id)}_at_most_once",
                )
            for pickup_id in sorted({item.route.bakery_id for item in candidate_list}):
                model.addConstr(
                    gp.quicksum(
                        route_vars[index]
                        for index, item in enumerate(candidate_list)
                        if item.route.bakery_id == pickup_id
                    ) <= 1,
                    name=f"pickup_{_safe_name(pickup_id)}_at_most_once",
                )

            model.ModelSense = GRB.MAXIMIZE
            model.setObjectiveN(
                gp.quicksum(route_vars.values()),
                index=0,
                priority=2,
                weight=1.0,
                name="maximize_served_food_ready_pickups",
            )
            model.setObjectiveN(
                gp.quicksum(
                    utilities[_assignment_key(item)] * route_vars[index]
                    for index, item in enumerate(candidate_list)
                ),
                index=1,
                priority=1,
                weight=1.0,
                name="minimize_total_direct_system_co2e",
            )
            model.optimize()
            if (
                model.Status not in {GRB.OPTIMAL, GRB.SUBOPTIMAL, GRB.TIME_LIMIT}
                or model.SolCount < 1
            ):
                raise RuntimeError(
                    "Gurobi could not solve the Xue-Zou Total-Curb adaptation "
                    f"(status {model.Status})."
                )
            assignments = tuple(
                item
                for index, item in enumerate(candidate_list)
                if route_vars[index].X > 0.5
            )
            assignments = tuple(sorted(
                assignments,
                key=lambda item: (
                    item.route.depart_at,
                    -_direct_system_benefit_kg_co2e(item),
                    item.request_id,
                ),
            ))
            return NetworkOptimizationResult(
                assignments=assignments,
                diagnostics=_xue_zou_assignment_diagnostics(
                    candidate_list,
                    assignments,
                    backend="gurobi:xue_zou_2025_total_curb_adaptation",
                    status=_status_name(model.Status),
                    runtime_seconds=float(model.Runtime),
                    mip_gap=_optional_float_attribute(model, "MIPGap"),
                ),
            )
        finally:
            model.dispose()


def _direct_system_benefit_kg_co2e(candidate: AssignmentCandidate) -> float:
    """Return direct waste-pathway emissions avoided minus route transport."""

    route = candidate.route
    return (
        route.counterfactual_waste_kg_co2e
        - route.residual_waste_kg_co2e
        - route.transport_kg_co2e
    )


def _xue_zou_assignment_diagnostics(
    candidates: Sequence[AssignmentCandidate],
    assignments: Sequence[AssignmentCandidate],
    *,
    backend: str,
    status: str,
    runtime_seconds: float,
    mip_gap: float | None = None,
) -> SolverDiagnostics:
    """Report the Total-Curb objective and common post-hoc impact ledger."""

    return SolverDiagnostics(
        backend=backend,
        status=status,
        candidate_count=len(candidates),
        matched_count=len(assignments),
        route_quality=sum(
            _direct_system_benefit_kg_co2e(item) for item in assignments
        ),
        runtime_seconds=runtime_seconds,
        mip_gap=mip_gap,
        expected_completed_deliveries=sum(
            item.route.acceptance_probability for item in assignments
        ),
        route_distance_miles=sum(
            item.route.route_distance_miles for item in assignments
        ),
        estimated_food_kg=sum(item.route.estimated_food_kg for item in assignments),
        usable_food_kg=sum(item.route.usable_food_kg for item in assignments),
        food_saved_kg=sum(item.route.food_saved_kg for item in assignments),
        collected_not_distributed_kg=sum(
            item.route.collected_not_distributed_kg for item in assignments
        ),
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
            item.route.net_environmental_benefit_kg_co2e for item in assignments
        ),
    )


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
    weights: OptimizationWeights,
) -> NetworkOptimizationResult:
    model = _model("bakedboston_network_assignment")
    try:
        return _solve_network_model(model, candidates, weights)
    finally:
        model.dispose()


def _solve_network_model(
    model: "gp_typing.Model",
    candidates: list[AssignmentCandidate],
    weights: OptimizationWeights,
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

    pantry_keys = sorted({_pantry_identity(item.route.pantry_id) for item in candidates})
    pantry_indices = {
        pantry_key: [
            index
            for index, candidate in enumerate(candidates)
            if _pantry_identity(candidate.route.pantry_id) == pantry_key
        ]
        for pantry_key in pantry_keys
    }
    visit_vars = {
        pantry_key: model.addVar(
            vtype=GRB.BINARY,
            name=f"pantry_served_{_safe_name(pantry_key)}",
        )
        for pantry_key in pantry_keys
    }
    for pantry_key, indices in pantry_indices.items():
        assignments_to_pantry = gp.quicksum(route_vars[index] for index in indices)
        model.addConstr(
            visit_vars[pantry_key] <= assignments_to_pantry,
            name=f"pantry_visit_lower_{_safe_name(pantry_key)}",
        )
        model.addConstr(
            assignments_to_pantry <= len(indices) * visit_vars[pantry_key],
            name=f"pantry_visit_upper_{_safe_name(pantry_key)}",
        )

    unique_pickups = sorted({item.route.bakery_id for item in candidates})
    max_assignments = max(
        1,
        min(
            len({item.request_id for item in candidates}),
            len({item.driver_id for item in candidates}),
            len(unique_pickups),
        ),
    )
    raw_capacity = max(
        1.0,
        sum(
            next(
                item.route.estimated_food_kg
                for item in candidates
                if item.route.bakery_id == pickup_id
            )
            for pickup_id in unique_pickups
        ),
    )
    saved_capacity = max(
        1.0,
        sum(
            max(
                item.route.food_saved_kg
                for item in candidates
                if item.route.bakery_id == pickup_id
            )
            for pickup_id in unique_pickups
        ),
    )
    raw_totals: dict[str, object] = {}
    saved_totals: dict[str, object] = {}
    for pantry_key, indices in pantry_indices.items():
        representative = candidates[indices[0]].route
        raw_totals[pantry_key] = representative.pantry_historical_raw_food_kg + gp.quicksum(
            candidates[index].route.estimated_food_kg * route_vars[index]
            for index in indices
        )
        saved_totals[pantry_key] = representative.pantry_historical_saved_food_kg + gp.quicksum(
            candidates[index].route.food_saved_kg * route_vars[index]
            for index in indices
        )

    raw_gaps = []
    saved_gaps = []
    for left_index, left in enumerate(pantry_keys):
        for right in pantry_keys[left_index + 1:]:
            raw_gap = model.addVar(
                lb=0.0,
                name=f"raw_gap_{_safe_name(left)}_{_safe_name(right)}",
            )
            saved_gap = model.addVar(
                lb=0.0,
                name=f"saved_gap_{_safe_name(left)}_{_safe_name(right)}",
            )
            model.addConstr(raw_gap >= raw_totals[left] - raw_totals[right])
            model.addConstr(raw_gap >= raw_totals[right] - raw_totals[left])
            model.addConstr(saved_gap >= saved_totals[left] - saved_totals[right])
            model.addConstr(saved_gap >= saved_totals[right] - saved_totals[left])
            raw_gaps.append(raw_gap)
            saved_gaps.append(saved_gap)

    history_raw = sum(
        candidates[indices[0]].route.pantry_historical_raw_food_kg
        for indices in pantry_indices.values()
    )
    history_saved = sum(
        candidates[indices[0]].route.pantry_historical_saved_food_kg
        for indices in pantry_indices.values()
    )
    evenness_multiplier = max(1, len(pantry_keys) - 1)
    raw_evenness = 1.0 - gp.quicksum(raw_gaps) / (
        evenness_multiplier * (history_raw + raw_capacity)
    )
    saved_evenness = 1.0 - gp.quicksum(saved_gaps) / (
        evenness_multiplier * (history_saved + saved_capacity)
    )
    net_values = [item.route.net_environmental_benefit_kg_co2e for item in candidates]
    net_min = min(net_values)
    net_span = max(net_values) - net_min
    environment_scores = [
        ((value - net_min) / net_span if net_span > 1e-9 else 0.5)
        for value in net_values
    ]
    max_drive = max(1.0, max(item.route.drive_minutes for item in candidates))
    driver_fit_scores = [
        max(
            0.0,
            min(
                1.0,
                1.0
                - (
                    min(1.0, item.route.drive_minutes / max_drive)
                    + min(1.0, item.route.requested_time_deviation_ratio)
                    + min(1.0, item.route.normalized_spatial_deviation)
                ) / 3.0,
            ),
        )
        for item in candidates
    ]
    coverage_component = gp.quicksum(visit_vars.values()) / max(1, len(pantry_keys))
    raw_volume_component = gp.quicksum(
        item.route.estimated_food_kg * route_vars[index]
        for index, item in enumerate(candidates)
    ) / raw_capacity
    saved_volume_component = gp.quicksum(
        item.route.food_saved_kg * route_vars[index]
        for index, item in enumerate(candidates)
    ) / saved_capacity
    priority_component = gp.quicksum(
        item.route.pantry_priority * route_vars[index]
        for index, item in enumerate(candidates)
    ) / max_assignments
    environment_component = gp.quicksum(
        environment_scores[index] * route_vars[index]
        for index in range(len(candidates))
    ) / max_assignments
    driver_fit_component = gp.quicksum(
        driver_fit_scores[index] * route_vars[index]
        for index in range(len(candidates))
    ) / max_assignments
    balanced_objective = (
        weights.pantry_coverage_reward * coverage_component
        + weights.raw_food_volume_reward * raw_volume_component
        + weights.raw_food_evenness_reward * raw_evenness
        + weights.saved_food_volume_reward * saved_volume_component
        + weights.saved_food_evenness_reward * saved_evenness
        + weights.pantry_priority_reward * priority_component
        + weights.environmental_benefit_reward * environment_component
        + weights.driver_fit_reward * driver_fit_component
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
        balanced_objective,
        index=1,
        priority=1,
        weight=1.0,
        name="maximize_normalized_food_fairness_environment_and_driver_fit",
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
            route_quality=balanced_objective_value(candidates, assignments, weights),
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
            food_saved_kg=sum(item.route.food_saved_kg for item in assignments),
            collected_not_distributed_kg=sum(
                item.route.collected_not_distributed_kg for item in assignments
            ),
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


def _pantry_identity(value: str) -> str:
    """Collapse recurring/one-time window IDs to the underlying pantry."""

    parts = value.split(":")
    return parts[1] if value.startswith("pantry:") and len(parts) > 1 else parts[0]


def balanced_objective_value(
    candidates: Sequence[AssignmentCandidate],
    assignments: Sequence[AssignmentCandidate],
    weights: OptimizationWeights = OptimizationWeights(),
) -> float:
    """Evaluate the normalized second-stage objective for an auditable result."""

    if not candidates:
        return 0.0
    pantry_keys = sorted({_pantry_identity(item.route.pantry_id) for item in candidates})
    unique_pickups = sorted({item.route.bakery_id for item in candidates})
    max_assignments = max(
        1,
        min(
            len({item.request_id for item in candidates}),
            len({item.driver_id for item in candidates}),
            len(unique_pickups),
        ),
    )
    raw_capacity = max(1.0, sum(
        next(item.route.estimated_food_kg for item in candidates if item.route.bakery_id == pickup_id)
        for pickup_id in unique_pickups
    ))
    saved_capacity = max(1.0, sum(
        max(item.route.food_saved_kg for item in candidates if item.route.bakery_id == pickup_id)
        for pickup_id in unique_pickups
    ))
    raw_totals: dict[str, float] = {}
    saved_totals: dict[str, float] = {}
    for pantry_key in pantry_keys:
        representative = next(
            item.route for item in candidates
            if _pantry_identity(item.route.pantry_id) == pantry_key
        )
        raw_totals[pantry_key] = representative.pantry_historical_raw_food_kg + sum(
            item.route.estimated_food_kg for item in assignments
            if _pantry_identity(item.route.pantry_id) == pantry_key
        )
        saved_totals[pantry_key] = representative.pantry_historical_saved_food_kg + sum(
            item.route.food_saved_kg for item in assignments
            if _pantry_identity(item.route.pantry_id) == pantry_key
        )
    raw_gaps = sum(
        abs(raw_totals[left] - raw_totals[right])
        for left_index, left in enumerate(pantry_keys)
        for right in pantry_keys[left_index + 1:]
    )
    saved_gaps = sum(
        abs(saved_totals[left] - saved_totals[right])
        for left_index, left in enumerate(pantry_keys)
        for right in pantry_keys[left_index + 1:]
    )
    history_raw = sum(next(
        item.route.pantry_historical_raw_food_kg for item in candidates
        if _pantry_identity(item.route.pantry_id) == pantry_key
    ) for pantry_key in pantry_keys)
    history_saved = sum(next(
        item.route.pantry_historical_saved_food_kg for item in candidates
        if _pantry_identity(item.route.pantry_id) == pantry_key
    ) for pantry_key in pantry_keys)
    evenness_multiplier = max(1, len(pantry_keys) - 1)
    raw_evenness = 1.0 - raw_gaps / (evenness_multiplier * (history_raw + raw_capacity))
    saved_evenness = 1.0 - saved_gaps / (evenness_multiplier * (history_saved + saved_capacity))
    net_values = [item.route.net_environmental_benefit_kg_co2e for item in candidates]
    net_min = min(net_values)
    net_span = max(net_values) - net_min
    environment_component = sum(
        ((item.route.net_environmental_benefit_kg_co2e - net_min) / net_span if net_span > 1e-9 else 0.5)
        for item in assignments
    ) / max_assignments
    max_drive = max(1.0, max(item.route.drive_minutes for item in candidates))
    driver_fit_component = sum(
        max(0.0, min(1.0, 1.0 - (
            min(1.0, item.route.drive_minutes / max_drive)
            + min(1.0, item.route.requested_time_deviation_ratio)
            + min(1.0, item.route.normalized_spatial_deviation)
        ) / 3.0))
        for item in assignments
    ) / max_assignments
    coverage = len({_pantry_identity(item.route.pantry_id) for item in assignments}) / max(1, len(pantry_keys))
    raw_volume = sum(item.route.estimated_food_kg for item in assignments) / raw_capacity
    saved_volume = sum(item.route.food_saved_kg for item in assignments) / saved_capacity
    priority = sum(item.route.pantry_priority for item in assignments) / max_assignments
    return (
        weights.pantry_coverage_reward * coverage
        + weights.raw_food_volume_reward * raw_volume
        + weights.raw_food_evenness_reward * raw_evenness
        + weights.saved_food_volume_reward * saved_volume
        + weights.saved_food_evenness_reward * saved_evenness
        + weights.pantry_priority_reward * priority
        + weights.environmental_benefit_reward * environment_component
        + weights.driver_fit_reward * driver_fit_component
    )


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
        pantry_distribution_fraction=pantry.distribution_fraction,
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
        bakery_usable_fraction=pickup.usable_fraction,
        pantry_distribution_fraction=pantry.distribution_fraction,
        pantry_historical_raw_food_kg=pantry.historical_raw_food_kg,
        pantry_historical_saved_food_kg=pantry.historical_saved_food_kg,
        food_saved_kg=environmental_impact.food_saved_kg,
        collected_not_distributed_kg=(
            environmental_impact.collected_not_distributed_kg
        ),
        counterfactual_waste_kg_co2e=(
            environmental_impact.counterfactual_waste_kg_co2e
        ),
        route_waste_kg_co2e=environmental_impact.route_waste_kg_co2e,
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
            f"Estimated net direct benefit: {net_environmental_benefit_kg_co2e:.1f} kg CO2e avoided"
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
