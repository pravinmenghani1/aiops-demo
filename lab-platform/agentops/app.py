"""AgentOps Lab: the agent service and its approval dashboard.

    python -m agentops.app        (inside the container; listens on :8000)

Standard library only, so it runs anywhere Python 3.11+ does.
"""
from __future__ import annotations

import json
import mimetypes
import queue
import re
import time
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from agentkit.llm import get_llm

from . import rehearsal  # noqa: F401  (registers the scripted agent used when AGENT_MODE=mock)
from . import settings as settings_mod
from .runs import Audit, EventBus, RunManager
from .toolset import Systems, build_policy, build_toolbox, system_prompt

STATIC = Path(__file__).resolve().parent / "static"


def make_llm():
    """The live LLM from .env, or the rehearsal script when AGENT_MODE=mock."""
    return get_llm(mock_script="agentops")


class StatusBoard:
    """Live status of every system for the dashboard header, cached for a few seconds."""

    def __init__(self, systems: Systems, mgr: RunManager):
        self.sys, self.mgr = systems, mgr
        self._cache: tuple[float, dict] = (0, {})
        self._pool = ThreadPoolExecutor(max_workers=8)
        self._llm = None

    def _jenkins(self):
        b = self.sys.jenkins.build(self.sys.settings.jenkins_job)
        if "error" in b:
            return {"state": "unknown", "label": "no builds yet"}
        state = {"SUCCESS": "ok", "FAILURE": "bad", "IN_PROGRESS": "busy"}.get(b["result"], "warn")
        return {"state": state, "label": f"#{b['build']} {b['result'].lower().replace('_', ' ')}", "detail": b.get("commit")}

    def _github(self):
        if not self.sys.github:
            return {"state": "off", "label": "not connected"}
        runs = self.sys.github.runs(1)["runs"]
        if not runs:
            return {"state": "unknown", "label": "no runs yet"}
        r = runs[0]
        label = r["conclusion"] or r["status"]
        state = {"success": "ok", "failure": "bad"}.get(r["conclusion"] or "", "busy")
        return {"state": state, "label": f"{label} · {r['commit']}", "detail": r["message"]}

    def _disk(self, host):
        d = self.sys.agents.disk(host)
        pct = d["used_pct"]
        return {"state": "bad" if pct >= 90 else "warn" if pct >= 75 else "ok", "label": f"{pct:.0f}% used",
                "value": pct}

    def _service(self, name):
        d = self.sys.services.status(name)
        if d.get("status") != "ok":
            return {"state": "bad", "label": "down"}
        return {"state": "ok", "label": f"{d['active_sessions']:,} sessions", "detail": f"up {d['uptime_seconds']} s"}

    def _alerts(self):
        n = self.sys.alertmanager.active()["firing"]
        return {"state": "bad" if n else "ok", "label": f"{n} firing", "value": n}

    def _llm_desc(self):
        if self._llm is None:
            try:
                desc = make_llm().describe()
                self._llm = ({"state": "warn", "label": "rehearsal (scripted, no LLM)"} if desc.startswith("MOCK")
                             else {"state": "ok", "label": desc})
            except Exception as err:  # noqa: BLE001  shown in the header instead of failing every run silently
                return {"state": "bad", "label": "not configured", "detail": str(err)[:200]}
        return self._llm

    def get(self) -> dict:
        ts, cached = self._cache
        if time.time() - ts < 4 and cached:
            return cached
        checks = {"jenkins": self._jenkins, "github": self._github}
        for host in self.sys.settings.build_agents:
            checks[f"disk:{host}"] = lambda h=host: self._disk(h)
        for name in self.sys.settings.services:
            checks[f"service:{name}"] = lambda n=name: self._service(n)
        checks.update(alerts=self._alerts, llm=self._llm_desc)
        futures = {k: self._pool.submit(fn) for k, fn in checks.items()}
        out = {}
        for k, fut in futures.items():
            try:
                out[k] = fut.result(timeout=6)
            except Exception as err:  # one system being down must not break the dashboard
                out[k] = {"state": "bad", "label": "unreachable", "detail": str(err)[:160]}
        out["guardrails"] = {"state": "ok" if self.mgr.guardrails else "bad", "label": "on" if self.mgr.guardrails else "off"}
        self._cache = (time.time(), out)
        return out


def make_handler(mgr: RunManager, board: StatusBoard, audit: Audit, bus: EventBus, cfg):
    class Handler(BaseHTTPRequestHandler):
        server_version = "agentops/1.0"
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt, *args):
            if "/api/status" not in self.path and "/api/stream" not in self.path:
                print(f"[agentops] {self.address_string()} {fmt % args}", flush=True)

        # ------------------------------------------------------------- helpers
        def _send(self, code: int, body, ctype: str = "application/json") -> None:
            data = body if isinstance(body, bytes) else (body.encode() if isinstance(body, str) else
                                                         json.dumps(body, ensure_ascii=False).encode())
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def _body(self) -> dict:
            length = int(self.headers.get("Content-Length") or 0)
            if not length:
                return {}
            try:
                return json.loads(self.rfile.read(length))
            except ValueError:
                return {}

        # ------------------------------------------------------------- routes
        def do_GET(self):  # noqa: N802
            url = urlparse(self.path)
            path = url.path
            if path in ("/", "/index.html"):
                return self._send(200, (STATIC / "index.html").read_bytes(), "text/html; charset=utf-8")
            if path.startswith("/static/"):
                name = path[len("/static/"):]
                f = STATIC / name
                if not re.fullmatch(r"[a-z0-9_.-]+", name) or not f.is_file():
                    return self._send(404, {"error": "not found"})
                ctype = mimetypes.guess_type(name)[0] or "application/octet-stream"
                return self._send(200, f.read_bytes(), ctype + ("; charset=utf-8" if ctype.startswith("text") or
                                                                   ctype.endswith("javascript") else ""))
            if path == "/healthz":
                return self._send(200, {"ok": True})
            if path == "/api/status":
                return self._send(200, board.get())
            if path == "/api/settings":
                return self._send(200, {"guardrails": mgr.guardrails, "github": cfg.github_enabled,
                                        "github_repo": cfg.github_repo, "job": cfg.jenkins_job,
                                        "services": sorted(cfg.services), "build_agents": sorted(cfg.build_agents)})
            if path == "/api/runs":
                return self._send(200, mgr.list())
            m = re.fullmatch(r"/api/runs/([0-9a-f]{8})", path)
            if m:
                run = mgr.runs.get(m.group(1))
                return self._send(200, run.full()) if run else self._send(404, {"error": "no such run"})
            if path == "/api/alerts":
                return self._send(200, sorted(mgr.alerts.values(), key=lambda a: a["received"], reverse=True))
            if path == "/api/audit":
                n = int(parse_qs(url.query).get("limit", ["50"])[0])
                return self._send(200, audit.tail(min(n, 500)))
            if path == "/api/stream":
                return self._stream()
            self._send(404, {"error": "not found"})

        def do_POST(self):  # noqa: N802
            path = urlparse(self.path).path
            body = self._body()
            if path == "/api/runs":
                q = str(body.get("question", "")).strip()
                if not q:
                    return self._send(400, {"error": "question is required"})
                return self._send(201, mgr.start(q[:2000], source="dashboard").summary())
            m = re.fullmatch(r"/api/runs/([0-9a-f]{8})/approvals/([0-9a-f]{8})", path)
            if m:
                ok = mgr.decide(m.group(1), m.group(2), bool(body.get("approve")), by=str(body.get("by") or "dashboard"))
                return self._send(200 if ok else 409, {"ok": ok})
            if path == "/api/settings":
                if "guardrails" in body:
                    mgr.set_guardrails(bool(body["guardrails"]))
                return self._send(200, {"guardrails": mgr.guardrails})
            if path == "/webhook/alertmanager":
                started = mgr.handle_alertmanager(body)
                return self._send(200, {"ok": True, "runs_started": started})
            self._send(404, {"error": "not found"})

        def _stream(self) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "keep-alive")
            self.end_headers()
            q = bus.subscribe()
            try:
                self.wfile.write(b": connected\n\n")
                self.wfile.flush()
                while True:
                    try:
                        ev = q.get(timeout=15)
                        self.wfile.write(f"data: {json.dumps(ev, ensure_ascii=False)}\n\n".encode())
                    except queue.Empty:
                        self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass
            finally:
                bus.unsubscribe(q)
                self.close_connection = True

    return Handler


def build(cfg=None):
    cfg = cfg or settings_mod.load()
    bus = EventBus()
    audit = Audit(Path(cfg.data_dir) / "audit.jsonl", bus)
    systems = Systems(cfg)
    mgr = RunManager(cfg, bus, audit, llm_factory=make_llm, toolbox_factory=lambda: build_toolbox(systems),
                     policy_factory=build_policy, prompt_factory=lambda g: system_prompt(cfg, g))
    board = StatusBoard(systems, mgr)
    server = ThreadingHTTPServer(("0.0.0.0", cfg.port), make_handler(mgr, board, audit, bus, cfg))
    server.daemon_threads = True
    return server, mgr


def main() -> None:
    server, mgr = build()
    print(f"[agentops] dashboard on http://localhost:{server.server_address[1]} "
          f"(guardrails {'on' if mgr.guardrails else 'off'})", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
