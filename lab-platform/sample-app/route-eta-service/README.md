# route-eta-service

A tiny ETA calculator used by the AgentOps Lab. Jenkins and GitHub Actions both build and test it,
and `labctl.py scenario ...` adds the commits that break it.

    python3 -m pytest -q
