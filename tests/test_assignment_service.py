import unittest

from bakedboston_optimizer.assignment_service import assign


def candidate(candidate_id: str, driver_id: int, request_id: int, pickup_id: int, pantry_id: int, score: float) -> dict[str, object]:
    return {
        "candidateId": candidate_id,
        "driverId": driver_id,
        "requestId": request_id,
        "pickupId": pickup_id,
        "pantryId": pantry_id,
        "departAt": "2026-07-29T17:00:00-04:00",
        "pickupAt": "2026-07-29T17:10:00-04:00",
        "pantryArrivalAt": "2026-07-29T17:25:00-04:00",
        "finishAt": "2026-07-29T17:30:00-04:00",
        "score": score,
    }


class AssignmentServiceTests(unittest.TestCase):
    def test_assign_returns_global_non_conflicting_selection(self) -> None:
        result = assign({"candidates": [
            candidate("d1-p1", 1, 101, 1, 10, 10),
            candidate("d1-p2", 1, 101, 2, 10, 9),
            candidate("d2-p1", 2, 102, 1, 11, 8),
            candidate("d2-p2", 2, 102, 2, 11, 1),
        ]})

        self.assertEqual(result["status"], "optimal")
        self.assertEqual(result["assignmentCount"], 2)
        self.assertEqual(
            {item["candidateId"] for item in result["assignments"]},
            {"d1-p2", "d2-p1"},
        )
        self.assertEqual(result["routeScore"], 17)


if __name__ == "__main__":
    unittest.main()
