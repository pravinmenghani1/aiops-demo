"""Guardrails that live in code, not in the prompt.

A Policy is checked by the agent loop *before* any tool runs, so a model that
has been talked into doing something unsafe still cannot do it.
"""
from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass, field
from typing import Callable

INJECTION_PATTERNS = [
    r"ignore\s+(all\s+|any\s+|your\s+|the\s+|previous\s+|prior\s+)*instructions",
    r"disregard\s+.{0,40}(instructions|rules|guidelines)",
    r"note\s+to\s+(the\s+)?(ai|assistant|agent|llm)",
    r"\byou\s+are\s+now\b",
    r"system\s+prompt",
    r"as\s+an\s+ai\s+(model|assistant)\s*,?\s*you\s+must",
]
_INJECTION_RE = re.compile("|".join(INJECTION_PATTERNS), re.IGNORECASE)


def scan_for_injection(text: str) -> list[tuple[int, str]]:
    """Return (line_number, line) for lines that look like instructions aimed at an AI."""
    return [(i, line.strip()) for i, line in enumerate(text.splitlines(), 1) if _INJECTION_RE.search(line)]


@dataclass
class Rule:
    tool: str
    check: Callable[[dict], bool]  # True = allowed
    reason: str


@dataclass
class Policy:
    rules: list[Rule] = field(default_factory=list)

    def allow_only(self, tool: str, arg: str, patterns: list[str], reason: str) -> "Policy":
        """Allow `tool` only when argument `arg` matches one of the glob patterns."""
        self.rules.append(Rule(tool, lambda a: any(fnmatch.fnmatch(str(a.get(arg, "")), p) for p in patterns), reason))
        return self

    def require(self, tool: str, check: Callable[[dict], bool], reason: str) -> "Policy":
        """Allow `tool` only when check(arguments) is True."""
        self.rules.append(Rule(tool, check, reason))
        return self

    def deny(self, tool: str, reason: str) -> "Policy":
        self.rules.append(Rule(tool, lambda a: False, reason))
        return self

    def check(self, name: str, arguments: dict) -> tuple[bool, str]:
        for rule in self.rules:
            if rule.tool != name:
                continue
            try:
                ok = rule.check(arguments or {})
            except Exception:  # noqa: BLE001  malformed arguments never slip past a rule
                ok = False
            if not ok:
                return False, rule.reason
        return True, ""
