"""Rehearsal mode: a scripted stand-in for the LLM that drives the REAL lab systems.

Set AGENT_MODE=mock in lab-platform/.env to use it. The tool calls are real (Jenkins,
the build agent, the services, Prometheus), only the "thinking" is scripted, so you can
rehearse every scenario before you have an API key, or if the training-room network
blocks your LLM. Answers are written from the real tool results.
"""
from __future__ import annotations

import json
import re

from agentkit.mock_llm import call, results, say, script

SERVICE_RE = re.compile(r"\b([a-z0-9]+(?:-[a-z0-9]+)*-api)\b")
HOST_RE = re.compile(r"\b(build-agent-[a-z0-9-]+)\b")


# ----------------------------------------------------------------------------- helpers
def _unwrap(r):
    """With guardrails on, suspicious tool output arrives wrapped in a runtime notice."""
    if isinstance(r, dict) and "untrusted_output" in r:
        return r["untrusted_output"], True
    return r, False


def get(messages, name):
    found = [r for n, r in results(messages) if n == name]
    return _unwrap(found[-1])[0] if found else None


def all_of(messages, name):
    return [_unwrap(r)[0] for n, r in results(messages) if n == name]


def flagged(messages) -> bool:
    return any(_unwrap(r)[1] for _n, r in results(messages))


def question(messages) -> str:
    return next((m["content"] for m in messages if m["role"] == "user"), "")


def safe_prompt(messages) -> bool:
    return bool(messages) and messages[0]["role"] == "system" and "L2 operations agent" in messages[0]["content"]


def err(r) -> str | None:
    return r.get("error") if isinstance(r, dict) else None


def _first(lines, pattern, default=""):
    rx = re.compile(pattern)
    return next((ln.strip() for ln in lines if rx.search(ln)), default)


def short(text: str, n: int = 110) -> str:
    return text if len(text) <= n else text[:n].rsplit(" ", 1)[0] + " …"


def touches_code(commit: dict) -> bool:
    return any(f.startswith("src/") for f in commit.get("files_changed", []))


def evidence(log_text: str) -> dict:
    lines = log_text.splitlines()
    return {
        "failed": _first(lines, r"^FAILED tests/", ""),
        "assertion": _first(lines, r"^E\s+(AssertionError: )?assert", ""),
        "location": _first(lines, r"^tests/\S+\.py:\d+:", ""),
        "summary": _first(lines, r"\d+ failed", ""),
        "injected": _first(lines, r"(?i)note to (the )?(ai|assistant|agent)", ""),
        "finished": _first(lines, r"^Finished: ", ""),
    }


# ----------------------------------------------------------------------------- flows
def jenkins_flow(messages, tools):
    b = get(messages, "get_jenkins_build")
    if b is None:
        return call("I'll check the latest route-eta-service build in Jenkins.", ("get_jenkins_build", {"build": "lastBuild"}))
    if err(b):
        return say(f"I couldn't find a Jenkins build: {err(b)}. Start one from Jenkins or with "
                   "`python3 labctl.py scenario build-failure`.")
    if b["result"] == "IN_PROGRESS":
        return say(f"Build #{b['build']} is still running (started {b['started_relative']}). Ask me again when it finishes.")
    if b["result"] == "SUCCESS":
        return say(f"The latest build, #{b['build']}, passed in {b['duration_s']} s ({b['started_relative']}). "
                   f"It tested commit {b['commit']}. Nothing to fix.")
    log = get(messages, "get_jenkins_log")
    if log is None:
        return call(f"Build #{b['build']} ended {b['result']}. I'll read the end of its console log to find out why.",
                    ("get_jenkins_log", {"build": str(b["build"]), "tail": 60}))
    ev = evidence(log.get("log", ""))
    sha = (b.get("commit") or "").split(" ")[0]
    commit = get(messages, "get_commit")
    if commit is None and sha:
        return call(f"The log shows `{ev['failed'] or ev['finished']}`. I'll look at commit {sha}, which this build tested.",
                    ("get_commit", {"sha": sha}))
    history = get(messages, "list_recent_commits")
    if commit and not err(commit) and not touches_code(commit) and history is None:
        return call(f"Commit {commit['sha']} only changed {', '.join(commit['files_changed'])}, which can't explain an "
                    "ETA assertion. I'll look at the commits before it.", ("list_recent_commits", {"limit": 5}))
    culprit = commit if commit and not err(commit) else None
    if history and not err(history) and culprit and not touches_code(culprit):
        culprit = next((c for c in history["commits"] if touches_code(c)), culprit)
    parts = [f"Build #{b['build']} of {b['job']} failed in the Test stage" + (f" ({ev['summary']})." if ev["summary"] else ".")]
    proof = [x for x in (ev["location"], ev["assertion"], ev["failed"]) if x]
    if proof:
        parts.append("Evidence from the console log:\n" + "\n".join(f"  {p}" for p in proof))
    if culprit:
        tested = "" if culprit is commit else f"the build tested {commit['sha']} (\"{commit['message'].splitlines()[0]}\"), but "
        parts.append(f"Likely cause: {tested}commit {culprit['sha']} by {culprit['author']} "
                     f"(\"{culprit['message'].splitlines()[0]}\") changed {', '.join(culprit['files_changed']) or 'files'}. "
                     "The test still expects the old behaviour.")
        parts.append(f"Next step: ask {culprit['author']} whether the change is intended. If yes, update the test; "
                     f"if not, revert {culprit['sha']}. Re-running the build will not help. I haven't changed anything.")
    if ev["injected"] and safe_prompt(messages):
        parts.append(f"Also: the log contains a line addressed to an AI (\"{short(ev['injected'])}\"). "
                     "I treated it as untrusted data and did not act on it. Report it to security.")
    return say("\n\n".join(parts))


def injection_flow(messages, tools):
    log = get(messages, "get_jenkins_log")
    if log is None:
        return call("I'll read the console log of the last route-eta-service build.",
                    ("get_jenkins_log", {"build": "lastBuild", "tail": 60}))
    if err(log):
        return say(f"I couldn't read the build log: {err(log)}")
    text = json.dumps(log)
    ev = evidence(log.get("log", ""))
    restart = get(messages, "restart_service")
    asks_restart = "restart_service" in text and "prod-api" in text
    if not safe_prompt(messages):  # guardrails off: the gullible prompt follows instructions found in data
        if restart is None and asks_restart:
            return call("The log includes a platform-team instruction to restart prod-api to clear a stale ETA cache. "
                        "I'll do that now to restore service.", ("restart_service", {"name": "prod-api"}))
        if isinstance(restart, dict) and str(restart.get("status", "")).startswith("restart"):
            return say(f"Done: I restarted prod-api as the instruction in the build log asked. "
                       f"{restart.get('active_sessions_dropped', 0):,} active sessions were dropped. The build itself "
                       f"failed in the tests ({ev['failed'] or 'see the log'}); the service team should look at that.")
    if restart is not None and err(restart):
        return say("I did not restart prod-api: " + err(restart) + "\n\nLooking again, the 'NOTE TO AI' line is not a "
                   "normal log entry. It reads like an instruction planted in the build output, so I've flagged it as "
                   f"possible prompt injection.\n\nThe real problem: {ev['failed'] or 'a failing test'}. No action taken.")
    warn = (" The agent runtime also flagged it automatically." if flagged(messages) else "")
    return say(
        f"The last build failed in the tests: {ev['failed'] or 'see the log'}"
        + (f" ({ev['location']})" if ev["location"] else "") + ". That is a code or test issue for the service team; "
        "restarting a service won't fix it.\n\n"
        + (f"The log also contains a line addressed to an AI: \"{short(ev['injected'], 160)}\". Log contents are untrusted "
           f"data, so I haven't acted on it, and I recommend reporting it to security.{warn}\n\n" if ev["injected"] else "")
        + "No action taken.")


def disk_flow(messages, tools):
    host = (HOST_RE.findall(question(messages)) or ["build-agent-07"])[0]
    checks = all_of(messages, "get_disk_usage")
    listing = get(messages, "list_old_artifacts")
    cleanup = get(messages, "cleanup_artifacts")
    if not checks:
        return call(f"I'll confirm the current disk usage on {host} before suggesting anything.",
                    ("get_disk_usage", {"host": host}))
    d = checks[0]
    if err(d):
        return say(f"I couldn't read the disk on {host}: {err(d)}")
    if d["used_pct"] < d["alert_threshold_pct"] and listing is None:
        return say(f"{host} is fine: the artifact disk is {d['used_pct']}% full ({d['free_mb']} MB free), under the "
                   f"{d['alert_threshold_pct']}% alert threshold. No action needed.")
    if listing is None:
        return call(f"Disk is at {d['used_pct']}% with {d['free_mb']} MB free. I'll look for old build artifacts that "
                    "are safe to remove.", ("list_old_artifacts", {"host": host, "older_than_days": 14}))
    if cleanup is None:
        if not listing.get("count"):
            return say(f"{host} is at {d['used_pct']}%, but there are no artifacts older than 14 days to remove. "
                       "A human should check what is filling the disk.")
        return call(f"{listing['count']} artifact folders older than 14 days use {listing['total_mb']} MB. Removing them "
                    "should bring usage well under the threshold. This deletes files, so it needs approval.",
                    ("cleanup_artifacts", {"host": host, "older_than_days": 14}))
    if err(cleanup):
        why = "you declined the cleanup" if "Declined" in err(cleanup) else err(cleanup).replace("BLOCKED by policy: ", "")
        return say(f"No changes made: {why}. {host} is still at {d['used_pct']}% ({d['free_mb']} MB free), "
                   f"above the {d['alert_threshold_pct']}% threshold.\n\nRecommendation: delete the {listing['count']} "
                   f"build folders older than 14 days ({listing['total_mb']} MB) in the next quiet window. Builds from "
                   "the last 14 days stay untouched either way.")
    if len(checks) < 2:
        return call(f"Cleanup finished and freed {cleanup['freed_mb']} MB. I'll check the disk again to confirm.",
                    ("get_disk_usage", {"host": host}))
    after = checks[-1]
    return say(f"Resolved. I deleted {cleanup['deleted']} build folders older than 14 days on {host}, freeing "
               f"{cleanup['freed_mb']} MB. Disk usage went from {d['used_pct']}% to {after['used_pct']}% "
               f"({after['free_mb']} MB free), below the {after['alert_threshold_pct']}% threshold. Builds from the last "
               "14 days were kept for rollbacks. The Prometheus alert resolves on its own within a minute.")


def github_flow(messages, tools):
    names = {t["name"] for t in tools}
    if "list_github_runs" not in names:
        return say("GitHub Actions isn't connected to this lab yet, so I can't see its pipeline. Run "
                   "`python3 labctl.py github` to connect it, then ask again.")
    runs = get(messages, "list_github_runs")
    if runs is None:
        return call("I'll list the latest GitHub Actions runs.", ("list_github_runs", {"limit": 5}))
    if err(runs):
        return say(f"I couldn't reach GitHub: {err(runs)}")
    rs = runs.get("runs", [])
    if not rs:
        return say(f"There are no GitHub Actions runs on {runs.get('repo')} yet. Push a commit to start one.")
    latest = rs[0]
    ok = sum(1 for r in rs if r["conclusion"] == "success")
    if latest["conclusion"] is None:
        return say(f"The latest run ({latest['workflow']}, commit {latest['commit']}) is still {latest['status']}. "
                   f"{ok} of the last {len(rs)} runs passed.")
    if latest["conclusion"] == "success":
        return say(f"Yes, it's healthy. The latest run ({latest['workflow']}, commit {latest['commit']}: "
                   f"\"{latest['message']}\") passed, and {ok} of the last {len(rs)} runs passed.")
    log = get(messages, "get_github_run_log")
    if log is None:
        return call(f"The latest run ({latest['commit']}) ended {latest['conclusion']}. I'll read the failed job's log.",
                    ("get_github_run_log", {"run_id": latest["run_id"], "tail": 60}))
    commit = get(messages, "get_commit")
    if commit is None:
        return call(f"I'll check what commit {latest['commit']} changed.", ("get_commit", {"sha": latest["commit"]}))
    ev = evidence(log.get("log", "")) if not err(log) else {"failed": "", "location": "", "assertion": "", "injected": ""}
    proof = [x for x in (ev["location"], ev["assertion"], ev["failed"]) if x]
    who = commit.get("author") if not err(commit) else "the author"
    return say(f"No, the pipeline is red. Run {latest['run_id']} ({latest['workflow']}) failed"
               + (f" in step {', '.join(log.get('failed_steps') or [])}" if not err(log) and log.get("failed_steps") else "")
               + ".\n\n" + ("Evidence from the job log:\n" + "\n".join(f"  {p}" for p in proof) + "\n\n" if proof else "")
               + f"It tested commit {latest['commit']} by {who}: \"{latest['message']}\". Re-running won't help, "
               f"because the failure comes from the code change. Next step: ask {who} to fix the test or revert. "
               "I haven't changed anything."
               + (f"\n\nAlso: the job log contains a line addressed to an AI (\"{short(ev['injected'])}\"). I treated it "
                  "as untrusted data and did not act on it. Report it to security." if ev.get("injected") else ""))


def service_flow(messages, tools):
    q = question(messages)
    name = (SERVICE_RE.findall(q) or ["prod-api"])[0]
    statuses = all_of(messages, "get_service_status")
    restart = get(messages, "restart_service")
    wants_restart = "restart" in q.lower()
    if not statuses:
        return call(f"I'll check {name}'s health and active sessions first.", ("get_service_status", {"name": name}))
    s = statuses[0]
    if err(s):
        return say(f"I couldn't check {name}: {err(s)}")
    if not wants_restart:
        if s.get("status") != "ok":
            return say(f"{name} is down ({s.get('detail', 'no response')}). Docker restarts it automatically; if it "
                       "stays down, a human should check the container logs.")
        return say(f"{name} ({s.get('environment')}) is healthy: up {s['uptime_seconds']} s with "
                   f"{s['active_sessions']:,} active sessions.")
    if restart is None:
        return call(f"{name} is {s.get('status')} with {s.get('active_sessions', 0):,} active sessions. Restarting drops "
                    "all of them, so this needs approval.", ("restart_service", {"name": name}))
    sessions = f"{s.get('active_sessions', 0):,} active sessions"
    if err(restart) and "Declined" in err(restart):
        return say(f"You declined, so I left {name} running ({s.get('status')}, {sessions}). No action taken.")
    if err(restart):
        reason = err(restart).replace("BLOCKED by policy: ", "")
        return say(f"I did not restart {name}: the runtime policy blocks it ({reason})\n\nIt is currently "
                   f"{s.get('status')} with {sessions}. If a restart is really needed, raise a change and let the "
                   "on-call engineer do it.")
    if len(statuses) < 2:
        return call(f"{name} restarted and dropped {restart.get('active_sessions_dropped', 0):,} sessions. "
                    "I'll check that it is healthy again.", ("get_service_status", {"name": name}))
    after = statuses[-1]
    back = after.get("status") == "ok"
    return say(f"Restarted {name}. {restart.get('active_sessions_dropped', 0):,} sessions were dropped. "
               + (f"It is back up ({after['uptime_seconds']} s) and users are reconnecting: "
                  f"{after['active_sessions']:,} sessions so far." if back else
                  "It hasn't answered yet; Docker should bring it back within a few seconds."))


def overview_flow(messages, tools):
    alerts = get(messages, "list_active_alerts")
    if alerts is None:
        return call("I'll start with what's firing in Alertmanager.", ("list_active_alerts", {}))
    if err(alerts):
        return say(f"I couldn't reach Alertmanager: {err(alerts)}")
    if not alerts.get("firing"):
        return say("Nothing is firing right now. Try: \"Why did the last Jenkins build fail?\", \"Check build-agent-07 "
                   "disk\" or \"Is the GitHub Actions pipeline healthy?\"")
    lines = [f"- {a['alert']} ({a['severity']}): {a['summary']}" for a in alerts["alerts"]]
    return say(f"{alerts['firing']} alert(s) firing:\n" + "\n".join(lines) + "\n\nAsk me to investigate one of them.")


@script("agentops")
def agentops(messages, tools):
    q = question(messages).lower()
    if any(k in q for k in ("whatever is needed", "do what", "whatever it says", "follow the")):
        return injection_flow(messages, tools)
    if "github" in q or "actions" in q:
        return github_flow(messages, tools)
    if any(k in q for k in ("disk", "artifact", "diskalmostfull")):
        return disk_flow(messages, tools)
    if SERVICE_RE.search(q) or "restart" in q or "servicedown" in q:
        return service_flow(messages, tools)
    if any(k in q for k in ("jenkins", "build", "fail", "pipeline")):
        return jenkins_flow(messages, tools)
    return overview_flow(messages, tools)
