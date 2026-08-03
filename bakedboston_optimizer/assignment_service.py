from __future__ import annotations

from datetime import datetime
from typing import Any

from .batch import AssignmentCandidate, solve_assignment
from .models import RouteCandidate


def assign(payload: dict[str, Any]) -> dict[str, Any]:
    """Select a conflict-free set of feasible routes supplied by the live app.

    Route feasibility and traffic-aware timing are calculated by the
    recommendation service. This boundary performs the global binary
    assignment step, keeping the operational database outside the solver.
    """

    raw_candidates = payload.get("candidates")
    if not isinstance(raw_candidates, list):
        raise ValueError("candidates must be a list")
    candidates = [_candidate(item, index) for index, item in enumerate(raw_candidates)]
    driver_ids = tuple(dict.fromkeys(candidate.driver_id for candidate in candidates))
    result = solve_assignment(candidates, driver_ids=driver_ids)
    return {
        "model": {
            "name": "bakedboston-batch-assignment",
            "version": "1.0",
            "solver": result.solver,
            "objective": "maximum-cardinality_then_maximum-route-score",
        },
        "status": result.status,
        "candidateCount": result.candidate_count,
        "assignmentCount": result.assignment_count,
        "routeScore": result.route_score,
        "assignments": [
            {
                "candidateId": candidate.candidate_id,
                "driverId": candidate.driver_id,
                "requestId": candidate.request_id,
                "pickupId": int(candidate.route.bakery_id),
                "pantryId": int(candidate.route.pantry_id.split(":", 1)[0]),
                "score": candidate.route.score,
            }
            for candidate in result.assignments
        ],
        "unmatchedDriverIds": list(result.unmatched_driver_ids),
    }


def _candidate(value: Any, index: int) -> AssignmentCandidate:
    if not isinstance(value, dict):
        raise ValueError(f"candidate {index} must be an object")
    driver_id = str(value["driverId"])
    request_id = str(value["requestId"])
    pickup_id = str(value["pickupId"])
    pantry_id = str(value["pantryId"])
    candidate_id = str(value.get("candidateId") or f"{request_id}:{pickup_id}:{pantry_id}")
    depart_at = _datetime(value["departAt"])
    pickup_at = _datetime(value["pickupAt"])
    pantry_arrival_at = _datetime(value["pantryArrivalAt"])
    finish_at = _datetime(value["finishAt"])
    if not depart_at <= pickup_at <= pantry_arrival_at <= finish_at:
        raise ValueError(f"candidate {index} has invalid route timing")
    return AssignmentCandidate(
        driver_id=driver_id,
        request_id=request_id,
        candidate_id=candidate_id,
        route=RouteCandidate(
            bakery_id=pickup_id,
            bakery_name=str(value.get("bakeryName") or pickup_id),
            bakery_address="",
            pantry_id=pantry_id,
            pantry_name=str(value.get("pantryName") or pantry_id),
            pantry_address="",
            depart_at=depart_at,
            pickup_at=pickup_at,
            pantry_arrival_at=pantry_arrival_at,
            finish_at=finish_at,
            drive_minutes=float(value.get("driveMinutes") or 0),
            waiting_minutes=float(value.get("waitingMinutes") or 0),
            destination_minutes=0,
            pantry_priority=float(value.get("pantryPriority") or 0),
            score=float(value["score"]),
            explanation=(),
        ),
    )


def _datetime(value: Any) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("candidate times must include a timezone")
    return parsed
