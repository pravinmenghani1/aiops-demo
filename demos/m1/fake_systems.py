"""Pretend CI server, build agent and service manager for the Module 1 demos.

Everything is local and in memory, loaded from data/. Nothing real is touched,
so the demos are safe to run anywhere, including the 'agent goes wrong' demo.
"""
from __future__ import annotations

import copy
import json
from datetime import datetime
from pathlib import Path

DATA = Path(__file__).resolve().parent / "data"


def _load(name: str):
    return json.loads((DATA / name).read_text(encoding="utf-8"))


def _ago(iso: str, now: str) -> str:
    mins = int((datetime.fromisoformat(now) - datetime.fromisoformat(iso)).total_seconds() // 60)
    return f"{mins} minutes ago" if mins < 120 else f"{mins // 60} hours ago"


class FakeCI:
    def __init__(self, log_variant: str = ""):
        data = _load("builds.json")
        self.now = data["now"]
        self.builds = {b["id"]: b for b in data["builds"]}
        self.commits = _load("commits.json")
        self.pipelines = _load("pipelines.json")
        self.log_variant = log_variant

    def get_build_status(self, build_id: int | None = None) -> dict:
        if build_id is None:
            nightly = [b for b in self.builds.values() if b["trigger"] == "nightly"]
            b = max(nightly, key=lambda x: x["started"])
        elif int(build_id) in self.builds:
            b = self.builds[int(build_id)]
        else:
            return {"error": f"No build with id {build_id}"}
        out = {k: b[k] for k in ("id", "pipeline", "trigger", "branch", "status", "started", "duration_s", "agent", "commit")}
        if b.get("failed_step"):
            out["failed_step"] = b["failed_step"]
        out["started_relative"] = _ago(b["started"], self.now)
        return out

    def get_build_log(self, build_id: int, tail: int = 40) -> dict:
        path = DATA / "logs" / f"{int(build_id)}{self.log_variant}.log"
        if not path.exists():
            return {"error": f"No log stored for build {build_id}"}
        lines = path.read_text(encoding="utf-8").splitlines()
        tail = max(5, min(int(tail), 80))
        return {"build_id": int(build_id), "total_lines": len(lines), "showing": f"last {min(tail, len(lines))} lines",
                "log": "\n".join(lines[-tail:])}

    def get_commit(self, sha: str) -> dict:
        return self.commits.get(sha[:7], {"error": f"Unknown commit {sha}"})

    def get_pipeline_status(self, pipeline: str) -> dict:
        p = self.pipelines.get(pipeline)
        if not p:
            return {"error": f"Unknown pipeline {pipeline!r}. Known: {sorted(self.pipelines)}"}
        return {**p, "finished_relative": _ago(p["finished"], self.now)}


class FakeHost:
    def __init__(self):
        self.hosts = copy.deepcopy(_load("hosts.json"))

    def _host(self, host: str) -> dict:
        if host not in self.hosts:
            raise KeyError(f"Unknown host {host!r}. Known: {sorted(self.hosts)}")
        return self.hosts[host]

    def get_disk_usage(self, host: str) -> dict:
        h = self._host(host)
        return {"host": host, "mount": h["mount"], "size_gb": h["size_gb"], "used_gb": round(h["used_gb"], 1),
                "free_gb": round(h["size_gb"] - h["used_gb"], 1), "used_pct": round(100 * h["used_gb"] / h["size_gb"]),
                "alert_threshold_pct": h["alert_threshold_pct"]}

    def list_old_artifacts(self, host: str, older_than_days: int = 14) -> dict:
        old = [a for a in self._host(host)["artifacts"] if a["age_days"] > int(older_than_days)]
        return {"host": host, "older_than_days": int(older_than_days), "count": len(old),
                "total_gb": round(sum(a["size_gb"] for a in old), 1), "artifacts": old}

    def cleanup_artifacts(self, host: str, older_than_days: int = 14) -> dict:
        if int(older_than_days) < 7:
            return {"error": "Refused: artifacts newer than 7 days may still be needed for rollbacks."}
        h = self._host(host)
        old = [a for a in h["artifacts"] if a["age_days"] > int(older_than_days)]
        freed = round(sum(a["size_gb"] for a in old), 1)
        h["artifacts"] = [a for a in h["artifacts"] if a not in old]
        h["used_gb"] -= freed
        return {"host": host, "deleted": len(old), "freed_gb": freed, "simulated": True}


class FakeServices:
    SERVICES = {"prod-api": ("production", 1240), "staging-api": ("staging", 12),
                "build-agent-07": ("ci", 0), "build-agent-04": ("ci", 0)}

    def __init__(self):
        self.restarted: list[str] = []

    def restart_service(self, name: str) -> dict:
        if name not in self.SERVICES:
            return {"error": f"Unknown service {name!r}"}
        env, sessions = self.SERVICES[name]
        self.restarted.append(name)
        out = {"service": name, "environment": env, "status": "RESTARTED", "simulated": True}
        if sessions:
            out["active_sessions_dropped"] = sessions
        return out
