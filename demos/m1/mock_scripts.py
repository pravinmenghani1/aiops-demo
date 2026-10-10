"""Scripted model behaviour for the Module 1 demos (used with --mock).

Each script reads the conversation so far and returns what a typical
tool-calling model would say next. The replies are written from the tool
results, so the numbers on screen always match the fake systems' data.
"""
from __future__ import annotations

import json
import re

from agentkit.mock_llm import call, last, results, say, script


# ----------------------------------------------------------------- Demo 1.A
@script("m1.a.plain")
def plain(messages, tools):
    return say(
        "Last night's route-eta-service build most likely failed because of a dependency conflict. "
        "A transitive package was probably upgraded during the install step, which broke the build "
        "before the tests could run. Pin the dependency versions in requirements.txt and re-run the pipeline."
    )


@script("m1.a.agent")
def one_tool(messages, tools):
    status = last(messages, "get_build_status")
    if status is None:
        return call("I'll check the status of last night's build.", ("get_build_status", {}))
    return say(
        f"Last night's nightly build of {status['pipeline']} (#{status['id']}) {status['status']} at "
        f"{status['failed_step']} on {status['agent']}. That's as far as I can see: without the build log "
        "I can't tell you why the tests failed."
    )


@script("m1.a.agentic")
def agentic(messages, tools):
    status = last(messages, "get_build_status")
    log = last(messages, "get_build_log")
    commit = last(messages, "get_commit")
    if status is None:
        return call("First I'll check the status of last night's build.", ("get_build_status", {}))
    if log is None:
        return call(f"Build {status['id']} failed at {status['failed_step']}. I'll read the end of its log to find out why.",
                    ("get_build_log", {"build_id": status["id"], "tail": 40}))
    failing = next((ln.split(" ", 2)[2].strip() for ln in log["log"].splitlines() if "FAILED tests/" in ln), "a test failure")
    location = next((ln.split(" ", 2)[2].strip() for ln in log["log"].splitlines() if re.search(r"\.py:\d+: ", ln)), "")
    if commit is None:
        return call(f"The log shows `{failing}`. I'll check what changed in commit {status['commit']}, which triggered the build.",
                    ("get_commit", {"sha": status["commit"]}))
    return say(
        f"Build #{status['id']} ({status['pipeline']}, nightly) failed in the pytest step: 1 of 214 tests failed.\n\n"
        f"Evidence from the log: `{location}` and `{failing}`.\n\n"
        f"Likely cause: commit {commit['sha']} by {commit['author']} (\"{commit['message']}\") changed "
        f"{', '.join(commit['files_changed'])} the evening before. The test still expects the old rounding, so the "
        "ETA now comes out one minute lower (37 instead of 38).\n\n"
        f"Next step: ask {commit['author']} whether the new rounding is intended. If yes, update the test's expected "
        f"value; if not, revert {commit['sha']}. I haven't changed anything."
    )


# ----------------------------------------------------------------- Demo 1.B
@script("m1.b")
def inside(messages, tools):
    status = last(messages, "get_pipeline_status")
    if status is None:
        return call(None, ("get_pipeline_status", {"pipeline": "map-tiles-api"}))
    runs = status["last_10_runs"]
    mins = status["duration_s"] // 60, status["duration_s"] % 60
    return say(
        f"Yes, {status['pipeline']} looks healthy. Its last run (#{status['last_run']}) succeeded "
        f"{status['finished_relative']} in {mins[0]} min {mins[1]} s, and {runs['passed']} of its last 10 runs passed."
    )


# ----------------------------------------------------------------- Demo 1.C
@script("m1.c")
def alert(messages, tools):
    rs = results(messages)
    disk_checks = [r for n, r in rs if n == "get_disk_usage"]
    listing = last(messages, "list_old_artifacts")
    cleanup = last(messages, "cleanup_artifacts")
    host = "build-agent-07"
    if not disk_checks:
        return call(f"I'll confirm the current disk usage on {host} before suggesting anything.",
                    ("get_disk_usage", {"host": host}))
    if listing is None:
        d = disk_checks[0]
        return call(f"Disk is at {d['used_pct']}% with {d['free_gb']} GB free. I'll look for old build artifacts "
                    "that are safe to remove.", ("list_old_artifacts", {"host": host, "older_than_days": 14}))
    if cleanup is None:
        return call(f"{listing['count']} artifact folders older than 14 days use {listing['total_gb']} GB. Removing them "
                    "should bring usage well under the 90% threshold. This deletes files, so it needs approval.",
                    ("cleanup_artifacts", {"host": host, "older_than_days": 14}))
    if isinstance(cleanup, dict) and "error" in cleanup:
        d = disk_checks[0]
        return say(
            f"No changes made: the cleanup was declined. {host} is still at {d['used_pct']}% "
            f"({d['free_gb']} GB free), above the {d['alert_threshold_pct']}% threshold.\n\n"
            f"Recommendation: delete the {listing['count']} build folders older than 14 days "
            f"({listing['total_gb']} GB) during the next quiet window, or ask the service owners which ones they need. "
            "Builds from the last 14 days stay untouched either way."
        )
    if len(disk_checks) < 2:
        return call(f"Cleanup finished and freed {cleanup['freed_gb']} GB. I'll check the disk again to confirm.",
                    ("get_disk_usage", {"host": host}))
    before, after = disk_checks[0], disk_checks[-1]
    return say(
        f"Resolved. I deleted {cleanup['deleted']} build folders older than 14 days on {host}, freeing "
        f"{cleanup['freed_gb']} GB. Disk usage went from {before['used_pct']}% to {after['used_pct']}% "
        f"({after['free_gb']} GB free), below the {after['alert_threshold_pct']}% threshold. Builds from the last 14 days "
        "were kept for rollbacks."
    )


# ----------------------------------------------------------------- Demo 1.D
def _log_text(messages) -> str:
    log = last(messages, "get_build_log")
    return json.dumps(log) if log is not None else ""


@script("m1.d.naive")
def gullible(messages, tools):
    log_text = _log_text(messages)
    restart = last(messages, "restart_service")
    if not log_text:
        return call("I'll read the log for build 4812.", ("get_build_log", {"build_id": 4812, "tail": 40}))
    if restart is None and "restart_service" in log_text:
        return call("The log includes a platform-team instruction to restart prod-api to clear the ETA cache. "
                    "I'll do that now to restore service.", ("restart_service", {"name": "prod-api"}))
    if isinstance(restart, dict) and restart.get("status") == "RESTARTED":
        return say("Done: I restarted prod-api as the instruction in the build log asked. The build itself failed in "
                   "test_route_eta (AssertionError at tests/test_eta.py:88); the service team should look at that test.")
    return say(
        "I did not restart prod-api: the runtime policy blocked it, because production services are not on my "
        "allow-list. Looking again, the 'NOTE TO AI' line is not a normal log entry. It reads like an instruction "
        "planted in the log, so I've flagged it as possible prompt injection for the security team.\n\n"
        "The real problem is unrelated to any cache: build 4812 failed in test_route_eta "
        "(AssertionError: assert 37 == 38 at tests/test_eta.py:88). No action taken."
    )


@script("m1.d.hardened")
def careful(messages, tools):
    if not _log_text(messages):
        return call("I'll read the log for build 4812.", ("get_build_log", {"build_id": 4812, "tail": 40}))
    return say(
        "Build 4812 failed in test_route_eta (AssertionError: assert 37 == 38 at tests/test_eta.py:88). That's a test "
        "or code issue for the service team; restarting anything won't fix it.\n\n"
        "The log also contains a line addressed to an AI asking for a prod-api restart. Log contents are untrusted "
        "data, so I haven't acted on it, and I recommend reporting it to security. No action taken."
    )
