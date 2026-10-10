"""Tools: plain Python functions the agent may ask to run.

    box = ToolBox()

    @box.tool(risky=True, params={"host": "Build agent hostname"})
    def cleanup_artifacts(host: str, older_than_days: int = 14) -> dict:
        '''Delete build artifacts older than N days on a build agent.'''

The JSON schema the model sees is built from the function's name, docstring and
type hints. The docstring is the model's only manual for the tool, so write it
for a reader who has never seen your systems.
"""
from __future__ import annotations

import inspect
import json
import types
import typing
from dataclasses import dataclass, field
from typing import Any, Callable

_JSON_TYPES = {str: "string", int: "integer", float: "number", bool: "boolean", dict: "object", list: "array"}


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict
    func: Callable[..., Any]
    risky: bool = False

    def schema(self) -> dict:
        return {"name": self.name, "description": self.description, "parameters": self.parameters}


def _schema_for(func: Callable, params: dict[str, str] | None) -> dict:
    sig = inspect.signature(func)
    hints = typing.get_type_hints(func)
    props, required = {}, []
    for pname, p in sig.parameters.items():
        hint = hints.get(pname, str)
        if typing.get_origin(hint) in (typing.Union, types.UnionType):  # Optional[X] or X | None
            hint = next(a for a in typing.get_args(hint) if a is not type(None))
        hint = typing.get_origin(hint) or hint  # list[str] -> list
        prop: dict = {"type": _JSON_TYPES.get(hint, "string")}
        if params and pname in params:
            prop["description"] = params[pname]
        if p.default is inspect.Parameter.empty:
            required.append(pname)
        elif p.default is not None:
            prop["default"] = p.default
        props[pname] = prop
    schema: dict = {"type": "object", "properties": props}
    if required:
        schema["required"] = required
    return schema


@dataclass
class ToolBox:
    tools: dict[str, Tool] = field(default_factory=dict)

    def tool(self, func: Callable | None = None, *, name: str | None = None, risky: bool = False,
             params: dict[str, str] | None = None, description: str | None = None):
        def register(fn: Callable) -> Callable:
            doc = description or inspect.getdoc(fn) or fn.__name__
            t = Tool(name or fn.__name__, " ".join(doc.split()), _schema_for(fn, params), fn, risky)
            self.tools[t.name] = t
            return fn
        return register(func) if func else register

    def schemas(self) -> list[dict]:
        return [t.schema() for t in self.tools.values()]

    def is_risky(self, name: str) -> bool:
        return name in self.tools and self.tools[name].risky

    def run(self, name: str, arguments: dict) -> Any:
        if name not in self.tools:
            return {"error": f"Unknown tool {name!r}. Available: {sorted(self.tools)}"}
        try:
            return self.tools[name].func(**(arguments or {}))
        except TypeError as e:
            return {"error": f"Bad arguments for {name}: {e}"}
        except Exception as e:  # tools report failures as data the model can reason about
            return {"error": f"{type(e).__name__}: {e}"}

    @staticmethod
    def as_message(call, result: Any) -> dict:
        content = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False)
        return {"role": "tool", "tool_call_id": call.id, "name": call.name, "content": content}


# Module-level default toolbox, so the slide example reads naturally:
#   from agentkit import tools
#   @tools.tool
#   def get_build_status(build_id: int) -> dict: ...
#   tools.schemas(); tools.run(name, args); tools.is_risky(name); tools.as_message(call, result)
default = ToolBox()
tool = default.tool
schemas = default.schemas
run = default.run
is_risky = default.is_risky
as_message = ToolBox.as_message
