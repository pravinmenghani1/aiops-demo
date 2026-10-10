"""Terminal output for live demos: coloured trace lines, panels, JSON, tables.

Standard library only. Everything printed is also recorded, so a demo can be
saved as a transcript (.txt and a dark-themed .html) to use as a fallback.
Set NO_COLOR=1 to turn colours off.
"""
from __future__ import annotations

import html
import json
import os
import re
import shutil
import sys
import textwrap
from pathlib import Path

if os.name == "nt":  # enable ANSI colours in Windows consoles
    os.system("")
try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except Exception:
    pass

COLORS = {"teal": 43, "coral": 209, "amber": 178, "blue": 75, "green": 77, "red": 203,
          "purple": 141, "grey": 245, "white": 255, "key": 114, "str": 117, "num": 75}
HEX = {43: "#2ec4b6", 209: "#f78166", 178: "#d29922", 75: "#58a6ff", 77: "#3fb950", 203: "#f85149",
       141: "#a371f7", 245: "#8b949e", 255: "#e6edf3", 114: "#7ee787", 117: "#a5d6ff"}
ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
LABELS = {"THINK": "teal", "ACT": "coral", "OBSERVE": "blue", "ANSWER": "green", "BLOCKED": "red",
          "WARNING": "amber", "APPROVAL": "amber", "HUMAN": "amber"}


def strip(s: str) -> str:
    return ANSI_RE.sub("", s)


def vlen(s: str) -> int:
    return len(strip(s))


def c(text: str, color: str | None = None, bold: bool = False, dim: bool = False, italic: bool = False) -> str:
    codes = []
    if bold:
        codes.append("1")
    if dim:
        codes.append("2")
    if italic:
        codes.append("3")
    if color:
        codes.append(f"38;5;{COLORS[color]}")
    return f"\x1b[{';'.join(codes)}m{text}\x1b[0m" if codes else text


_JSON_TOKEN = re.compile(r'("(?:[^"\\]|\\.)*"?)(\s*:)?|\b(true|false|null)\b|(-?\b\d+(?:\.\d+)?\b)')


def highlight_json_line(line: str) -> str:
    def repl(m: re.Match) -> str:
        if m.group(1):
            return c(m.group(1), "key") + m.group(2) if m.group(2) else c(m.group(1), "str")
        if m.group(3):
            return c(m.group(3), "coral")
        return c(m.group(4), "num")

    return _JSON_TOKEN.sub(repl, line)


def highlight_json(obj) -> str:
    return "\n".join(highlight_json_line(ln) for ln in json.dumps(obj, indent=2, ensure_ascii=False).splitlines())


class UI:
    def __init__(self, interactive: bool = True, approval: bool | None = None):
        self.interactive = interactive and sys.stdin.isatty()
        self.preset_approval = approval  # None = ask the human; True/False = scripted answer
        self.color = sys.stdout.isatty() and not os.environ.get("NO_COLOR")
        self.width = max(72, min(shutil.get_terminal_size((100, 30)).columns - 2, 104))
        self.lines: list[str] = []

    # ------------------------------------------------------------- output core
    def out(self, s: str = "") -> None:
        self.lines.append(s)
        print(s if self.color else strip(s), flush=True)

    def blank(self) -> None:
        self.out("")

    def wrap(self, text: str, width: int) -> list[str]:
        lines: list[str] = []
        for para in str(text).split("\n"):
            lines.extend(textwrap.wrap(para, width) or [""])
        return lines

    # ------------------------------------------------------------- building blocks
    def header(self, title: str, subtitle: str = "") -> None:
        w = self.width
        self.blank()
        self.out(c("━" * w, "teal"))
        self.out(c(f"  {title}", "white", bold=True))
        if subtitle:
            for line in self.wrap(subtitle, w - 4):
                self.out(c(f"  {line}", "grey"))
        self.out(c("━" * w, "teal"))

    def banner(self, describe: str) -> None:
        mock = describe.startswith("MOCK")
        self.out(c("  LLM  ", "amber" if mock else "teal", bold=True) + c(describe, "amber" if mock else "grey"))

    def section(self, title: str, color: str = "teal") -> None:
        self.blank()
        self.out(c(f"── {title} ", color, bold=True) + c("─" * max(4, self.width - vlen(title) - 4), color))

    def panel(self, title: str, body: str, color: str = "teal", body_is_ansi: bool = False) -> None:
        w = self.width
        inner = w - 4
        top = c("╭─ ", color) + c(title, color, bold=True) + c(" " + "─" * max(1, w - vlen(title) - 5) + "╮", color)
        self.out(top)
        lines = body.split("\n") if body_is_ansi else self.wrap(body, inner)
        for line in lines:
            pad = max(0, inner - vlen(line))
            self.out(c("│ ", color) + line + " " * pad + c(" │", color))
        self.out(c("╰" + "─" * (w - 2) + "╯", color))

    def labelled(self, label: str, text: str, color: str | None = None, text_color: str | None = None) -> None:
        color = color or LABELS.get(label, "grey")
        indent = 10
        lines = self.wrap(text, self.width - indent)
        for i, line in enumerate(lines):
            lab = c(f"{label:<9}", color, bold=True) if i == 0 else " " * 9
            self.out(f"{lab} {c(line, text_color) if text_color else line}")

    def note(self, text: str) -> None:
        for line in self.wrap(text, self.width - 2):
            self.out(c(f"  {line}", "grey", italic=True))

    def json_block(self, title: str, obj, color: str = "blue") -> None:
        inner = self.width - 4
        out: list[str] = []
        for line in json.dumps(obj, indent=2, ensure_ascii=False).splitlines():
            if len(line) <= inner:
                out.append(highlight_json_line(line))
                continue
            indent = " " * (len(line) - len(line.lstrip()) + 2)
            chunks = textwrap.wrap(line, inner, subsequent_indent=indent, break_on_hyphens=False)
            out.append(highlight_json_line(chunks[0]))
            out.extend(indent + c(ch[len(indent):], "str") for ch in chunks[1:])
        self.panel(title, "\n".join(out), color, body_is_ansi=True)

    def table(self, headers: list[str], rows: list[list[str]], color: str = "teal") -> None:
        widths = [max(vlen(h), *(vlen(r[i]) for r in rows)) + 2 for i, h in enumerate(headers)]
        sep = c("  " + "─" * (sum(widths) + len(widths) - 1), color)
        self.out(sep)
        self.out("  " + " ".join(c(h.ljust(widths[i]), color, bold=True) for i, h in enumerate(headers)))
        self.out(sep)
        for r in rows:
            self.out("  " + " ".join(cell + " " * (widths[i] - vlen(cell)) for i, cell in enumerate(r)))
        self.out(sep)

    # ------------------------------------------------------------- agent trace
    def think(self, text: str) -> None:
        self.labelled("THINK", text)

    def act(self, name: str, args: dict) -> None:
        shown = ", ".join(f"{k}={json.dumps(v, ensure_ascii=False)}" for k, v in (args or {}).items())
        self.labelled("ACT", f"{name}({shown})", text_color="coral")

    def observe(self, result, log_lines: int = 12) -> None:
        """Show a tool result compactly: scalars on wrapped lines, long text (logs) as its last lines."""
        width = self.width - 11
        lines: list[str] = []

        def add_obj(obj, prefix: str = "") -> None:
            if isinstance(obj, dict):
                def is_rows(v) -> bool:
                    return isinstance(v, list) and len(v) > 1 and all(isinstance(x, dict) for x in v)

                flat = {k: v for k, v in obj.items()
                        if not (isinstance(v, str) and "\n" in v) and not isinstance(v, dict) and not is_rows(v)}
                if flat:
                    lines.extend(textwrap.wrap(prefix + json.dumps(flat, ensure_ascii=False), width,
                                               subsequent_indent=" " * len(prefix)) or [prefix])
                for k, v in obj.items():
                    if isinstance(v, dict):
                        add_obj(v, f"{k}: ")
                    elif is_rows(v):
                        lines.append(f"{k}: ({len(v)} items)")
                        for row in v:
                            lines.extend(textwrap.wrap(json.dumps(row, ensure_ascii=False), width - 2,
                                                       initial_indent="  ", subsequent_indent="      "))
                    elif isinstance(v, str) and "\n" in v:
                        body = v.splitlines()
                        hidden = len(body) - log_lines
                        lines.append(f"{k}: ({len(body)} lines" + (f", showing the last {log_lines})" if hidden > 0 else ")"))
                        for ln in body[-log_lines:]:
                            ln = re.sub(r"^\d{4}-\d\d-\d\d (\d\d:\d\d:\d\d)( |$)", r"\1 ", ln)
                            ln = re.sub(r"([=_])\1{5,}", r"\1\1\1", ln)
                            lines.extend(textwrap.wrap(ln, width - 2, initial_indent="  ", subsequent_indent="      ")
                                         or ["  "])
            else:
                text = obj if isinstance(obj, str) else json.dumps(obj, ensure_ascii=False)
                for para in text.splitlines() or [""]:
                    lines.extend(textwrap.wrap(prefix + para, width) or [""])

        add_obj(result)
        for i, line in enumerate(lines):
            lab = c("OBSERVE  ", "blue", bold=True) if i == 0 else " " * 9
            self.out(f"{lab} {c(line, 'grey')}")

    def answer(self, text: str, title: str = "ANSWER") -> None:
        self.blank()
        self.panel(title, text, "green")

    def blocked(self, text: str) -> None:
        self.labelled("BLOCKED", text, text_color="red")

    def warn(self, text: str) -> None:
        self.labelled("WARNING", text, text_color="amber")

    # ------------------------------------------------------------- interaction
    def pause(self, msg: str = "Press Enter to continue") -> None:
        if not self.interactive:
            return
        try:
            input(c(f"\n  {msg} …", "grey", italic=True) if self.color else f"\n  {msg} …")
        except EOFError:
            pass

    def ask_approval(self, name: str, args: dict, why: str = "") -> bool:
        shown = ", ".join(f"{k}={json.dumps(v)}" for k, v in (args or {}).items())
        body = f"The agent wants to run:  {name}({shown})"
        if why:
            body += f"\n{why}"
        self.blank()
        self.panel("APPROVAL NEEDED", body, "amber")
        prompt = c("  Approve? (yes/no): ", "amber", bold=True)
        if self.preset_approval is not None:
            answer = "yes" if self.preset_approval else "no"
            self.out(prompt + answer)
        else:
            try:
                answer = input(prompt if self.color else strip(prompt)).strip().lower()
            except EOFError:
                answer = "no"
            self.lines.append(prompt + answer)
        ok = answer in ("y", "yes")
        self.labelled("HUMAN", "approved" if ok else "declined", text_color="green" if ok else "red")
        return ok

    # ------------------------------------------------------------- transcripts
    def save(self, path_base: str | Path, title: str = "Demo transcript") -> list[Path]:
        base = Path(path_base)
        base.parent.mkdir(parents=True, exist_ok=True)
        text = "\n".join(self.lines)
        txt, htm = base.with_suffix(".txt"), base.with_suffix(".html")
        txt.write_text(strip(text) + "\n", encoding="utf-8")
        htm.write_text(_html_page(text, title), encoding="utf-8")
        return [txt, htm]


def _ansi_to_html(text: str) -> str:
    out, state, open_span = [], {"fg": None, "b": False, "d": False, "i": False}, False
    for tok in re.split(r"(\x1b\[[0-9;]*m)", text):
        if not tok.startswith("\x1b["):
            out.append(html.escape(tok))
            continue
        codes = [x for x in tok[2:-1].split(";") if x]
        if not codes or codes == ["0"]:
            state = {"fg": None, "b": False, "d": False, "i": False}
        i = 0
        while i < len(codes):
            code = codes[i]
            if code == "1":
                state["b"] = True
            elif code == "2":
                state["d"] = True
            elif code == "3":
                state["i"] = True
            elif code == "38" and i + 2 < len(codes) and codes[i + 1] == "5":
                state["fg"] = HEX.get(int(codes[i + 2]), "#e6edf3")
                i += 2
            i += 1
        if open_span:
            out.append("</span>")
            open_span = False
        style = []
        if state["fg"]:
            style.append(f"color:{state['fg']}")
        if state["b"]:
            style.append("font-weight:700")
        if state["d"]:
            style.append("opacity:.7")
        if state["i"]:
            style.append("font-style:italic")
        if style:
            out.append(f'<span style="{";".join(style)}">')
            open_span = True
    if open_span:
        out.append("</span>")
    return "".join(out)


def _html_page(text: str, title: str) -> str:
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>
<style>
body{{margin:0;background:#0d1117;color:#e6edf3}}
main{{max-width:1000px;margin:0 auto;padding:24px 16px}}
h1{{font:600 18px/1.4 -apple-system,Segoe UI,Calibri,sans-serif;color:#8b949e;margin:0 0 12px}}
pre{{font:13px/1.22 Menlo,Consolas,"DejaVu Sans Mono",monospace;white-space:pre;overflow-x:auto;margin:0}}
</style></head><body><main><h1>{html.escape(title)}</h1><pre>{_ansi_to_html(text)}</pre></main></body></html>
"""
