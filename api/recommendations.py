from __future__ import annotations

import hmac
import json
import os
from http.server import BaseHTTPRequestHandler
from typing import Any

from bakedboston_optimizer.optimizer import GurobiUnavailableError
from bakedboston_optimizer.service import (
    recommend,
    recommend_network,
    simulate_custom_experiment,
    simulate_network,
)

MAX_REQUEST_BYTES = 1_000_000


def _dispatch(payload: dict[str, Any]) -> dict[str, Any]:
    """Route the API request to the requested Gurobi optimization mode."""

    if payload.get("mode") == "network_assignments":
        return recommend_network(payload)
    if payload.get("mode") == "schedule_simulation":
        return simulate_network(payload)
    if payload.get("mode") == "custom_experiment":
        return simulate_custom_experiment(payload)
    return recommend(payload)


class handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        wls_configured = all(os.getenv(name, "").strip() for name in (
            "GUROBI_WLSACCESSID",
            "GUROBI_WLSSECRET",
            "GUROBI_LICENSEID",
        ))
        self._json(200, {
            "service": "bakedboston-optimizer",
            "backend": "gurobi",
            "status": "handler_ready",
            "productionLicenseConfigured": wls_configured,
        })

    def do_POST(self) -> None:
        expected = os.getenv("OPTIMIZER_API_KEY", "")
        authorization = self.headers.get("Authorization", "")
        supplied = authorization[7:] if authorization.startswith("Bearer ") else ""
        if not expected or not hmac.compare_digest(expected, supplied):
            self._json(401, {"error": "Unauthorized."})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0:
                raise ValueError("A JSON request body is required.")
            if length > MAX_REQUEST_BYTES:
                self._json(413, {"error": "The optimizer request is too large."})
                return
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            if not isinstance(payload, dict):
                raise TypeError("The optimizer request must be a JSON object.")
            result = _dispatch(payload)
            self._json(200, result)
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as error:
            self._json(400, {"error": str(error)})
        except GurobiUnavailableError as error:
            self._json(503, {
                "error": "The Gurobi optimization service is unavailable.",
                "diagnostics": {"backend": "gurobi", "detail": str(error)},
            })
        except Exception:
            self._json(500, {"error": "The optimizer could not generate recommendations."})

    def _json(self, status: int, payload: dict[str, object]) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "private, no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
