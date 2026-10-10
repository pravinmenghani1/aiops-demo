#!/usr/bin/env python3
"""A narrow ops API for one build agent: disk usage, old artifacts, cleanup and Prometheus metrics.

The agent never gets a shell on this machine. It can only call these four endpoints,
and cleanup refuses anything newer than 7 days. Standard library only.

    GET  /disk                         -> filesystem usage of the artifact disk
    GET  /artifacts?older_than_days=14 -> artifact folders older than N days
    POST /cleanup {"older_than_days":14}
    GET  /metrics                      -> Prometheus metrics (no token needed)
"""
from __future__ import annotations

import json
import os
import shutil
import socket
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(os.environ.get("OPS_ARTIFACTS_DIR", "/srv/artifacts"))
TOKEN = os.environ.get("OPS_TOKEN", "")
HOST = os.environ.get("OPS_HOSTNAME") or socket.gethostname()
PORT = int(os.environ.get("OPS_PORT", "8700"))
CAPACITY = int(os.environ.get("OPS_CAPACITY_BYTES", "0"))  # test hook: pretend the disk is this big
MIN_AGE_DAYS = 7
MB = 1024 * 1024


def dir_size(path: Path) -> int:
    total = 0
    for dirpath, _dirs, files in os.walk(path):
        for f in files:
            try:
                total += os.lstat(os.path.join(dirpath, f)).st_size
            except OSError:
                pass
    return total


def disk() -> dict:
    if CAPACITY:
        total, used = CAPACITY, dir_size(ROOT)
    else:
        st = os.statvfs(ROOT)
        total = st.f_blocks * st.f_frsize
        used = total - st.f_bfree * st.f_frsize
    free = max(0, total - used)
    return {"host": HOST, "mount": str(ROOT), "size_mb": round(total / MB), "used_mb": round(used / MB),
            "free_mb": round(free / MB), "used_pct": round(100 * used / total, 1) if total else 0.0,
            "alert_threshold_pct": 90, "_used_bytes": used, "_total_bytes": total}


def artifact_dirs() -> list[dict]:
    now = time.time()
    out = []
    if not ROOT.exists():
        return out
    for job in sorted(p for p in ROOT.iterdir() if p.is_dir()):
        for build in sorted(p for p in job.iterdir() if p.is_dir()):
            age = (now - build.stat().st_mtime) / 86400
            out.append({"path": str(build), "job": job.name, "build": build.name,
                        "size_mb": round(dir_size(build) / MB, 1), "age_days": int(age)})
    return sorted(out, key=lambda a: -a["age_days"])


def old_artifacts(older_than_days: int) -> dict:
    old = [a for a in artifact_dirs() if a["age_days"] > older_than_days]
    return {"host": HOST, "older_than_days": older_than_days, "count": len(old),
            "total_mb": round(sum(a["size_mb"] for a in old), 1), "artifacts": old}


def cleanup(older_than_days: int) -> dict:
    if older_than_days < MIN_AGE_DAYS:
        return {"error": f"Refused: artifacts newer than {MIN_AGE_DAYS} days may still be needed for rollbacks."}
    before = disk()
    old = [a for a in artifact_dirs() if a["age_days"] > older_than_days]
    for a in old:
        shutil.rmtree(a["path"], ignore_errors=True)
    after = disk()
    return {"host": HOST, "deleted": len(old), "freed_mb": round((before["_used_bytes"] - after["_used_bytes"]) / MB, 1),
            "used_pct_before": before["used_pct"], "used_pct_after": after["used_pct"]}


def public(d: dict) -> dict:
    return {k: v for k, v in d.items() if not k.startswith("_")}


def metrics() -> str:
    d = disk()
    labels = f'host="{HOST}",mount="{ROOT}"'
    count = len(artifact_dirs())
    lines = [
        "# HELP agentops_disk_used_ratio Fraction of the build agent's artifact disk in use.",
        "# TYPE agentops_disk_used_ratio gauge",
        f"agentops_disk_used_ratio{{{labels}}} {d['_used_bytes'] / d['_total_bytes'] if d['_total_bytes'] else 0:.4f}",
        "# HELP agentops_disk_size_bytes Size of the artifact disk.",
        "# TYPE agentops_disk_size_bytes gauge",
        f"agentops_disk_size_bytes{{{labels}}} {d['_total_bytes']}",
        "# HELP agentops_disk_free_bytes Free space on the artifact disk.",
        "# TYPE agentops_disk_free_bytes gauge",
        f"agentops_disk_free_bytes{{{labels}}} {d['_total_bytes'] - d['_used_bytes']}",
        "# HELP agentops_artifact_folders Number of build artifact folders kept.",
        "# TYPE agentops_artifact_folders gauge",
        f"agentops_artifact_folders{{{labels}}} {count}",
    ]
    return "\n".join(lines) + "\n"


class Handler(BaseHTTPRequestHandler):
    server_version = "agentops-ops-api/1.0"

    def log_message(self, fmt, *args):
        print(f"[ops-api] {self.address_string()} {fmt % args}", flush=True)

    def _send(self, code: int, body, ctype: str = "application/json") -> None:
        data = body.encode() if isinstance(body, str) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _authorised(self) -> bool:
        if TOKEN and self.headers.get("X-Ops-Token") != TOKEN:
            self._send(401, {"error": "missing or wrong X-Ops-Token"})
            return False
        return True

    def do_GET(self):  # noqa: N802
        url = urlparse(self.path)
        if url.path == "/metrics":
            return self._send(200, metrics(), "text/plain; version=0.0.4")
        if url.path == "/healthz":
            return self._send(200, {"ok": True, "host": HOST})
        if not self._authorised():
            return
        if url.path == "/disk":
            return self._send(200, public(disk()))
        if url.path == "/artifacts":
            days = int(parse_qs(url.query).get("older_than_days", ["14"])[0])
            return self._send(200, old_artifacts(days))
        self._send(404, {"error": "not found"})

    def do_POST(self):  # noqa: N802
        if not self._authorised():
            return
        if urlparse(self.path).path != "/cleanup":
            return self._send(404, {"error": "not found"})
        length = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
            days = int(body.get("older_than_days", 14))
        except (ValueError, TypeError):
            return self._send(400, {"error": "body must be JSON with older_than_days"})
        result = cleanup(days)
        print(f"[ops-api] cleanup older_than_days={days} -> {result}", flush=True)
        self._send(200 if "error" not in result else 400, result)


if __name__ == "__main__":
    ROOT.mkdir(parents=True, exist_ok=True)
    print(f"[ops-api] {HOST} serving {ROOT} on :{PORT}", flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
