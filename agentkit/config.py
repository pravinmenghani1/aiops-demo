"""Settings for the lab kit, read from environment variables and an optional .env file.

No third-party packages: the .env loader below is a few lines of standard library code.
Values already set in the environment always win over the .env file.
"""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_dotenv(path: Path | None = None) -> None:
    path = path or ROOT / ".env"
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip().removeprefix("export ").strip()
        value = value.strip()
        if value.startswith("#"):
            value = ""
        if " #" in value and not value.startswith(("'", '"')):
            value = value.split(" #", 1)[0].strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        os.environ.setdefault(key, value)


def env(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(name)
    return value if value not in (None, "") else default


def require(name: str) -> str:
    value = env(name)
    if value is None:
        raise ConfigError(
            f"{name} is not set. Add it to .env (see .env.example) or run the demo with --mock."
        )
    return value


def agent_mode() -> str:
    """'live' calls a real LLM; 'mock' uses scripted responses and calls nothing."""
    return (env("AGENT_MODE", "live") or "live").lower()


class ConfigError(RuntimeError):
    pass


load_dotenv()
