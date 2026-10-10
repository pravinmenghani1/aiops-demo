"""Configuration from environment variables (set by docker-compose from lab-platform/.env)."""
from __future__ import annotations

import os
from dataclasses import dataclass


def _env(name: str, default: str = "") -> str:
    value = os.environ.get(name, "")
    return value if value else default


def _map(raw: str) -> dict[str, str]:
    """'prod-api=http://prod-api:8080,staging-api=http://staging-api:8080' -> dict"""
    out = {}
    for part in raw.split(","):
        if "=" in part:
            k, v = part.split("=", 1)
            out[k.strip()] = v.strip()
    return out


@dataclass
class Settings:
    port: int
    data_dir: str
    jenkins_url: str
    jenkins_user: str
    jenkins_password: str
    jenkins_job: str
    github_repo: str
    github_token: str
    github_api: str
    local_git_dir: str
    build_agents: dict
    ops_token: str
    services: dict
    services_token: str
    prometheus_url: str
    alertmanager_url: str
    guardrails_default: bool
    approval_timeout_s: int
    max_steps: int

    @property
    def github_enabled(self) -> bool:
        return bool(self.github_repo and self.github_token)


def load() -> Settings:
    return Settings(
        port=int(_env("AGENTOPS_PORT", "8000")),
        data_dir=_env("DATA_DIR", "/data"),
        jenkins_url=_env("JENKINS_URL", "http://jenkins:8080"),
        jenkins_user=_env("JENKINS_USER", "agentops"),
        jenkins_password=_env("JENKINS_AGENT_PASSWORD"),
        jenkins_job=_env("JENKINS_JOB", "route-eta-service"),
        github_repo=_env("GITHUB_REPO"),
        github_token=_env("GITHUB_TOKEN"),
        github_api=_env("GITHUB_API", "https://api.github.com"),
        local_git_dir=_env("LOCAL_GIT_DIR", "/srv/git/route-eta-service.git"),
        build_agents=_map(_env("BUILD_AGENTS", "build-agent-07=http://build-agent-07:8700")),
        ops_token=_env("OPS_TOKEN"),
        services=_map(_env("SERVICES", "prod-api=http://prod-api:8080,staging-api=http://staging-api:8080")),
        services_token=_env("SERVICES_ADMIN_TOKEN"),
        prometheus_url=_env("PROMETHEUS_URL", "http://prometheus:9090"),
        alertmanager_url=_env("ALERTMANAGER_URL", "http://alertmanager:9093"),
        guardrails_default=_env("GUARDRAILS", "on").lower() != "off",
        approval_timeout_s=int(_env("APPROVAL_TIMEOUT_SECONDS", "600")),
        max_steps=int(_env("AGENT_MAX_STEPS", "10")),
    )
