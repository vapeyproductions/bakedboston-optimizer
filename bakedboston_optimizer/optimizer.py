from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from itertools import product
import atexit
import os
from threading import RLock
from typing import TYPE_CHECKING

from .models import (
    AssignmentCandidate,
    BakeryPickup,
    DriverRequest,
    NetworkOptimizationResult,
    Pantry,
    RouteCandidate,
    SolverDiagnostics,
)
from .travel import TravelTimeProvider

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
    waiting_minute_penalty: float = 0.35
    destination_minute_penalty: float = 0.65


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
        )
        if candidate is not None:
            feasible_candidates.append(candidate)
    if not feasible_candidates:
        return []

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
) -> NetworkOptimizationResult:
    """Select a globally consistent set of driver-pickup-pantry assignments.

    The first objective maximizes completed bakery pickups. The second objective
    maximizes route quality, which rewards pantry priority and penalizes driving,
    waiting, and distance from a driver's preferred destination. Pantries do not
    receive a capacity constraint because BakedBoston intentionally allows a
    pantry to accept multiple deliveries while its receiving window is open.
    """

    candidates = list(enumerate_assignment_candidates(
        pickups,
        pantries,
        requests,
        travel,
        weights,
        pickup_service_minutes,
        dropoff_service_minutes,
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
            ),
        )

    with _SOLVER_LOCK:
        return _optimize_network_with_gurobi(candidate_list)


def enumerate_assignment_candidates(
    pickups: list[BakeryPickup],
    pantries: list[Pantry],
    requests: list[DriverRequest],
    travel: TravelTimeProvider,
    weights: OptimizationWeights = OptimizationWeights(),
    pickup_service_minutes: int = 5,
    dropoff_service_minutes: int = 5,
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
        gp.quicksum(route_vars.values()),
        index=0,
        priority=2,
        weight=1.0,
        name="maximize_completed_pickups",
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
) -> list[AssignmentCandidate]:
    result: list[AssignmentCandidate] = []
    for request_index, request in enumerate(requests):
        request_id = request.id or f"request-{request_index}"
        driver_id = request.driver_id or request_id
        for pickup, pantry in product(pickups, pantries):
            route = _candidate(
                pickup,
                pantry,
                request,
                travel,
                weights,
                pickup_service_minutes,
                dropoff_service_minutes,
            )
            if route is not None:
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
    for candidate in sorted(candidates, key=lambda item: -item.route.score):
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
