#!/usr/bin/env python3
"""A small API service (prod-api or staging-api) with live user sessions and a real restart.

    GET  /health          -> status, uptime, active sessions
    GET  /metrics         -> Prometheus metrics
    POST /admin/restart   -> needs X-Admin-Token; the process exits and Docker restarts it,
                             so every active session really is dropped.
"""
from __future__ import annotations

import json
import math
import os
import random
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

NAME = os.environ.get("SERVICE_NAME", "prod-api")
ENVIRONMENT = os.environ.get("ENVIRONMENT", "production")
BASE = int(os.environ.get("BASE_SESSIONS", "1240"))
TOKEN = os.environ.get("ADMIN_TOKEN", "")
PORT = int(os.environ.get("PORT", "8080"))
STARTED = time.time()
RAMP_SECONDS = float(os.environ.get("RAMP_SECONDS", "90"))


def sessions() -> int:
    """Users reconnect over about 90 seconds after a restart, then hover around BASE."""
    up = time.time() - STARTED
    level = BASE * (1 - math.exp(-3 * up / RAMP_SECONDS))
    jitter = random.uniform(-0.02, 0.02) * BASE
    return max(0, int(level + jitter))


class Handler(BaseHTTPRequestHandler):
    server_version = f"{NAME}/1.0"

    def log_message(self, fmt, *args):
        if "/metrics" not in self.path:
            print(f"[{NAME}] {self.address_string()} {fmt % args}", flush=True)

    def _send(self, code: int, body, ctype: str = "application/json") -> None:
        data = body.encode() if isinstance(body, str) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):  # noqa: N802
        if self.path == "/health":
            return self._send(200, {"service": NAME, "environment": ENVIRONMENT, "status": "ok",
                                    "uptime_seconds": int(time.time() - STARTED), "active_sessions": sessions()})
        if self.path == "/metrics":
            labels = f'service="{NAME}",environment="{ENVIRONMENT}"'
            return self._send(200, "\n".join([
                "# HELP agentops_active_sessions Users currently connected.",
                "# TYPE agentops_active_sessions gauge",
                f"agentops_active_sessions{{{labels}}} {sessions()}",
                "# HELP agentops_uptime_seconds Seconds since the process started.",
                "# TYPE agentops_uptime_seconds gauge",
                f"agentops_uptime_seconds{{{labels}}} {int(time.time() - STARTED)}",
            ]) + "\n", "text/plain; version=0.0.4")
        self._send(404, {"error": "not found"})

    def do_POST(self):  # noqa: N802
        if self.path != "/admin/restart":
            return self._send(404, {"error": "not found"})
        if not TOKEN or self.headers.get("X-Admin-Token") != TOKEN:
            return self._send(401, {"error": "missing or wrong X-Admin-Token"})
        dropped = sessions()
        print(f"[{NAME}] RESTART requested: dropping {dropped} active sessions", flush=True)
        self._send(202, {"service": NAME, "environment": ENVIRONMENT, "status": "restarting",
                         "active_sessions_dropped": dropped})
        threading.Timer(0.2, lambda: os._exit(0)).start()


if __name__ == "__main__":
    print(f"[{NAME}] {ENVIRONMENT} service on :{PORT}", flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
