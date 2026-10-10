#!/usr/bin/env python3
"""Self-test for the Module 1 demo kit. Needs no API key and no network.

    python tests/test_kit.py

1. Runs every demo in --mock mode and checks the key moments appear.
2. Runs every demo in *live* mode against a local fake OpenAI endpoint and a
   fake Azure OpenAI endpoint, which check each request strictly. This proves
   the HTTP and tool-calling code path works end to end.
3. Checks the Amazon Bedrock message conversion against the Converse API rules.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEMOS = ROOT / "demos" / "m1"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

CASES = [
    ("a_three_answers.py", [], ["Run 1", "Run 3", "tests/test_eta.py:88", "Yes: quotes the failing line"]),
    ("b_inside_tool_call.py", [], ["Nothing has touched the CI system yet", "get_pipeline_status", "map-tiles-api looks healthy"]),
    ("c_alert_to_action.py", ["--approve", "no"], ["APPROVAL NEEDED", "declined", "No changes made", "now 95% used"]),
    ("c_alert_to_action.py", ["--approve", "yes"], ["approved", "freed_gb", "from 95% to 64%", "now 64% used"]),
    ("d_injection.py", ["--guardrails", "off"], ["restart_service", "UNINTENDED PRODUCTION ACTION"]),
    ("d_injection.py", ["--guardrails", "on"], ["Possible prompt injection", "BLOCKED", "STOPPED BY CODE"]),
    ("d_injection.py", ["--guardrails", "on", "--hardened"], ["Possible prompt injection", "NO ACTION TAKEN"]),
]

failures: list[str] = []


def run(script: str, args: list[str], env_extra: dict) -> str:
    env = {k: v for k, v in os.environ.items() if not k.startswith(("LLM_", "OPENAI_", "AZURE_", "AGENT_"))}
    env.update({"NO_COLOR": "1", **env_extra})
    proc = subprocess.run([sys.executable, str(DEMOS / script), "--no-pause", *args], capture_output=True,
                          text=True, env=env, timeout=60, stdin=subprocess.DEVNULL)
    return proc.stdout + proc.stderr + f"\n[exit {proc.returncode}]"


def check(label: str, out: str, expected: list[str]) -> None:
    missing = [e for e in expected if e not in out]
    if "[exit 0]" not in out:
        missing.append("exit code 0")
    if missing:
        failures.append(f"{label}: missing {missing}\n{out[-1500:]}")
        print(f"  FAIL  {label}  missing: {missing}")
    else:
        print(f"  ok    {label}")


def main() -> int:
    print("1) Mock mode")
    for script, args, expected in CASES:
        check(f"mock  {script} {' '.join(args)}", run(script, args + ["--mock"], {}), expected)

    print("2) Live code path against a local fake OpenAI endpoint")
    import fake_openai_server as fake

    srv, port = fake.start()
    openai_env = {"LLM_PROVIDER": "openai", "OPENAI_BASE_URL": f"http://127.0.0.1:{port}/v1",
                  "OPENAI_API_KEY": "test-key", "LLM_MODEL": "fake-model"}
    for script, args, expected in CASES:
        check(f"openai {script} {' '.join(args)}", run(script, args, openai_env), expected)

    print("   ... with a model that rejects temperature=0 (auto-retry without it)")
    before = len(fake.Handler.log)
    out = run("b_inside_tool_call.py", [], {**openai_env, "LLM_MODEL": "no-temp-model"})
    check("openai temperature fallback", out, ["map-tiles-api looks healthy"])
    if not any(e.get("temperature_rejected") for e in fake.Handler.log[before:]):
        failures.append("temperature fallback was not exercised")

    print("   ... and through the Azure OpenAI URL format")
    azure_env = {"LLM_PROVIDER": "azure", "AZURE_OPENAI_ENDPOINT": f"http://127.0.0.1:{port}",
                 "AZURE_OPENAI_API_KEY": "test-key", "AZURE_OPENAI_DEPLOYMENT": "gpt-demo"}
    check("azure  c_alert_to_action.py --approve yes", run("c_alert_to_action.py", ["--approve", "yes"], azure_env),
          ["from 95% to 64%"])
    bad = [e for e in fake.Handler.log if not e["ok"] and "temperature_rejected" not in e]
    if bad:
        failures.append(f"fake endpoint rejected {len(bad)} request(s): {bad[:3]}")
    print(f"   fake endpoint served {len(fake.Handler.log)} requests, {len(bad)} malformed")
    srv.shutdown()

    print("3) Amazon Bedrock Converse message shapes")
    check_bedrock()

    print()
    if failures:
        print(f"{len(failures)} problem(s):\n" + "\n\n".join(failures))
        return 1
    print("All checks passed.")
    return 0


def check_bedrock() -> None:
    from agentkit import llm as L

    conv = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "disk alert"},
        {"role": "assistant", "content": "checking", "tool_calls": [
            {"id": "t1", "name": "get_disk_usage", "arguments": {"host": "a"}},
            {"id": "t2", "name": "list_old_artifacts", "arguments": {"host": "a", "older_than_days": 14}}]},
        {"role": "tool", "tool_call_id": "t1", "name": "get_disk_usage", "content": json.dumps({"used_pct": 95})},
        {"role": "tool", "tool_call_id": "t2", "name": "list_old_artifacts", "content": json.dumps([1, 2])},
        {"role": "assistant", "content": None, "tool_calls": [
            {"id": "t3", "name": "cleanup_artifacts", "arguments": {"host": "a"}}]},
        {"role": "tool", "tool_call_id": "t3", "name": "cleanup_artifacts", "content": json.dumps({"error": "declined"})},
        {"role": "assistant", "content": "done", "tool_calls": []},
        {"role": "user", "content": "thanks"},
    ]
    system, msgs = L.to_bedrock_messages(conv)
    problems = []
    if system != [{"text": "sys"}]:
        problems.append("system blocks")
    roles = [m["role"] for m in msgs]
    if roles[0] != "user" or any(a == b for a, b in zip(roles, roles[1:])):
        problems.append(f"roles must start with user and alternate: {roles}")
    allowed = {"text", "toolUse", "toolResult"}
    uses = set()
    for m in msgs:
        for b in m["content"]:
            if len(b) != 1 or next(iter(b)) not in allowed:
                problems.append(f"bad block {b}")
            if "toolUse" in b:
                uses.add(b["toolUse"]["toolUseId"])
                if not isinstance(b["toolUse"]["input"], dict):
                    problems.append("toolUse.input must be an object")
            if "toolResult" in b:
                tr = b["toolResult"]
                if tr["toolUseId"] not in uses:
                    problems.append(f"toolResult before its toolUse: {tr['toolUseId']}")
                if m["role"] != "user":
                    problems.append("toolResult must be in a user message")
                for cb in tr["content"]:
                    if "json" in cb and not isinstance(cb["json"], dict):
                        problems.append("toolResult json must be an object")
    results = [b["toolResult"] for m in msgs for b in m["content"] if "toolResult" in b]
    if results[-1].get("status") != "error":
        problems.append("declined tool result should carry status=error")
    if len(msgs[2]["content"]) != 2:
        problems.append("parallel tool results should share one user message")
    tools = L.to_bedrock_tools([{"name": "x", "description": "d", "parameters": {"type": "object", "properties": {}}}])
    if tools != {"tools": [{"toolSpec": {"name": "x", "description": "d",
                                         "inputSchema": {"json": {"type": "object", "properties": {}}}}}]}:
        problems.append("toolConfig shape")
    resp = {"output": {"message": {"role": "assistant", "content": [
        {"text": "I'll check."}, {"toolUse": {"toolUseId": "tooluse_abc", "name": "get_disk_usage", "input": {"host": "a"}}}]}},
        "stopReason": "tool_use", "ResponseMetadata": {"RequestId": "x"}}
    r = L.from_bedrock_response(resp)
    if r.text != "I'll check." or r.tool_calls[0].name != "get_disk_usage" or "ResponseMetadata" in r.raw:
        problems.append("response parsing")
    for p in problems:
        failures.append("bedrock: " + p)
    print("  ok    bedrock conversions" if not problems else f"  FAIL  bedrock: {problems}")


if __name__ == "__main__":
    sys.exit(main())
