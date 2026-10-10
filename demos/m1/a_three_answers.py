#!/usr/bin/env python3
"""Demo 1.A · Same question, three answers  (about 8 min)

One question, "Why did last night's build of route-eta-service fail?", asked three ways:
  1. a plain LLM with no data
  2. an agent with one tool: get_build_status
  3. an agentic workflow that also reads the log and checks the commit

    python demos/m1/a_three_answers.py            # live LLM from .env
    python demos/m1/a_three_answers.py --mock     # scripted fallback, no network
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from agentkit import cli  # noqa: E402
from agentkit.agent import run_agent  # noqa: E402
from agentkit.llm import get_llm  # noqa: E402
from agentkit.tools import ToolBox  # noqa: E402
from agentkit.ui import UI, c  # noqa: E402

import mock_scripts  # noqa: E402,F401  (registers the --mock responses)
from fake_systems import FakeCI  # noqa: E402

QUESTION = "Why did last night's build of route-eta-service fail?"

PLAIN_SYSTEM = ("You are the build-support assistant for the platform team. "
                "Give a direct, specific answer and name the most likely cause.")
AGENT_SYSTEM = ("You are the build-support assistant for the platform team. Use your tool to check facts "
                "before answering. Before calling a tool, say in one short sentence what you will check.")
AGENTIC_SYSTEM = ("You are an L2 build-support agent. Investigate before answering: check the build status, read "
                  "the relevant part of the log, then check the commit that triggered the build. Before each tool "
                  "call, say in one short sentence what you will check and why. In the final answer, quote the exact "
                  "log line that proves the cause, name the likely change, and recommend one next step. "
                  "Do not take any actions.")


def build_tools(ci: FakeCI, full: bool) -> ToolBox:
    box = ToolBox()

    @box.tool(params={"build_id": "CI build number. Leave out to get last night's nightly build."})
    def get_build_status(build_id: int | None = None) -> dict:
        """Get the status of a CI build: pipeline, result, failed step, build agent and commit."""
        return ci.get_build_status(build_id)

    if full:
        @box.tool(params={"build_id": "CI build number", "tail": "How many lines from the end of the log (max 80)"})
        def get_build_log(build_id: int, tail: int = 40) -> dict:
            """Read the last lines of a CI build's console log."""
            return ci.get_build_log(build_id, tail)

        @box.tool(params={"sha": "Commit hash, at least 7 characters"})
        def get_commit(sha: str) -> dict:
            """Get a commit's author, message, time and the files it changed."""
            return ci.get_commit(sha)

    return box


def main() -> None:
    p = cli.parser(__doc__.splitlines()[0])
    args = p.parse_args()
    cli.apply(args)
    ui = UI(interactive=not args.no_pause)
    ci = FakeCI()

    ui.header("Demo 1.A · Same question, three answers", f'Question for all three: "{QUESTION}"')
    runs = [
        ("Run 1 · Plain LLM, no data", "m1.a.plain", PLAIN_SYSTEM, ToolBox()),
        ("Run 2 · Agent with one tool (get_build_status)", "m1.a.agent", AGENT_SYSTEM, build_tools(ci, full=False)),
        ("Run 3 · Agentic workflow (status, log, commit)", "m1.a.agentic", AGENTIC_SYSTEM, build_tools(ci, full=True)),
    ]
    outcomes = []
    try:
        for i, (title, script, system, box) in enumerate(runs):
            llm = get_llm(mock_script=script)
            if i == 0:
                ui.banner(llm.describe())
            ui.section(title)
            tools = ", ".join(t["name"] for t in box.schemas()) or "none"
            ui.note(f"Tools available: {tools}")
            result = run_agent(llm, box, QUESTION, system=system, ui=ui, max_steps=6)
            outcomes.append(result)
            if i < len(runs) - 1:
                ui.pause(f"Press Enter for {runs[i + 1][0].split(' · ')[0]}")
    except Exception as err:  # keep the room moving if the live call fails
        cli.fail(ui, err)

    ui.section("What changed between the three runs?")
    seen = ["nothing", "build status", "status, log and commit"]
    grounded = [c("No: it guessed", "red"), c("Partly: knows that it failed", "amber"),
                c("Yes: quotes the failing line", "green")]
    rows = [[name.split(" · ")[1].split(" (")[0], seen[i], str(len(outcomes[i].tool_calls)), grounded[i]]
            for i, (name, *_rest) in enumerate(runs)]
    ui.table(["Approach", "Data it saw", "Tool calls", "Grounded?"], rows)
    ui.note("Ask the room: which answer would you paste into an incident ticket?")
    cli.save(ui, __file__, args.save, "Demo 1.A · Same question, three answers")


if __name__ == "__main__":
    main()
