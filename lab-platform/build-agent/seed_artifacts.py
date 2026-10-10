#!/usr/bin/env python3
"""Fill the artifact disk with realistic old and recent build folders, to trigger the disk alert.

    python3 /opt/agentops/seed_artifacts.py --target-pct 94

About 60% of the target goes into folders 16-45 days old (safe to clean up) and the rest
into folders 2-10 days old (kept for rollbacks), so a correct cleanup lands well below 90%.
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ops_api  # noqa: E402

JOBS = ["route-eta-service", "map-tiles-api", "traffic-ingest"]
MB = 1024 * 1024


def jenkins_ids() -> tuple:
    try:
        import pwd
        pw = pwd.getpwnam("jenkins")
        return pw.pw_uid, pw.pw_gid
    except (ImportError, KeyError):
        return None


def write_folder(job: str, build: int, size: int, age_days: float) -> None:
    folder = ops_api.ROOT / job / str(build)
    folder.mkdir(parents=True, exist_ok=True)
    ids = jenkins_ids()
    if ids and os.geteuid() == 0:  # run as root via `docker compose exec`: Jenkins builds must still write here
        os.chown(folder.parent, *ids)
    chunk = b"\0" * MB
    with open(folder / "debug-symbols.bin", "wb") as f:
        left = size
        while left > 0:
            n = min(left, MB)
            f.write(chunk[:n])
            left -= n
    t = time.time() - age_days * 86400
    os.utime(folder / "debug-symbols.bin", (t, t))
    os.utime(folder, (t, t))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--target-pct", type=float, default=94)
    ap.add_argument("--old-share", type=float, default=0.6, help="share of the target used by old folders")
    ap.add_argument("--force", action="store_true", help="allow disks larger than 4 GB")
    args = ap.parse_args()

    d = ops_api.disk()
    total, used = d["_total_bytes"], d["_used_bytes"]
    if total > 4 * 1024 * MB and not args.force:
        print(f"Refusing: {ops_api.ROOT} is {total // MB} MB. This script is meant for the small lab tmpfs.")
        return 2
    need = int(total * args.target_pct / 100) - used
    if need <= 0:
        print(f"Already at {d['used_pct']}%: nothing to seed.")
        return 0
    old_bytes = int(need * args.old_share)
    recent_bytes = need - old_bytes
    old_ages = [45, 38, 31, 26, 21, 17]
    recent_ages = [9, 6, 4, 2]
    build = 4600
    for i, age in enumerate(old_ages):
        write_folder(JOBS[i % 3], build + i, old_bytes // len(old_ages), age + 0.5)
    for i, age in enumerate(recent_ages):
        write_folder(JOBS[i % 3], build + 50 + i, recent_bytes // len(recent_ages), age + 0.5)
    print(f"Seeded {need // MB} MB: disk now {ops_api.disk()['used_pct']}% used "
          f"({len(old_ages)} folders older than 14 days, {len(recent_ages)} recent).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
