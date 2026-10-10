#!/usr/bin/env python3
"""Demo 1.C · Alert to action  (about 4 min)

A "disk 95% on build-agent-07" alert goes to an agent with three tools. It checks
the facts, proposes the least destructive fix, and waits for a human "yes" before
deleting anything. Run it twice: answer "no" the first time, "yes" the second.

    python demos/m1/c_alert_to_action.py              # live LLM, you answer the approval
    python demos/m1/c_alert_to_action.py --mock       # scripted fallback
    python demos/m1/c_alert_to_action.py --approve no # pre-answer the approval (for recordings)
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from agentkit import cli  # noqa: E402
from agentkit.agent import run_agent  # noqa: E402
from agentkit.llm import get_llm  # noqa: E402
from agentkit.tools import ToolBox  # noqa: E402
from agentkit.ui import UI  # noqa: E402

import mock_scripts  # noqa: E402,F401
from fake_systems import FakeHost  # noqa: E402

ALERT = ("ALERT [warning] build-agent-07: disk usage 95% on /var/lib/jenkins (threshold 90%). "
         "Investigate and fix it safely.")
SYSTEM = ("You are an L2 operations agent for the build platform. Check the facts with your tools before "
          "suggesting anything, and prefer the least destructive fix. Keep artifacts from the last 14 days for "
          "rollbacks. Tools that delete or change things need human approval; if the human declines, stop and "
          "summarise your recommendation instead of trying again. After any change, verify the result. "
          "Before each tool call, say in one short sentence what you are about to check or do.")


def main() -> None:
    p = cli.parser(__doc__.splitlines()[0])
    p.add_argument("--approve", choices=["yes", "no"], help="answer the approval prompt automatically")
    args = p.parse_args()
    cli.apply(args)
    ui = UI(interactive=not args.no_pause,
            approval=None if args.approve is None else args.approve == "yes")
    fake = FakeHost()

    box = ToolBox()

    @box.tool(params={"host": "Build agent hostname, for example build-agent-07"})
    def get_disk_usage(host: str) -> dict:
        """Get disk size, used and free space, and the alert threshold for a build agent's workspace disk."""
        return fake.get_disk_usage(host)

    @box.tool(params={"host": "Build agent hostname", "older_than_days": "Only list artifacts older than this"})
    def list_old_artifacts(host: str, older_than_days: int = 14) -> dict:
        """List build artifact folders older than N days on a build agent, with their sizes. Read-only."""
        return fake.list_old_artifacts(host, older_than_days)

    @box.tool(risky=True, params={"host": "Build agent hostname",
                                  "older_than_days": "Delete artifacts older than this (minimum 7)"})
    def cleanup_artifacts(host: str, older_than_days: int = 14) -> dict:
        """Delete build artifact folders older than N days on a build agent. Destructive: needs human approval."""
        return fake.cleanup_artifacts(host, older_than_days)

    ui.header("Demo 1.C · Alert to action", "Incoming alert, handed to the agent as-is:")
    ui.panel("ALERT", ALERT, "red")
    try:
        llm = get_llm(mock_script="m1.c")
        ui.banner(llm.describe())
        ui.pause("Press Enter to hand the alert to the agent")
        run_agent(llm, box, ALERT, system=SYSTEM, ui=ui, max_steps=8, require_approval=True)
    except Exception as err:
        cli.fail(ui, err)
    d = fake.get_disk_usage("build-agent-07")
    ui.note(f"Disk on build-agent-07 is now {d['used_pct']}% used ({d['free_gb']} GB free). "
            "Everything here is simulated; no real files were touched.")
    cli.save(ui, __file__, args.save, "Demo 1.C · Alert to action")


if __name__ == "__main__":
    main()
