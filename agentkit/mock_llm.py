"""Scripted stand-in for an LLM, used with --mock or AGENT_MODE=mock.

A script is a small Python function that looks at the conversation so far and
returns the next reply. Scripts are branch-aware: if a human declines an
approval, or a policy blocks a tool, the script reacts the way a real model
typically would. Nothing is sent over the network.
"""
from __future__ import annotations

import itertools
import json
from typing import Callable

from .llm import Reply, ToolCall

SCRIPTS: dict[str, Callable[[list[dict], list[dict]], Reply]] = {}
_ids = itertools.count(1)


def script(name: str):
    def register(fn):
        SCRIPTS[name] = fn
        return fn
    return register


def say(text: str) -> Reply:
    return Reply(text=text, message={"role": "assistant", "content": text, "tool_calls": []},
                 raw={"mock": True, "content": text})


def call(text: str | None, *calls: tuple[str, dict]) -> Reply:
    tcs = [ToolCall(f"call_{next(_ids)}", name, args) for name, args in calls]
    message = {"role": "assistant", "content": text, "tool_calls": [t.as_dict() for t in tcs]}
    raw = {"mock": True, "content": text,
           "tool_calls": [{"id": t.id, "name": t.name, "arguments": t.arguments} for t in tcs]}
    return Reply(text=text or "", tool_calls=tcs, message=message, raw=raw)


def results(messages: list[dict]) -> list[tuple[str, object]]:
    """[(tool_name, parsed_result), ...] in the order the tools ran."""
    out = []
    for m in messages:
        if m["role"] == "tool":
            try:
                out.append((m.get("name", "?"), json.loads(m["content"])))
            except (json.JSONDecodeError, TypeError):
                out.append((m.get("name", "?"), m["content"]))
    return out


def last(messages: list[dict], name: str):
    found = [r for n, r in results(messages) if n == name]
    return found[-1] if found else None


class ScriptedLLM:
    def __init__(self, name: str):
        if name not in SCRIPTS:
            raise KeyError(f"No mock script named {name!r}. Known: {sorted(SCRIPTS)}")
        self.name = name
        self.last_request: dict | None = None
        self.last_response: dict | None = None

    def describe(self) -> str:
        return f"MOCK MODE · scripted responses ({self.name}) · no LLM is called"

    def chat(self, messages: list[dict], tools: list[dict] | None = None) -> Reply:
        self.last_request = {"messages": messages, "tools": tools or []}
        reply = SCRIPTS[self.name](messages, tools or [])
        self.last_response = reply.raw
        return reply
