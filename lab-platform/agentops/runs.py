"""Agent runs: each run is one agentkit agent loop in a thread, streamed live to the dashboard.

Approvals block the run until someone clicks Approve or Decline (or the timeout passes,
which counts as a decline). Every tool call, block and decision goes to the audit log.
"""
from __future__ import annotations

import itertools
import json
import queue
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from agentkit.agent import run_agent

from .clients import now_iso

MAX_RESULT_CHARS = 12000
KEEP_RUNS = 100


class EventBus:
    """Fan-out of live events to every open dashboard (Server-Sent Events)."""

    def __init__(self):
        self._subs: set[queue.Queue] = set()
        self._lock = threading.Lock()

    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=1000)
        with self._lock:
            self._subs.add(q)
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self._lock:
            self._subs.discard(q)

    def publish(self, event: dict) -> None:
        with self._lock:
            subs = list(self._subs)
        for q in subs:
            try:
                q.put_nowait(event)
            except queue.Full:
                pass


class Audit:
    """Append-only JSON-lines record of who did what."""

    def __init__(self, path: Path, bus: EventBus):
        self.path = path
        self.bus = bus
        self._lock = threading.Lock()
        path.parent.mkdir(parents=True, exist_ok=True)

    def write(self, **entry) -> None:
        entry = {"ts": now_iso(), **entry}
        with self._lock, open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        self.bus.publish({"type": "audit", "entry": entry})

    def tail(self, n: int = 50) -> list[dict]:
        if not self.path.exists():
            return []
        lines = self.path.read_text(encoding="utf-8").splitlines()[-n:]
        return [json.loads(ln) for ln in reversed(lines) if ln.strip()]


@dataclass
class Approval:
    id: str
    run_id: str
    tool: str
    args: dict
    created: float = field(default_factory=time.time)
    done: threading.Event = field(default_factory=threading.Event)
    approved: bool | None = None
    by: str = ""


@dataclass
class Run:
    id: str
    question: str
    source: str
    guardrails: bool
    created: str = field(default_factory=now_iso)
    status: str = "running"  # running | waiting_approval | done | stopped | failed
    answer: str = ""
    events: list[dict] = field(default_factory=list)
    approvals: dict[str, Approval] = field(default_factory=dict)
    tool_calls: int = 0
    alert: dict | None = None

    def summary(self) -> dict:
        pending = [a.id for a in self.approvals.values() if a.approved is None]
        return {"id": self.id, "question": self.question, "source": self.source, "guardrails": self.guardrails,
                "created": self.created, "status": self.status, "answer": self.answer, "tool_calls": self.tool_calls,
                "pending_approvals": pending}

    def full(self) -> dict:
        return {**self.summary(), "events": self.events, "alert": self.alert}


def _clip(result):
    text = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False)
    if len(text) <= MAX_RESULT_CHARS:
        return result
    return text[:MAX_RESULT_CHARS] + f"\n… ({len(text) - MAX_RESULT_CHARS} more characters sent to the model)"


class DashboardUI:
    """Implements the UI methods agentkit's run_agent calls, streaming each step to the dashboard."""

    def __init__(self, run: Run, mgr: "RunManager"):
        self.run, self.mgr = run, mgr
        self._seq = itertools.count(1)
        self._call: dict = {}

    def _emit(self, kind: str, **data) -> dict:
        ev = {"seq": next(self._seq), "ts": now_iso(), "kind": kind, **data}
        self.run.events.append(ev)
        self.mgr.bus.publish({"type": "event", "run_id": self.run.id, "event": ev})
        return ev

    # --- the methods run_agent uses
    def think(self, text: str) -> None:
        self._emit("think", text=text)

    def act(self, name: str, args: dict) -> None:
        self.run.tool_calls += 1
        self._call = {"tool": name, "args": args, "status": "ran"}
        self._emit("act", tool=name, args=args)

    def blocked(self, text: str) -> None:
        self._call["status"] = "blocked"
        self._emit("blocked", text=text)

    def warn(self, text: str) -> None:
        self._emit("warn", text=text)

    def observe(self, result) -> None:
        self._emit("observe", result=_clip(result))
        status = self._call.get("status", "ran")
        if status == "ran" and isinstance(result, dict) and "error" in result:
            status = "error"
        self.mgr.audit.write(run=self.run.id, actor="agent", action="tool_call", tool=self._call.get("tool"),
                             args=self._call.get("args"), status=status, guardrails=self.run.guardrails)

    def answer(self, text: str, title: str = "ANSWER") -> None:
        self.run.answer = text
        self._emit("answer", text=text)

    def ask_approval(self, name: str, args: dict, why: str = "") -> bool:
        ap = Approval(id=uuid.uuid4().hex[:8], run_id=self.run.id, tool=name, args=args)
        self.run.approvals[ap.id] = ap
        self.run.status = "waiting_approval"
        self.mgr.publish_run(self.run)
        self._emit("approval", approval_id=ap.id, tool=name, args=args, why=why)
        if not ap.done.wait(self.mgr.settings.approval_timeout_s):
            ap.approved, ap.by = False, "timeout"
        self._call["status"] = "approved" if ap.approved else "declined"
        self.run.status = "running"
        self.mgr.publish_run(self.run)
        self._emit("human", approval_id=ap.id, approved=bool(ap.approved), by=ap.by)
        self.mgr.audit.write(run=self.run.id, actor=ap.by or "human", action="approval", tool=name, args=args,
                             status="approved" if ap.approved else "declined")
        return bool(ap.approved)

    # tolerate any other UI call agentkit may add later
    def __getattr__(self, name: str) -> Callable:
        return lambda *a, **k: None


class RunManager:
    def __init__(self, settings, bus: EventBus, audit: Audit, llm_factory: Callable, toolbox_factory: Callable,
                 policy_factory: Callable, prompt_factory: Callable):
        self.settings = settings
        self.bus, self.audit = bus, audit
        self.llm_factory, self.toolbox_factory = llm_factory, toolbox_factory
        self.policy_factory, self.prompt_factory = policy_factory, prompt_factory
        self.guardrails = settings.guardrails_default
        self.runs: dict[str, Run] = {}
        self.alerts: dict[str, dict] = {}
        self._auto_started: dict[str, float] = {}
        self._lock = threading.Lock()
        self.store = Path(settings.data_dir) / "runs.jsonl"
        self._load()

    # ------------------------------------------------------------------ history (survives restarts)
    def _load(self) -> None:
        if not self.store.exists():
            return
        for line in self.store.read_text(encoding="utf-8").splitlines()[-KEEP_RUNS:]:
            try:
                d = json.loads(line)
                self.runs[d["id"]] = Run(id=d["id"], question=d["question"], source=d["source"],
                                         guardrails=d["guardrails"], created=d["created"], status=d["status"],
                                         answer=d.get("answer", ""), events=d.get("events", []),
                                         tool_calls=d.get("tool_calls", 0), alert=d.get("alert"))
            except (ValueError, KeyError):
                continue

    def _save(self, run: Run) -> None:
        with self._lock:
            with open(self.store, "a", encoding="utf-8") as f:
                f.write(json.dumps(run.full(), ensure_ascii=False) + "\n")
            if self.store.stat().st_size > 8_000_000:  # keep the file small: only the latest runs
                lines = self.store.read_text(encoding="utf-8").splitlines()[-KEEP_RUNS:]
                self.store.write_text("\n".join(lines) + "\n", encoding="utf-8")

    # ------------------------------------------------------------------ runs
    def publish_run(self, run: Run) -> None:
        self.bus.publish({"type": "run", "run": run.summary()})

    def start(self, question: str, source: str = "dashboard", alert: dict | None = None) -> Run:
        run = Run(id=uuid.uuid4().hex[:8], question=question.strip(), source=source, guardrails=self.guardrails,
                  alert=alert)
        with self._lock:
            self.runs[run.id] = run
        self.audit.write(run=run.id, actor=source, action="run_started", question=run.question,
                         guardrails=run.guardrails)
        self.publish_run(run)
        threading.Thread(target=self._execute, args=(run,), daemon=True, name=f"run-{run.id}").start()
        return run

    def _execute(self, run: Run) -> None:
        ui = DashboardUI(run, self)
        try:
            llm = self.llm_factory()
            ui._emit("start", llm=llm.describe(), guardrails=run.guardrails)
            result = run_agent(llm, self.toolbox_factory(), run.question,
                               system=self.prompt_factory(run.guardrails), ui=ui,
                               max_steps=self.settings.max_steps, require_approval=run.guardrails,
                               policy=self.policy_factory() if run.guardrails else None,
                               scan_injection=run.guardrails)
            run.status = "done" if result.stopped_reason == "done" else "stopped"
        except Exception as err:  # surface the failure in the dashboard instead of a silent thread death
            ui._emit("error", text=f"{type(err).__name__}: {err}")
            run.status = "failed"
        self.audit.write(run=run.id, actor="agent", action="run_finished", status=run.status)
        self.publish_run(run)
        try:
            self._save(run)
        except OSError as err:
            print(f"[agentops] could not save run history: {err}", flush=True)

    def decide(self, run_id: str, approval_id: str, approve: bool, by: str = "dashboard") -> bool:
        run = self.runs.get(run_id)
        ap = run.approvals.get(approval_id) if run else None
        if not ap or ap.approved is not None:
            return False
        ap.approved, ap.by = bool(approve), by
        ap.done.set()
        return True

    def list(self) -> list[dict]:
        return [r.summary() for r in sorted(self.runs.values(), key=lambda r: r.created, reverse=True)]

    # ------------------------------------------------------------------ alerts
    def set_guardrails(self, on: bool, by: str = "dashboard") -> None:
        self.guardrails = bool(on)
        self.audit.write(actor=by, action="guardrails", status="on" if on else "off")
        self.bus.publish({"type": "settings", "settings": {"guardrails": self.guardrails}})

    def handle_alertmanager(self, payload: dict) -> list[str]:
        started = []
        for a in payload.get("alerts", []):
            labels = a.get("labels", {})
            fp = a.get("fingerprint") or json.dumps(labels, sort_keys=True)
            entry = {"fingerprint": fp, "status": a.get("status"), "alert": labels.get("alertname"),
                     "severity": labels.get("severity"), "summary": a.get("annotations", {}).get("summary"),
                     "labels": labels, "startsAt": a.get("startsAt"), "endsAt": a.get("endsAt"), "received": now_iso()}
            self.alerts[fp] = entry
            self.bus.publish({"type": "alert", "alert": entry})
            if a.get("status") != "firing" or labels.get("agentops_auto") != "true":
                continue
            last = self._auto_started.get(fp, 0)
            busy = any(r.alert and r.alert.get("fingerprint") == fp and r.status in ("running", "waiting_approval")
                       for r in self.runs.values())
            if busy or time.time() - last < 600:
                continue
            self._auto_started[fp] = time.time()
            question = (f"ALERT [{labels.get('severity', 'warning')}] {labels.get('alertname')} on "
                        f"{labels.get('host', labels.get('instance', '?'))}: {entry['summary']}. "
                        "Investigate and fix it safely.")
            started.append(self.start(question, source="alert", alert=entry).id)
        return started
