from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from .batch import AssignmentCandidate, solve_assignment
from .models import RouteCandidate


def candidate(driver: str, pickup: str, pantry: str, score: float) -> AssignmentCandidate:
    time = datetime(2026, 7, 27, 17, tzinfo=ZoneInfo("America/New_York"))
    return AssignmentCandidate(
        driver_id=driver,
        route=RouteCandidate(
            bakery_id=pickup,
            bakery_name=f"Bakery {pickup[-1]}",
            bakery_address="Boston, MA",
            pantry_id=pantry,
            pantry_name=f"Pantry {pantry[-1]}",
            pantry_address="Boston, MA",
            depart_at=time,
            pickup_at=time,
            pantry_arrival_at=time,
            finish_at=time,
            drive_minutes=10,
            waiting_minutes=0,
            destination_minutes=0,
            pantry_priority=0.5,
            score=score,
            explanation=("Demonstration candidate",),
        ),
    )


def main() -> None:
    # Greedily choosing the 10-point route would strand driver d2. The MIP
    # selects 9 + 8 instead, completing both pickups with total quality 17.
    result = solve_assignment([
        candidate("d1", "b1", "p1", 10),
        candidate("d1", "b2", "p1", 9),
        candidate("d2", "b1", "p2", 8),
    ], driver_ids=("d1", "d2"))
    print(f"status={result.status}")
    print(f"assignments={result.assignment_count}/{len(('d1', 'd2'))}")
    print(f"route_score={result.route_score:.2f}")
    for assignment in result.assignments:
        print(
            f"{assignment.driver_id}: {assignment.route.bakery_name} -> "
            f"{assignment.route.pantry_name} (score={assignment.route.score:.2f})"
        )


if __name__ == "__main__":
    main()
