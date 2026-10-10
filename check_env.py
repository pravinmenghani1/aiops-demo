#!/usr/bin/env python3
"""Environment check for the course. Run it before Session 1:

    python check_env.py            # checks Python, .env and a real tool call to your LLM
    python check_env.py --mock     # checks everything except the LLM connection

Module 1 needs only Python and an LLM. Docker, Airflow, Jenkins and Databricks
checks are added in later modules.
"""
from __future__ import annotations

import argparse
import os
import platform
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

if os.name == "nt":  # enable ANSI colours in Windows consoles
    os.system("")

results: list[tuple[str, bool, str]] = []


def record(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    mark = "\x1b[38;5;77mPASS\x1b[0m" if ok else "\x1b[38;5;203mFAIL\x1b[0m"
    if os.environ.get("NO_COLOR") or not sys.stdout.isatty():
        mark = "PASS" if ok else "FAIL"
    print(f"  {mark}  {name}" + (f"  ·  {detail}" if detail else ""))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--mock", action="store_true", help="skip the live LLM call")
    args = ap.parse_args()
    print("\nAgentic AI for Operations Teams · environment check\n")

    v = sys.version_info
    record("Python 3.11 or newer", v >= (3, 11), f"{platform.python_version()} on {platform.system()}")
    if v < (3, 11):
        return summary()

    from agentkit import config  # loads .env
    from agentkit.llm import get_llm
    from agentkit.tools import ToolBox

    env_file = ROOT / ".env"
    if env_file.exists():
        record(".env file", True, "found")
    elif args.mock or os.environ.get("LLM_PROVIDER"):
        record(".env file", True, "not found; using environment variables" if not args.mock else "not needed for --mock")
    else:
        record(".env file", False, "copy .env.example to .env and fill in one provider")
    proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
    ca = os.environ.get("SSL_CERT_FILE") or os.environ.get("REQUESTS_CA_BUNDLE")
    if proxy or ca:
        record("Corporate network settings", True, f"proxy={'set' if proxy else 'none'}, CA bundle={ca or 'default'}")
    if ca:
        record("CA bundle file exists", Path(ca).exists(), ca)

    if args.mock:
        record("LLM connection", True, "skipped (--mock): demos will use scripted responses")
        return summary()

    provider = (config.env("LLM_PROVIDER") or "").lower()
    record("LLM_PROVIDER set", bool(provider), provider or "set openai, azure or bedrock in .env")
    if not provider:
        return summary()

    box = ToolBox()

    @box.tool(params={"host": "Hostname to check"})
    def ping_host(host: str) -> dict:
        """Check whether a host answers. Returns its status."""
        return {"host": host, "status": "up", "latency_ms": 12}

    try:
        llm = get_llm()
        record("Client configured", True, llm.describe())
        msgs = [{"role": "system", "content": "Use the tool to answer."},
                {"role": "user", "content": "Is build-agent-07 up? Use the ping_host tool."}]
        reply = llm.chat(msgs, tools=box.schemas())
        record("Model answered", True, (reply.text[:60] + "…") if reply.text else "(tool call, no text)")
        called = any(t.name == "ping_host" for t in reply.tool_calls)
        record("Model can call tools", called,
               "ping_host requested" if called else "no tool call: pick a model that supports tool/function calling")
        if called:
            msgs.append(reply.message)
            for t in reply.tool_calls:
                msgs.append(box.as_message(t, box.run(t.name, t.arguments)))
            final = llm.chat(msgs, tools=box.schemas())
            record("Tool result round trip", bool(final.text), (final.text or "")[:70])
    except Exception as err:  # report, don't crash, so the learner sees what to fix
        record("LLM call", False, f"{type(err).__name__}: {str(err)[:220]}")
    return summary()


def summary() -> int:
    failed = [r for r in results if not r[1]]
    print()
    if failed:
        print(f"  {len(failed)} check(s) failed. Fix them, or run the demos with --mock in the meantime.\n")
        return 1
    print("  All checks passed. You're ready for Module 1.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
