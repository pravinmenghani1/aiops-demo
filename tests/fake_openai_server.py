"""A local stand-in for an OpenAI-compatible chat completions endpoint.

It speaks the real wire format (OpenAI and Azure OpenAI URLs), checks every
request strictly, and answers using the same scripted behaviour as --mock.
This lets the *live* code path (HTTP, JSON conversion, tool-call round trips)
be tested without an API key or network access.
"""
from __future__ import annotations

import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "demos" / "m1"))

import mock_scripts  # noqa: E402,F401  (registers scripts)
from agentkit.mock_llm import SCRIPTS  # noqa: E402

import a_three_answers as A  # noqa: E402
import b_inside_tool_call as B  # noqa: E402
import c_alert_to_action as Cc  # noqa: E402
import d_injection as D  # noqa: E402

SYSTEM_TO_SCRIPT = {A.PLAIN_SYSTEM: "m1.a.plain", A.AGENT_SYSTEM: "m1.a.agent", A.AGENTIC_SYSTEM: "m1.a.agentic",
                    B.SYSTEM: "m1.b", Cc.SYSTEM: "m1.c", D.GULLIBLE_SYSTEM: "m1.d.naive",
                    D.HARDENED_SYSTEM: "m1.d.hardened"}


class WireError(Exception):
    pass


def check_and_convert(body: dict) -> tuple[list[dict], list[dict]]:
    """Validate an OpenAI chat request and convert it back to agentkit's neutral messages."""
    if not isinstance(body.get("messages"), list) or not body["messages"]:
        raise WireError("messages must be a non-empty list")
    tools = body.get("tools") or []
    for t in tools:
        if t.get("type") != "function" or not {"name", "description", "parameters"} <= set(t.get("function", {})):
            raise WireError(f"bad tool definition: {t}")
        if t["function"]["parameters"].get("type") != "object":
            raise WireError("tool parameters must be a JSON-schema object")
    neutral, pending, names = [], set(), {}
    for m in body["messages"]:
        role = m.get("role")
        if role in ("system", "user"):
            if not isinstance(m.get("content"), str):
                raise WireError(f"{role} content must be a string")
            if pending:
                raise WireError(f"tool calls {pending} were never answered before a {role} message")
            neutral.append({"role": role, "content": m["content"]})
        elif role == "assistant":
            if pending:
                raise WireError(f"tool calls {pending} were never answered")
            calls = []
            for tc in m.get("tool_calls") or []:
                if tc.get("type") != "function" or not isinstance(tc["function"].get("arguments"), str):
                    raise WireError(f"bad assistant tool_call: {tc}")
                pending.add(tc["id"])
                names[tc["id"]] = tc["function"]["name"]
                calls.append({"id": tc["id"], "name": tc["function"]["name"],
                              "arguments": json.loads(tc["function"]["arguments"])})
            if not calls and m.get("content") in (None, ""):
                raise WireError("assistant message with neither content nor tool_calls")
            neutral.append({"role": "assistant", "content": m.get("content"), "tool_calls": calls})
        elif role == "tool":
            tid = m.get("tool_call_id")
            if tid not in pending:
                raise WireError(f"tool message answers unknown or already answered call {tid}")
            if not isinstance(m.get("content"), str):
                raise WireError("tool content must be a string")
            pending.discard(tid)
            neutral.append({"role": "tool", "tool_call_id": tid, "name": names[tid], "content": m["content"]})
        else:
            raise WireError(f"unknown role {role!r}")
    if pending:
        raise WireError(f"request ends with unanswered tool calls {pending}")
    schemas = [t["function"] for t in tools]
    return neutral, schemas


class Handler(BaseHTTPRequestHandler):
    server_version = "FakeOpenAI/1.0"
    log: list[dict] = []
    reject_temperature_for: set[str] = {"no-temp-model"}

    def log_message(self, *args):  # keep test output quiet
        pass

    def _send(self, code: int, obj: dict) -> None:
        data = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):  # noqa: N802
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        azure = self.path.startswith("/openai/deployments/")
        entry = {"path": self.path, "azure": azure, "ok": True}
        Handler.log.append(entry)
        try:
            if azure:
                if "api-version=" not in self.path or not self.headers.get("api-key"):
                    raise WireError("Azure request needs api-version and an api-key header")
                model = self.path.split("/")[3]
            else:
                if self.path != "/v1/chat/completions":
                    raise WireError(f"unexpected path {self.path}")
                if not self.headers.get("Authorization", "").startswith("Bearer "):
                    raise WireError("missing bearer token")
                model = body.get("model") or ""
                if not model:
                    raise WireError("model is required")
            if model in self.reject_temperature_for and "temperature" in body:
                entry["temperature_rejected"] = True
                return self._send(400, {"error": {"message": "Unsupported value: 'temperature' does not support 0 "
                                                             "with this model. Only the default (1) value is supported.",
                                                  "param": "temperature"}})
            neutral, schemas = check_and_convert(body)
            system = next((m["content"] for m in neutral if m["role"] == "system"), "")
            reply = SCRIPTS[SYSTEM_TO_SCRIPT[system]](neutral, schemas)
        except (WireError, KeyError, json.JSONDecodeError) as e:
            entry["ok"] = False
            entry["error"] = str(e)
            return self._send(400, {"error": {"message": f"fake server rejected request: {e}"}})
        msg: dict = {"role": "assistant", "content": reply.text or None}
        if reply.tool_calls:
            msg["tool_calls"] = [{"id": t.id, "type": "function",
                                  "function": {"name": t.name, "arguments": json.dumps(t.arguments)}}
                                 for t in reply.tool_calls]
        self._send(200, {"id": "chatcmpl-fake", "object": "chat.completion", "model": model,
                         "choices": [{"index": 0, "message": msg,
                                      "finish_reason": "tool_calls" if reply.tool_calls else "stop"}],
                         "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}})


def start() -> tuple[ThreadingHTTPServer, int]:
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, srv.server_address[1]
