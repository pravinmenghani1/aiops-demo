"""Clients for the real systems the agent works with. Each returns small, model-friendly dicts."""
from __future__ import annotations

import json
import re
import subprocess
import time
from datetime import datetime, timezone
from urllib.parse import quote, urlencode

from .net import HttpError, get_json, opener, request


def _ago(epoch_s: float) -> str:
    mins = int((time.time() - epoch_s) // 60)
    if mins < 2:
        return "just now"
    return f"{mins} minutes ago" if mins < 120 else f"{mins // 60} hours ago"


def _tail(text: str, n: int) -> dict:
    lines = text.splitlines()
    n = max(5, min(int(n), 120))
    return {"total_lines": len(lines), "showing": f"last {min(n, len(lines))} lines", "log": "\n".join(lines[-n:])}


# ----------------------------------------------------------------------------- Jenkins
class Jenkins:
    def __init__(self, url: str, user: str, password: str):
        self.url = url.rstrip("/")
        self.auth = (user, password)
        self.session = opener(cookies=True)
        self._crumb: dict | None = None

    def _get(self, path: str, raw: bool = False):
        status, _h, body = request("GET", self.url + path, auth=self.auth, via=self.session)
        return body.decode("utf-8", errors="replace") if raw else json.loads(body)

    def _crumb_header(self, refresh: bool = False) -> dict:
        if refresh or self._crumb is None:
            try:
                d = self._get("/crumbIssuer/api/json")
                self._crumb = {d["crumbRequestField"]: d["crumb"]}
            except HttpError as e:
                if e.status == 404:  # CSRF protection disabled
                    self._crumb = {}
                else:
                    raise
        return self._crumb

    def build(self, job: str, build: str = "lastBuild") -> dict:
        tree = "number,result,building,timestamp,duration,description,url"
        try:
            d = self._get(f"/job/{quote(job)}/{quote(str(build))}/api/json?tree={tree}")
        except HttpError as e:
            if e.status == 404:
                return {"error": f"No build {build!r} found for job {job!r} (has it run yet?)"}
            raise
        result = d.get("result") or ("IN_PROGRESS" if d.get("building") else "UNKNOWN")
        return {"job": job, "build": d["number"], "result": result, "commit": d.get("description"),
                "started_relative": _ago(d["timestamp"] / 1000), "duration_s": round((d.get("duration") or 0) / 1000),
                "url": d.get("url")}

    def log(self, job: str, build: str = "lastBuild", tail: int = 60) -> dict:
        try:
            text = self._get(f"/job/{quote(job)}/{quote(str(build))}/consoleText", raw=True)
        except HttpError as e:
            if e.status == 404:
                return {"error": f"No console log for {job} {build}"}
            raise
        return {"job": job, "build": build, **_tail(text, tail)}

    def trigger(self, job: str) -> dict:
        for attempt in range(2):
            try:
                _s, headers, _b = request("POST", f"{self.url}/job/{quote(job)}/build", auth=self.auth,
                                          via=self.session, headers=self._crumb_header(refresh=attempt > 0))
                return {"job": job, "queued": True, "queue_item": headers.get("Location")}
            except HttpError as e:
                if e.status == 403 and attempt == 0:
                    continue
                raise
        return {"error": "could not trigger"}


# ----------------------------------------------------------------------------- GitHub Actions
_TS = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(\.\d+)?Z ")


class GitHub:
    API = "https://api.github.com"

    def __init__(self, repo: str, token: str, api: str | None = None):
        self.repo = repo.strip("/")
        self.token = token
        self.api = (api or self.API).rstrip("/")
        self.raw = opener(follow=False)

    @property
    def _h(self) -> dict:
        return {"Authorization": f"Bearer {self.token}", "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28"}

    def _get(self, path: str):
        return get_json(f"{self.api}/repos/{self.repo}{path}", headers=self._h)

    def runs(self, limit: int = 5) -> dict:
        d = self._get(f"/actions/runs?{urlencode({'per_page': max(1, min(int(limit), 20))})}")
        runs = [{"run_id": r["id"], "workflow": r.get("name"), "commit": (r.get("head_sha") or "")[:7],
                 "message": ((r.get("head_commit") or {}).get("message") or "").splitlines()[0][:100],
                 "status": r.get("status"), "conclusion": r.get("conclusion"),
                 "started": r.get("run_started_at") or r.get("created_at"), "url": r.get("html_url")}
                for r in d.get("workflow_runs", [])]
        return {"repo": self.repo, "runs": runs}

    def run_log(self, run_id: int, tail: int = 60) -> dict:
        jobs = self._get(f"/actions/runs/{int(run_id)}/jobs").get("jobs", [])
        if not jobs:
            return {"error": f"Run {run_id} has no jobs yet"}
        job = next((j for j in jobs if j.get("conclusion") == "failure"), jobs[0])
        failed_steps = [s["name"] for s in job.get("steps", []) if s.get("conclusion") == "failure"]
        status, headers, body = request("GET", f"{self.api}/repos/{self.repo}/actions/jobs/{job['id']}/logs",
                                        headers=self._h, via=self.raw, timeout=20)
        if status in (301, 302, 303, 307, 308):  # the log lives at a signed URL: fetch it without our token
            _s, _h2, body = request("GET", headers["Location"], headers={"Accept": "*/*"}, timeout=20)
        text = "\n".join(_TS.sub("", ln) for ln in body.decode("utf-8", errors="replace").splitlines())
        return {"run_id": int(run_id), "job": job.get("name"), "conclusion": job.get("conclusion"),
                "failed_steps": failed_steps, **_tail(text, tail)}

    def rerun_failed(self, run_id: int) -> dict:
        request("POST", f"{self.api}/repos/{self.repo}/actions/runs/{int(run_id)}/rerun-failed-jobs", headers=self._h)
        return {"run_id": int(run_id), "rerun": "requested"}

    def commit(self, sha: str) -> dict:
        d = self._get(f"/commits/{quote(sha)}")
        c = d.get("commit", {})
        return {"sha": d.get("sha", "")[:7], "author": c.get("author", {}).get("name"),
                "date": c.get("author", {}).get("date"), "message": c.get("message"),
                "files_changed": [f["filename"] for f in d.get("files", [])][:20], "source": "GitHub"}


# ----------------------------------------------------------------------------- local git mirror
class LocalGit:
    def __init__(self, git_dir: str):
        self.git_dir = git_dir

    def commit(self, sha: str) -> dict:
        if not re.fullmatch(r"[0-9a-fA-F]{4,40}|HEAD|main", sha or ""):
            return {"error": "sha must be a commit hash"}
        base = ["git", "--git-dir", self.git_dir]
        try:
            meta = subprocess.run(base + ["log", "-1", "--format=%h%x1f%an%x1f%aI%x1f%B", sha],
                                  capture_output=True, text=True, timeout=10, check=True).stdout
            files = subprocess.run(base + ["show", "--name-only", "--format=", sha],
                                   capture_output=True, text=True, timeout=10, check=True).stdout
        except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired) as e:
            return {"error": f"commit {sha} not found in the local mirror ({type(e).__name__})"}
        short, author, date, message = (meta.split("\x1f") + ["", "", "", ""])[:4]
        return {"sha": short, "author": author, "date": date, "message": message.strip(),
                "files_changed": [f for f in files.splitlines() if f.strip()][:20], "source": "local git mirror"}

    def recent(self, limit: int = 5) -> dict:
        n = max(1, min(int(limit), 20))
        try:
            out = subprocess.run(["git", "--git-dir", self.git_dir, "log", f"-{n}", "--name-only",
                                  "--format=%x1e%h%x1f%an%x1f%aI%x1f%s"],
                                 capture_output=True, text=True, timeout=10, check=True).stdout
        except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired) as e:
            return {"error": f"cannot read the local mirror ({type(e).__name__})"}
        commits = []
        for rec in out.split("\x1e"):
            if not rec.strip():
                continue
            head, _, files = rec.partition("\n")
            sha, author, date, subject = (head.split("\x1f") + ["", "", "", ""])[:4]
            commits.append({"sha": sha, "author": author, "date": date, "message": subject,
                            "files_changed": [f for f in files.splitlines() if f.strip()][:20]})
        return {"branch": "main", "commits": commits, "source": "local git mirror"}


# ----------------------------------------------------------------------------- build agent ops API
class BuildAgent:
    def __init__(self, hosts: dict[str, str], token: str):
        self.hosts = hosts
        self.token = token

    def _url(self, host: str) -> str:
        if host not in self.hosts:
            raise KeyError(f"Unknown host {host!r}. Known build agents: {sorted(self.hosts)}")
        return self.hosts[host].rstrip("/")

    def disk(self, host: str) -> dict:
        return get_json(f"{self._url(host)}/disk", headers={"X-Ops-Token": self.token})

    def artifacts(self, host: str, older_than_days: int) -> dict:
        return get_json(f"{self._url(host)}/artifacts?older_than_days={int(older_than_days)}",
                        headers={"X-Ops-Token": self.token})

    def cleanup(self, host: str, older_than_days: int) -> dict:
        try:
            _s, _h, body = request("POST", f"{self._url(host)}/cleanup", json_body={"older_than_days": int(older_than_days)},
                                   headers={"X-Ops-Token": self.token}, timeout=60)
            return json.loads(body)
        except HttpError as e:
            try:
                return json.loads(e.body)
            except ValueError:
                raise e from None


# ----------------------------------------------------------------------------- services
class Services:
    def __init__(self, urls: dict[str, str], token: str):
        self.urls = urls
        self.token = token

    def _url(self, name: str) -> str:
        if name not in self.urls:
            raise KeyError(f"Unknown service {name!r}. Known services: {sorted(self.urls)}")
        return self.urls[name].rstrip("/")

    def status(self, name: str) -> dict:
        try:
            return get_json(f"{self._url(name)}/health", timeout=4)
        except HttpError as e:
            return {"service": name, "status": "down", "detail": str(e)}

    def restart(self, name: str) -> dict:
        """Ask the service to restart, then wait (up to 20 s) until it answers again."""
        url = self._url(name)
        _s, _h, body = request("POST", f"{url}/admin/restart", headers={"X-Admin-Token": self.token})
        out = json.loads(body)
        started, went_down = time.time(), False
        while time.time() - started < 20:
            time.sleep(0.5)
            try:
                h = get_json(f"{url}/health", timeout=2)
            except HttpError:
                went_down = True
                continue
            if went_down or h.get("uptime_seconds", 99) < 3:
                out.update(status="restarted", back_up_after_s=round(time.time() - started, 1),
                           active_sessions_now=h.get("active_sessions"))
                return out
        out["status"] = "restart requested, but the service had not come back after 20 s"
        return out


# ----------------------------------------------------------------------------- Prometheus / Alertmanager
class Prometheus:
    def __init__(self, url: str):
        self.url = url.rstrip("/")

    def query(self, promql: str) -> dict:
        d = get_json(f"{self.url}/api/v1/query?{urlencode({'query': promql})}")
        if d.get("status") != "success":
            return {"error": d.get("error", "query failed")}
        rows = []
        for r in d["data"].get("result", [])[:25]:
            labels = {k: v for k, v in r.get("metric", {}).items() if k != "__name__"}
            value = r.get("value", [None, None])[1]
            rows.append({"labels": labels, "value": value})
        return {"query": promql, "results": rows}


class Alertmanager:
    def __init__(self, url: str):
        self.url = url.rstrip("/")

    def active(self) -> dict:
        alerts = get_json(f"{self.url}/api/v2/alerts?active=true&silenced=false&inhibited=false")
        out = [{"alert": a["labels"].get("alertname"), "severity": a["labels"].get("severity"),
                "summary": a.get("annotations", {}).get("summary"), "since": a.get("startsAt"),
                "labels": {k: v for k, v in a["labels"].items() if k not in ("alertname", "severity")}}
               for a in alerts]
        return {"firing": len(out), "alerts": out}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
