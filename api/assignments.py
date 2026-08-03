from __future__ import annotations

import hmac
import json
import os
from http.server import BaseHTTPRequestHandler

from bakedboston_optimizer.assignment_service import assign


class handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        self._json(200, {
            "service": "BakedBoston Batch Assignment",
            "status": "ok",
            "model": "two-stage-mip-v1",
        })

    def do_POST(self) -> None:
        expected = os.getenv("OPTIMIZER_API_KEY", "")
        supplied = self.headers.get("Authorization", "").removeprefix("Bearer ")
        if not expected or not hmac.compare_digest(expected, supplied):
            self._json(401, {"error": "Unauthorized."})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length))
            self._json(200, assign(payload))
        except (KeyError, TypeError, ValueError) as error:
            self._json(400, {"error": str(error)})
        except Exception:
            self._json(500, {"error": "The optimizer could not allocate routes."})

    def _json(self, status: int, payload: dict[str, object]) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "private, no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
