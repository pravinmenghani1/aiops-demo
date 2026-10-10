#!/usr/bin/env python3
"""Demo 1.B · Inside a tool call  (about 3 min)

Slows one tool call down to show exactly what travels between your code and the model:
  1. what we send (the question and the tool definition)
  2. what the model sends back (a request to call a tool; nothing has run yet)
  3. our code running the tool
  4. the result going back, and the final answer

    python demos/m1/b_inside_tool_call.py             # live LLM from .env
    python demos/m1/b_inside_tool_call.py --raw       # also show the provider's raw JSON
    python demos/m1/b_inside_tool_call.py --mock      # scripted fallback
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from agentkit import cli  # noqa: E402
from agentkit.llm import get_llm  # noqa: E402
from agentkit.tools import ToolBox  # noqa: E402
from agentkit.ui import UI  # noqa: E402

import mock_scripts  # noqa: E402,F401
from fake_systems import FakeCI  # noqa: E402

QUESTION = "Is the map-tiles-api pipeline healthy right now?"
SYSTEM = "You are a CI assistant. Use your tools to check live status; never guess."


def main() -> None:
    p = cli.parser(__doc__.splitlines()[0])
    p.add_argument("--raw", action="store_true", help="also print the provider's raw request/response JSON")
    args = p.parse_args()
    cli.apply(args)
    ui = UI(interactive=not args.no_pause)
    ci = FakeCI()

    box = ToolBox()

    @box.tool(params={"pipeline": "Pipeline name, for example map-tiles-api"})
    def get_pipeline_status(pipeline: str) -> dict:
        """Get the latest run status of a CI pipeline, with its duration and recent pass rate."""
        return ci.get_pipeline_status(pipeline)

    ui.header("Demo 1.B · Inside a tool call", f'Question: "{QUESTION}"')
    try:
        llm = get_llm(mock_script="m1.b")
        ui.banner(llm.describe())
        messages = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": QUESTION}]

        ui.section("1 · What we send the model", "teal")
        ui.json_block("messages", messages, "teal")
        ui.json_block("tools: the tool definition (the model's only manual)", box.schemas(), "teal")
        ui.pause("Press Enter to send it")

        reply = llm.chat(messages, tools=box.schemas())
        ui.section("2 · What the model sends back", "coral")
        if args.raw and llm.last_response is not None:
            ui.json_block("raw response from the provider", llm.last_response, "coral")
        if not reply.tool_calls:
            ui.note("The model answered without asking for the tool this time. Run again, or use --mock.")
            ui.answer(reply.text)
            cli.save(ui, __file__, args.save, "Demo 1.B · Inside a tool call")
            return
        ui.json_block("the model's reply", {"content": reply.text or None,
                                            "tool_calls": [t.as_dict() for t in reply.tool_calls]}, "coral")
        ui.panel("NOTICE", "Nothing has touched the CI system yet. The model has only asked. "
                 "Your code decides whether this tool runs.", "amber")
        ui.pause("Press Enter to run the tool")

        ui.section("3 · Our code runs the tool", "blue")
        messages.append(reply.message)
        for call in reply.tool_calls:
            ui.act(call.name, call.arguments)
            result = box.run(call.name, call.arguments)
            ui.json_block("tool result", result, "blue")
            tool_msg = box.as_message(call, result)
            messages.append(tool_msg)
        ui.pause("Press Enter to send the result back")

        ui.section("4 · We send the result back, and the model answers", "green")
        ui.json_block("the message we append to the conversation",
                      {**tool_msg, "content": "<the tool result above, sent as a JSON string>"}, "blue")
        final = llm.chat(messages, tools=box.schemas())
        for _ in range(2):  # a live model occasionally wants one more look before answering
            if not final.tool_calls:
                break
            messages.append(final.message)
            for call in final.tool_calls:
                ui.act(call.name, call.arguments)
                result = box.run(call.name, call.arguments)
                ui.observe(result)
                messages.append(box.as_message(call, result))
            final = llm.chat(messages, tools=box.schemas())
        if args.raw and llm.last_response is not None:
            ui.json_block("raw response from the provider", llm.last_response, "coral")
        ui.answer(final.text or "(empty answer)")
    except Exception as err:
        cli.fail(ui, err)
    ui.note("The final answer uses only what the tool returned. Ask: where would you put a guardrail in this flow?")
    cli.save(ui, __file__, args.save, "Demo 1.B · Inside a tool call")


if __name__ == "__main__":
    main()
