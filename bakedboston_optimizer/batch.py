from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

from .models import BakeryPickup, DriverRequest, Pantry, RouteCandidate
from .optimizer import OptimizationWeights, rank_routes
from .travel import TravelTimeProvider


@dataclass(frozen=True)
class AssignmentCandidate:
    """A complete, time-feasible route available to one driver."""

    driver_id: str
    route: RouteCandidate
    request_id: str = ""
    candidate_id: str = ""


@dataclass(frozen=True)
class BatchAssignmentResult:
    """The selected routes and auditable solver summary."""

    assignments: tuple[AssignmentCandidate, ...]
    unmatched_driver_ids: tuple[str, ...]
    candidate_count: int
    assignment_count: int
    route_score: float
    solver: str
    status: str


def build_assignment_candidates(
    drivers: Mapping[str, DriverRequest],
    pickups: list[BakeryPickup],
    pantries: list[Pantry],
    travel: TravelTimeProvider,
    weights: OptimizationWeights = OptimizationWeights(),
    pickup_service_minutes: int = 5,
    dropoff_service_minutes: int = 5,
) -> list[AssignmentCandidate]:
    """Generate every feasible driver-pickup-pantry assignment."""

    candidates: list[AssignmentCandidate] = []
    for driver_id, request in drivers.items():
        candidates.extend(
            AssignmentCandidate(driver_id=driver_id, route=route)
            for route in rank_routes(
                pickups,
                pantries,
                request,
                travel,
                weights,
                pickup_service_minutes,
                dropoff_service_minutes,
            )
        )
    return candidates


def optimize_batch(
    drivers: Mapping[str, DriverRequest],
    pickups: list[BakeryPickup],
    pantries: list[Pantry],
    travel: TravelTimeProvider,
    weights: OptimizationWeights = OptimizationWeights(),
    pickup_service_minutes: int = 5,
    dropoff_service_minutes: int = 5,
    time_limit_seconds: float = 10.0,
) -> BatchAssignmentResult:
    """Build feasible candidates and solve the two-stage binary assignment model."""

    candidates = build_assignment_candidates(
        drivers,
        pickups,
        pantries,
        travel,
        weights,
        pickup_service_minutes,
        dropoff_service_minutes,
    )
    return solve_assignment(
        candidates,
        driver_ids=tuple(drivers),
        time_limit_seconds=time_limit_seconds,
    )


def solve_assignment(
    candidates: Sequence[AssignmentCandidate],
    driver_ids: Sequence[str] | None = None,
    time_limit_seconds: float = 10.0,
) -> BatchAssignmentResult:
    """Solve a maximum-cardinality, maximum-quality binary assignment.

    Stage one maximizes the number of assigned bakery pickups. Stage two fixes
    that number and maximizes the route score. This implements BakedBoston's
    policy that completing a feasible delivery is always preferable to leaving
    it unassigned, even when a closer pantry has recently received deliveries.
    """

    try:
        from ortools.linear_solver import pywraplp
    except ModuleNotFoundError as error:  # pragma: no cover - exercised in installation failures
        raise RuntimeError(
            "OR-Tools is required for batch optimization. Install the project with `pip install -e '.[mip]'`."
        ) from error

    all_driver_ids = tuple(dict.fromkeys(driver_ids or (candidate.driver_id for candidate in candidates)))
    if not candidates:
        return BatchAssignmentResult(
            assignments=(),
            unmatched_driver_ids=all_driver_ids,
            candidate_count=0,
            assignment_count=0,
            route_score=0.0,
            solver="OR-Tools CBC",
            status="optimal",
        )

    maximum_assignments, stage_one_status = _solve(
        candidates,
        pywraplp,
        objective="cardinality",
        required_assignments=None,
        time_limit_seconds=time_limit_seconds,
    )
    if maximum_assignments is None:
        return _failed_result(candidates, all_driver_ids, stage_one_status)

    selected, stage_two_status = _solve(
        candidates,
        pywraplp,
        objective="score",
        required_assignments=len(maximum_assignments),
        time_limit_seconds=time_limit_seconds,
    )
    if selected is None:
        return _failed_result(candidates, all_driver_ids, stage_two_status)

    assignments = tuple(
        sorted(
            (candidates[index] for index in selected),
            key=lambda candidate: (candidate.route.depart_at, candidate.driver_id, candidate.route.bakery_id),
        )
    )
    assigned_driver_ids = {candidate.driver_id for candidate in assignments}
    return BatchAssignmentResult(
        assignments=assignments,
        unmatched_driver_ids=tuple(driver_id for driver_id in all_driver_ids if driver_id not in assigned_driver_ids),
        candidate_count=len(candidates),
        assignment_count=len(assignments),
        route_score=sum(candidate.route.score for candidate in assignments),
        solver="OR-Tools CBC",
        status=stage_two_status,
    )


def _solve(
    candidates: Sequence[AssignmentCandidate],
    pywraplp: object,
    objective: str,
    required_assignments: int | None,
    time_limit_seconds: float,
) -> tuple[list[int] | None, str]:
    solver = pywraplp.Solver.CreateSolver("CBC_MIXED_INTEGER_PROGRAMMING")
    if solver is None:
        raise RuntimeError("The OR-Tools CBC mixed-integer solver is unavailable.")
    solver.SetTimeLimit(max(1, round(time_limit_seconds * 1_000)))
    variables = [solver.BoolVar(f"x_{index}") for index in range(len(candidates))]

    by_driver: dict[str, list[int]] = {}
    by_pickup: dict[str, list[int]] = {}
    for index, candidate in enumerate(candidates):
        by_driver.setdefault(candidate.driver_id, []).append(index)
        by_pickup.setdefault(candidate.route.bakery_id, []).append(index)
    for indices in by_driver.values():
        solver.Add(sum(variables[index] for index in indices) <= 1)
    for indices in by_pickup.values():
        solver.Add(sum(variables[index] for index in indices) <= 1)
    if required_assignments is not None:
        solver.Add(sum(variables) == required_assignments)

    if objective == "cardinality":
        solver.Maximize(sum(variables))
    else:
        # The tiny deterministic term resolves exact score ties without
        # materially changing the declared route-scoring objective.
        solver.Maximize(sum(
            variables[index] * (candidate.route.score - index * 1e-9)
            for index, candidate in enumerate(candidates)
        ))

    status_code = solver.Solve()
    status = _status_name(status_code, pywraplp)
    if status_code not in (pywraplp.Solver.OPTIMAL, pywraplp.Solver.FEASIBLE):
        return None, status
    return [index for index, variable in enumerate(variables) if variable.solution_value() > 0.5], status


def _failed_result(
    candidates: Sequence[AssignmentCandidate],
    driver_ids: tuple[str, ...],
    status: str,
) -> BatchAssignmentResult:
    return BatchAssignmentResult(
        assignments=(),
        unmatched_driver_ids=driver_ids,
        candidate_count=len(candidates),
        assignment_count=0,
        route_score=0.0,
        solver="OR-Tools CBC",
        status=status,
    )


def _status_name(status: int, pywraplp: object) -> str:
    names = {
        pywraplp.Solver.OPTIMAL: "optimal",
        pywraplp.Solver.FEASIBLE: "feasible",
        pywraplp.Solver.INFEASIBLE: "infeasible",
        pywraplp.Solver.UNBOUNDED: "unbounded",
        pywraplp.Solver.ABNORMAL: "abnormal",
        pywraplp.Solver.NOT_SOLVED: "not_solved",
    }
    return names.get(status, f"unknown_{status}")
