from __future__ import annotations

import unittest
from http.server import BaseHTTPRequestHandler
from unittest.mock import patch

from api.recommendations import _dispatch, handler


class ApiEntrypointTests(unittest.TestCase):
    def test_vercel_entrypoint_is_http_handler(self) -> None:
        self.assertTrue(issubclass(handler, BaseHTTPRequestHandler))

    @patch("api.recommendations.recommend_network")
    def test_network_mode_dispatches_to_global_assignment_solver(self, mocked) -> None:
        mocked.return_value = {"assignments": []}

        result = _dispatch({"mode": "network_assignments", "serviceDate": "2026-08-20"})

        self.assertEqual(result, {"assignments": []})
        mocked.assert_called_once_with({
            "mode": "network_assignments",
            "serviceDate": "2026-08-20",
        })

    @patch("api.recommendations.recommend")
    def test_default_mode_dispatches_to_route_ranking_solver(self, mocked) -> None:
        mocked.return_value = {"routes": []}

        result = _dispatch({"earliestStart": "2026-08-20T17:00:00-04:00"})

        self.assertEqual(result, {"routes": []})
        mocked.assert_called_once_with({
            "earliestStart": "2026-08-20T17:00:00-04:00",
        })

    @patch("api.recommendations.simulate_network")
    def test_simulation_mode_dispatches_to_schedule_simulator(self, mocked) -> None:
        mocked.return_value = {"mode": "academic_schedule_simulation"}
        payload = {
            "mode": "schedule_simulation",
            "startDate": "2026-08-24",
            "days": 7,
            "randomSeed": 42,
        }

        result = _dispatch(payload)

        self.assertEqual(result, {"mode": "academic_schedule_simulation"})
        mocked.assert_called_once_with(payload)

    @patch("api.recommendations.simulate_custom_experiment")
    def test_custom_mode_dispatches_to_comparison_experiment(self, mocked) -> None:
        mocked.return_value = {"displayMode": "live_custom_gurobi_experiment"}
        payload = {
            "mode": "custom_experiment",
            "days": 5,
            "driversPerDay": 6,
            "bakeryCount": 9,
            "pantryCount": 9,
            "randomSeed": 2042,
        }

        result = _dispatch(payload)

        self.assertEqual(result, {"displayMode": "live_custom_gurobi_experiment"})
        mocked.assert_called_once_with(payload)


if __name__ == "__main__":
    unittest.main()
