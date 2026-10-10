"""Shared command-line options for demos, so every demo behaves the same way at the front of the room."""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


def parser(description: str) -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=description)
    p.add_argument("--mock", action="store_true",
                   help="use scripted responses instead of a real LLM (offline fallback)")
    p.add_argument("--no-pause", action="store_true", help="run straight through without 'Press Enter' pauses")
    p.add_argument("--save", metavar="NAME",
                   help="also save a transcript to demos/<module>/recorded/NAME.txt and .html")
    return p


def apply(args) -> None:
    if args.mock:
        os.environ["AGENT_MODE"] = "mock"


def fail(ui, err: Exception) -> None:
    ui.blank()
    ui.panel("THE LIVE CALL FAILED", f"{type(err).__name__}: {err}\n\nTo carry on, re-run this demo with --mock "
             "(scripted responses), or open the recorded transcript in the recorded/ folder.", "red")
    sys.exit(1)


def save(ui, demo_file: str, name: str | None, title: str) -> None:
    if not name:
        return
    paths = ui.save(Path(demo_file).resolve().parent / "recorded" / name, title)
    ui.note("Saved transcript: " + ", ".join(str(p) for p in paths))
