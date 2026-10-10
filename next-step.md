# Next steps: making the demo more interesting

Ideas to pick up later, ranked by impact for the effort. All of them build on what is already in the kit.

**Top three to start with:** #1 (the audience attacks the agent), #3 (a visible blast radius) and #5 (the agent proposes a fix). Together they give a crowd game, a vivid "why this matters" moment and a "what's next" ending.

## Quick wins (under a day each)

- [ ] **1. Let the audience attack the agent.**
  Add a page where anyone in the room can submit a "commit message" or "log line" that the agent reads live. People try to trick it into restarting `prod-api`. With guardrails on, the dashboard shows every attempt blocked. Then turn guardrails off and replay the best attempt.
  *Touches:* `lab-platform/agentops/app.py`, `agentops/static/`, the injection scenario in `labctl.py`.

- [ ] **2. Cost and time counter in the dashboard header.**
  Show tokens used, cost in € and time to diagnosis for each run, next to a "human estimate" (e.g. 20 minutes for an on-call engineer to find `assert 37 == 38`).
  *Touches:* `agentkit/llm.py` (token usage), `agentops/runs.py`, `agentops/static/app.js`.

- [ ] **3. Make the blast radius visible.**
  When `prod-api` restarts in the guardrails-off run, show a live graph of the sessions dropping from 1,240 to 0 (Prometheus already scrapes it).
  *Touches:* `services/svc.py` (session metric), `agentops/static/`.

- [ ] **4. Run the same question through two models side by side.**
  A small local model through Ollama vs. a top-tier model on the build-failure scenario. Shows that the guardrails hold no matter which model runs, and that the cheap model sometimes gets the diagnosis wrong.
  *Touches:* `agentops/app.py`, `agentkit/llm.py`, dashboard layout.

## Bigger additions (1–3 days each)

- [ ] **5. The agent proposes a fix.**
  The agent opens a branch with the fix to `eta.py`, Jenkins goes green, and a human merges it. The agent can only propose, never merge.
  *Touches:* `agentops/toolset.py` (new `propose_fix` tool, plus a policy rule), `jenkins/`, `labctl.py`.

- [ ] **6. A scenario where two causes look alike.**
  A test fails on timing on build-agent-07 because its disk is nearly full. The agent has to connect the Prometheus alert to the Jenkins failure: reasoning across systems, not reading one log.
  *Touches:* `labctl.py` (new scenario), `sample-app/`, `agentops/rehearsal.py` (scripted run for mock mode).

- [ ] **7. Tie the sample app to the audience's own domain.**
  A bad deploy makes Amsterdam → Utrecht ETAs come out 40% too high, an alert fires on the error rate, and the agent traces it to a commit.
  *Touches:* `sample-app/route-eta-service/`, `prometheus/alerts.yml`, `labctl.py`.

- [ ] **8. Replay a run after the fact.**
  A "replay" button in the dashboard that steps through a run from the audit log, step by step. Good for Q&A ("why did it do that?") and doubles as a fallback when the live LLM fails.
  *Touches:* `agentops/runs.py`, `agentops/static/`.

## How to stage it

- **Open with the failure, not the success.** Start with guardrails off and the agent restarting prod, then spend the session showing how each safety layer would have stopped it.
- **Use a countdown.** "The on-call engineer got paged 2 minutes ago. Who finds the cause first: the agent, or you?" Show the log to the audience while the agent works.
- **Keep the recorded fallbacks** (`demos/m1/recorded/`) one click away.
