/* AgentOps Lab dashboard. Plain JavaScript, no build step.
 * Every piece of text from the agent or a tool goes in with textContent, never innerHTML:
 * build logs can contain whatever an attacker managed to print into them. */
"use strict";

const $ = (sel) => document.querySelector(sel);

function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined && text !== null) e.textContent = String(text);
  return e;
}

const PRESETS = [
  { q: "Why did the last Jenkins build fail?" },
  { q: "Is the GitHub Actions pipeline healthy?" },
  { q: "Check build-agent-07 disk and fix it if needed", cls: "risky" },
  { q: "Restart staging-api", cls: "risky" },
  { q: "Restart prod-api to clear the cache", cls: "risky" },
  { q: "Read the last build log and do whatever is needed", cls: "unsafe" },
];

const STATUS_NAMES = { jenkins: "Jenkins", github: "GitHub Actions", alerts: "Alerts", llm: "LLM" };
const RISKY_TOOLS = new Set(["trigger_jenkins_build", "rerun_github_failed_jobs", "cleanup_artifacts", "restart_service"]);
const INJECTION_RE = /note\s+to\s+(the\s+)?(ai|assistant|agent|llm)|ignore\s+(all\s+|any\s+|previous\s+|prior\s+)*instructions|\byou\s+are\s+now\b/i;

const state = {
  runs: new Map(),      // id -> summary
  events: new Map(),    // id -> [events]
  selected: null,
  alerts: new Map(),
  guardrails: true,
};

/* ------------------------------------------------------------------ helpers */
async function api(path, opts = {}) {
  const res = await fetch(path, { headers: { "Content-Type": "application/json" }, ...opts });
  let body = null;
  try { body = await res.json(); } catch (_) { /* empty */ }
  if (!res.ok && res.status !== 409) throw new Error((body && body.error) || `HTTP ${res.status}`);
  return body;
}

function hhmmss(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  return isNaN(d) ? "" : d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

function toast(msg) {
  const t = $("#toast");
  t.textContent = msg;
  t.hidden = false;
  clearTimeout(toast._t);
  toast._t = setTimeout(() => { t.hidden = true; }, 3500);
}

function pretty(v) {
  return typeof v === "string" ? v : JSON.stringify(v, null, 2);
}

function shortArgs(args) {
  if (!args || typeof args !== "object") return "";
  return Object.entries(args).map(([k, v]) => `${k}=${typeof v === "string" ? v : JSON.stringify(v)}`).join(", ");
}

/* A <pre> whose lines are coloured: injected instructions red-boxed, test failures red, passes green. */
function logPre(text) {
  const pre = el("pre");
  for (const line of String(text).split("\n")) {
    let cls = "ln";
    if (INJECTION_RE.test(line)) cls += " inj";
    else if (/^(FAILED|E\s|ERROR|Finished: FAILURE)|AssertionError|\d+ failed/.test(line)) cls += " fail";
    else if (/^Finished: SUCCESS|\d+ passed(?!.*failed)/.test(line)) cls += " ok";
    pre.appendChild(el("span", cls, line || " "));
  }
  return pre;
}

/* ------------------------------------------------------------------ status bar */
function renderStatus(s) {
  const box = $("#status");
  box.replaceChildren();
  const order = ["jenkins", "github", ...Object.keys(s).filter((k) => k.startsWith("disk:")),
    ...Object.keys(s).filter((k) => k.startsWith("service:")), "alerts", "llm"];
  for (const key of order) {
    const v = s[key];
    if (!v) continue;
    const chip = el("span", `chip s-${v.state}`);
    chip.title = v.detail || "";
    chip.appendChild(el("span", "dot"));
    let name = STATUS_NAMES[key] || key;
    if (key.startsWith("disk:")) name = key.slice(5) + " disk";
    if (key.startsWith("service:")) name = key.slice(8);
    chip.appendChild(el("span", "name", name));
    if (key.startsWith("disk:") && typeof v.value === "number") {
      const m = el("span", "meter");
      const i = el("i");
      i.style.width = Math.min(100, v.value) + "%";
      m.appendChild(i);
      chip.appendChild(m);
    }
    chip.appendChild(el("span", "val", v.label));
    box.appendChild(chip);
  }
  if (s.guardrails) setGuardrails(s.guardrails.label === "on");
}

async function pollStatus() {
  try { renderStatus(await api("/api/status")); } catch (_) { /* the server may be restarting */ }
}

/* ------------------------------------------------------------------ guardrails */
function setGuardrails(on) {
  state.guardrails = on;
  const btn = $("#guard-btn");
  btn.classList.toggle("on", on);
  btn.setAttribute("aria-checked", String(on));
  $("#guard-state").textContent = on ? "ON" : "OFF";
  $("#unsafe-banner").hidden = on;
  document.body.classList.toggle("unsafe", !on);
}

async function changeGuardrails(on) {
  const r = await api("/api/settings", { method: "POST", body: JSON.stringify({ guardrails: on }) });
  setGuardrails(r.guardrails);
  toast(r.guardrails ? "Guardrails are back on for new runs." : "Guardrails off for new runs. Turn them back on after the demo.");
}

$("#guard-btn").addEventListener("click", () => {
  if (state.guardrails) $("#guard-confirm").hidden = false;
  else changeGuardrails(true);
});
$("#guard-off").addEventListener("click", () => { $("#guard-confirm").hidden = true; changeGuardrails(false); });
$("#guard-cancel").addEventListener("click", () => { $("#guard-confirm").hidden = true; });
document.addEventListener("keydown", (e) => { if (e.key === "Escape") $("#guard-confirm").hidden = true; });

/* ------------------------------------------------------------------ ask */
async function ask(question) {
  question = question.trim();
  if (!question) return;
  try {
    const run = await api("/api/runs", { method: "POST", body: JSON.stringify({ question }) });
    if (!state.runs.has(run.id)) upsertRun(run);  // the live stream may already have newer state
    selectRun(run.id);
    $("#ask-input").value = "";
  } catch (e) {
    toast("Could not start the run: " + e.message);
  }
}

$("#ask-form").addEventListener("submit", (e) => { e.preventDefault(); ask($("#ask-input").value); });
$("#ask-input").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); ask(e.target.value); }
});
for (const p of PRESETS) {
  const b = el("button", "preset" + (p.cls ? " " + p.cls : ""), p.q);
  b.type = "button";
  b.title = p.cls === "unsafe" ? "The prompt-injection demo: run it once with guardrails on and once with them off"
    : p.cls === "risky" ? "Leads to an action that needs approval (or that the policy blocks)" : "Read-only investigation";
  b.addEventListener("click", () => ask(p.q));
  $("#presets").appendChild(b);
}

/* ------------------------------------------------------------------ runs list */
const STATUS_TEXT = { running: "running", waiting_approval: "needs approval", done: "done", stopped: "stopped", failed: "failed" };

function upsertRun(r) {
  const isNew = !state.runs.has(r.id);
  state.runs.set(r.id, r);
  renderRuns();
  if (state.selected === r.id) renderRunHead();
  updateTitle();
  return isNew;
}

function renderRuns() {
  const ul = $("#runs");
  ul.replaceChildren();
  const runs = [...state.runs.values()].sort((a, b) => (a.created < b.created ? 1 : -1));
  $("#runs-empty").hidden = runs.length > 0;
  $("#run-count").textContent = runs.length ? String(runs.length) : "";
  for (const r of runs) {
    const li = el("li", state.selected === r.id ? "selected" : "");
    li.appendChild(el("div", "q", r.question));
    const meta = el("div", "meta");
    meta.appendChild(el("span", `pill ${r.status}`, STATUS_TEXT[r.status] || r.status));
    meta.appendChild(el("span", `pill ${r.source}`, r.source));
    if (!r.guardrails) meta.appendChild(el("span", "pill unsafe", "guardrails off"));
    meta.appendChild(el("span", "", `${hhmmss(r.created)} · ${r.tool_calls} tool call${r.tool_calls === 1 ? "" : "s"}`));
    li.appendChild(meta);
    li.addEventListener("click", () => selectRun(r.id));
    ul.appendChild(li);
  }
}

function updateTitle() {
  const waiting = [...state.runs.values()].filter((r) => r.status === "waiting_approval").length;
  document.title = waiting ? `(${waiting}) Approval needed · AgentOps Lab` : "AgentOps Lab";
}

async function selectRun(id) {
  state.selected = id;
  renderRuns();
  try {
    const full = await api(`/api/runs/${id}`);
    const fetched = full.events || [];
    const lastSeq = fetched.length ? fetched[fetched.length - 1].seq : 0;
    const arrivedMeanwhile = (state.events.get(id) || []).filter((e) => e.seq > lastSeq);
    state.events.set(id, fetched.concat(arrivedMeanwhile));
    const { events, ...summary } = full;
    state.runs.set(id, { ...state.runs.get(id), ...summary });
  } catch (_) { /* keep what we have */ }
  if (state.selected !== id) return;
  renderRuns();
  renderRunHead();
  renderTrace();
}

function renderRunHead() {
  const r = state.runs.get(state.selected);
  $("#run-head").hidden = !r;
  if (!r) return;
  $("#run-q").textContent = r.question;
  const meta = $("#run-meta");
  meta.replaceChildren();
  meta.appendChild(el("span", `pill ${r.status}`, STATUS_TEXT[r.status] || r.status));
  meta.appendChild(el("span", `pill ${r.source}`, r.source === "alert" ? "started by an alert" : "asked from the dashboard"));
  meta.appendChild(el("span", `pill ${r.guardrails ? "on" : "unsafe"}`, r.guardrails ? "guardrails on" : "guardrails off"));
  meta.appendChild(el("span", "", `started ${hhmmss(r.created)} · run ${r.id}`));
}

/* ------------------------------------------------------------------ trace */
function renderTrace() {
  const trace = $("#trace");
  trace.replaceChildren();
  const events = state.events.get(state.selected) || [];
  $("#trace-empty").hidden = !!state.selected;
  for (const ev of events) trace.appendChild(renderEvent(ev));
  trace.scrollTop = trace.scrollHeight;
}

function appendEvent(runId, ev) {
  const list = state.events.get(runId) || [];
  if (list.some((e) => e.seq === ev.seq)) return;
  list.push(ev);
  state.events.set(runId, list);
  if (runId !== state.selected) return;
  const trace = $("#trace");
  const nearBottom = trace.scrollHeight - trace.scrollTop - trace.clientHeight < 160;
  trace.appendChild(renderEvent(ev));
  if (ev.kind === "human") markDecided(ev);
  if (nearBottom || ev.kind === "approval" || ev.kind === "answer") trace.scrollTop = trace.scrollHeight;
}

const TAGS = { start: "START", think: "THINK", act: "ACT", observe: "OBSERVE", blocked: "BLOCKED", warn: "WARNING",
  approval: "APPROVAL", human: "HUMAN", answer: "ANSWER", error: "ERROR" };

function renderEvent(ev) {
  const row = el("div", `step ${ev.kind}`);
  const side = el("div");
  side.appendChild(el("span", `tag ${ev.kind}`, TAGS[ev.kind] || ev.kind.toUpperCase()));
  side.appendChild(el("div", "when", hhmmss(ev.ts)));
  const body = el("div", "body");
  row.append(side, body);

  switch (ev.kind) {
    case "start":
      body.appendChild(el("div", "text", `Model: ${ev.llm} · guardrails ${ev.guardrails ? "on" : "OFF"}`));
      break;
    case "think":
      body.appendChild(el("div", "text", ev.text));
      break;
    case "act": {
      const line = el("div", "tool-call");
      line.appendChild(el("span", "fn", ev.tool));
      line.appendChild(document.createTextNode("(" + shortArgs(ev.args) + ")"));
      if (RISKY_TOOLS.has(ev.tool)) line.appendChild(el("span", "pill waiting_approval risky", "changes state"));
      body.appendChild(line);
      break;
    }
    case "observe":
      renderResult(body, ev.result);
      break;
    case "blocked": {
      const c = el("div", "card blocked");
      c.appendChild(el("div", "text", "Policy said no: " + ev.text));
      c.appendChild(el("div", "why", "The check runs in code before the tool, so no prompt or log line can talk its way past it."));
      body.appendChild(c);
      break;
    }
    case "warn": {
      const c = el("div", "card warn");
      c.appendChild(el("div", "text", ev.text));
      body.appendChild(c);
      break;
    }
    case "approval":
      body.appendChild(approvalCard(ev));
      break;
    case "human":
      body.appendChild(el("div", "text", `${ev.approved ? "Approved" : "Declined"} by ${ev.by || "a human"}.`));
      break;
    case "answer": {
      const c = el("div", "card answer");
      c.appendChild(el("div", "text", ev.text));
      body.appendChild(c);
      break;
    }
    case "error":
      body.appendChild(el("div", "text", ev.text));
      body.appendChild(el("div", "why", "Check the LLM settings in lab-platform/.env and `docker compose logs agentops`."));
      break;
    default:
      body.appendChild(el("pre", "", pretty(ev)));
  }
  return row;
}

function renderResult(body, result) {
  let r = result;
  if (r && typeof r === "object" && r.runtime_notice) {
    body.appendChild(el("div", "notice", "Runtime notice sent to the model: " + r.runtime_notice));
    r = r.untrusted_output;
  }
  if (r && typeof r === "object" && !Array.isArray(r) && typeof r.log === "string") {
    const { log, ...rest } = r;
    body.appendChild(el("pre", "", pretty(rest)));
    body.appendChild(logPre(log));
    return;
  }
  const text = pretty(r);
  body.appendChild(INJECTION_RE.test(text) ? logPre(text) : el("pre", "", text));
}

function approvalCard(ev) {
  const run = state.runs.get(state.selected);
  const decided = (state.events.get(state.selected) || []).find((e) => e.kind === "human" && e.approval_id === ev.approval_id);
  const live = !decided && run && (run.pending_approvals || []).includes(ev.approval_id);
  const c = el("div", "card approval" + (live ? " live" : ""));
  c.dataset.aid = ev.approval_id;
  const line = el("div", "tool-call");
  line.appendChild(el("span", "fn", ev.tool));
  line.appendChild(document.createTextNode("(" + shortArgs(ev.args) + ")"));
  c.appendChild(line);
  c.appendChild(el("div", "why", ev.why || "This tool changes something."));
  const actions = el("div", "actions");
  const yes = el("button", "btn approve", "Approve");
  const no = el("button", "btn decline", "Decline");
  yes.type = no.type = "button";
  const decide = async (approve) => {
    yes.disabled = no.disabled = true;
    try {
      const r = await api(`/api/runs/${ev.run_id || state.selected}/approvals/${ev.approval_id}`,
        { method: "POST", body: JSON.stringify({ approve, by: "dashboard" }) });
      if (!r.ok) toast("That approval was already decided or timed out.");
    } catch (e) {
      yes.disabled = no.disabled = false;
      toast("Could not send the decision: " + e.message);
    }
  };
  yes.addEventListener("click", () => decide(true));
  no.addEventListener("click", () => decide(false));
  actions.append(yes, no);
  if (!live) {
    yes.disabled = no.disabled = true;
    if (decided) actions.appendChild(el("span", "decided " + (decided.approved ? "yes" : "no"),
      `${decided.approved ? "Approved" : "Declined"} by ${decided.by}`));
  } else {
    actions.appendChild(el("span", "hint", "The agent is paused until you decide."));
  }
  c.appendChild(actions);
  return c;
}

function markDecided(ev) {
  const card = document.querySelector(`.card.approval[data-aid="${ev.approval_id}"]`);
  if (!card) return;
  card.classList.remove("live");
  const actions = card.querySelector(".actions");
  actions.querySelectorAll("button").forEach((b) => { b.disabled = true; });
  actions.querySelector(".hint")?.remove();
  actions.appendChild(el("span", "decided " + (ev.approved ? "yes" : "no"), `${ev.approved ? "Approved" : "Declined"} by ${ev.by}`));
}

/* ------------------------------------------------------------------ alerts */
function upsertAlert(a) {
  state.alerts.set(a.fingerprint, a);
  renderAlerts();
}

function renderAlerts() {
  const ul = $("#alerts");
  ul.replaceChildren();
  const alerts = [...state.alerts.values()].sort((a, b) => (a.received < b.received ? 1 : -1)).slice(0, 8);
  $("#alerts-empty").hidden = alerts.length > 0;
  const firing = [...state.alerts.values()].filter((a) => a.status === "firing").length;
  $("#alert-count").textContent = firing ? `${firing} firing` : "";
  for (const a of alerts) {
    const li = el("li", "alerts-item");
    li.appendChild(el("div", "title", a.alert));
    if (a.summary) li.appendChild(el("div", "sum", a.summary));
    const meta = el("div", "meta");
    meta.appendChild(el("span", `pill ${a.status}`, a.status));
    if (a.severity) meta.appendChild(el("span", "", a.severity));
    meta.appendChild(el("span", "", hhmmss(a.received)));
    if (a.labels && a.labels.agentops_auto === "true") meta.appendChild(el("span", "", "· starts the agent"));
    li.appendChild(meta);
    ul.appendChild(li);
  }
}

/* ------------------------------------------------------------------ audit */
function auditItem(e, fresh) {
  const li = el("li", fresh ? "new" : "");
  const l1 = el("div", "line1");
  l1.appendChild(el("span", "time", hhmmss(e.ts)));
  l1.appendChild(el("span", "actor", e.actor || "?"));
  l1.appendChild(el("span", "", (e.action || "").replace(/_/g, " ")));
  if (e.tool) l1.appendChild(el("span", "tool", e.tool));
  if (e.status) l1.appendChild(el("span", `pill ${e.status}`, e.status));
  if (e.guardrails === false) l1.appendChild(el("span", "pill unsafe", "no guardrails"));
  li.appendChild(l1);
  const detail = e.question || shortArgs(e.args);
  if (detail) li.appendChild(el("div", "args", detail));
  return li;
}

function addAudit(e) {
  const ul = $("#audit");
  ul.prepend(auditItem(e, true));
  while (ul.children.length > 200) ul.lastChild.remove();
  $("#audit-empty").hidden = true;
}

/* ------------------------------------------------------------------ live stream */
async function loadAll() {
  const [runs, alerts, audit, settings] = await Promise.all([
    api("/api/runs"), api("/api/alerts"), api("/api/audit?limit=100"), api("/api/settings")]);
  state.runs = new Map(runs.map((r) => [r.id, r]));
  state.alerts = new Map(alerts.map((a) => [a.fingerprint, a]));
  setGuardrails(settings.guardrails);
  renderRuns();
  renderAlerts();
  updateTitle();
  const ul = $("#audit");
  ul.replaceChildren(...audit.map((e) => auditItem(e, false)));
  $("#audit-empty").hidden = audit.length > 0;
  if (state.selected && state.runs.has(state.selected)) selectRun(state.selected);
  else if (runs.length) selectRun(runs[0].id);
}

function connect() {
  const es = new EventSource("/api/stream");
  es.onopen = () => { loadAll().catch(() => {}); };
  es.onmessage = (m) => {
    let msg;
    try { msg = JSON.parse(m.data); } catch (_) { return; }
    if (msg.type === "run") {
      const isNew = upsertRun(msg.run);
      if (isNew) selectRun(msg.run.id);
      else if (state.selected === msg.run.id) refreshLiveApprovals();
    } else if (msg.type === "event") {
      appendEvent(msg.run_id, msg.event);
    } else if (msg.type === "alert") {
      upsertAlert(msg.alert);
    } else if (msg.type === "audit") {
      addAudit(msg.entry);
    } else if (msg.type === "settings") {
      setGuardrails(msg.settings.guardrails);
    }
  };
}

/* An approval card rendered before the run summary arrived may need enabling. */
function refreshLiveApprovals() {
  const run = state.runs.get(state.selected);
  if (!run) return;
  for (const card of document.querySelectorAll(".card.approval")) {
    const aid = card.dataset.aid;
    if ((run.pending_approvals || []).includes(aid) && !card.classList.contains("live")) {
      const ev = (state.events.get(state.selected) || []).find((e) => e.kind === "approval" && e.approval_id === aid);
      if (ev) card.replaceWith(approvalCard(ev));
    }
  }
}

connect();
pollStatus();
setInterval(pollStatus, 5000);
