# Agentic AI for DevOps & Data Engineering · lab kit

Demos and labs for the 7-module *Agentic AI for Operations Teams* program. This release contains the **Module 1 demos** (1.A–1.D) and `agentkit`, the small helper library every later lab builds on.

## Quick start

```bash
cp .env.example .env          # then fill in ONE provider: OpenAI, Azure OpenAI, Bedrock or Ollama
python check_env.py           # every line should say PASS
python demos/m1/a_three_answers.py
```

On **Windows** (PowerShell or Command Prompt), use `copy .env.example .env` instead of `cp`, and `python` or `py` instead of `python3`. Colours and UTF-8 output work in Windows Terminal and the classic console.

No LLM handy? Add `--mock` to any demo for scripted responses with no network calls.

Module 1 needs **Python 3.11+ and nothing else**. The kit calls OpenAI-compatible APIs over plain HTTPS with the standard library, so locked-down corporate laptops need no extra installs. Amazon Bedrock is the exception: it needs `pip install boto3`.

## What's inside

```text
agentic-ai-devops-labs/
├── check_env.py          environment check (Python, .env, a real tool call to your LLM)
├── .env.example          provider settings: OpenAI, Azure OpenAI, Bedrock, Ollama, Gemini, proxy and CA
├── agentkit/             shared helpers used by every demo and lab
│   ├── llm.py            one client for every provider; same Reply/ToolCall shape everywhere
│   ├── tools.py          ToolBox: turns Python functions into tool definitions
│   ├── agent.py          the think → act → observe loop, with guardrail hooks
│   ├── policy.py         allow-lists and the prompt-injection scanner
│   ├── mock_llm.py       scripted responses for --mock
│   └── ui.py             terminal output and transcript export (.txt and .html)
├── demos/m1/             Module 1 demos, fake systems, data and recorded fallbacks
│   └── README.md         trainer runbook: timings, talking points, fallbacks, troubleshooting
├── lab-platform/         the same demos on real systems with Docker Compose (see its README)
└── tests/test_kit.py     self-test: mock runs, live wire format against a fake endpoint, Bedrock shapes
```

Start with **demos/m1/README.md**.

## Real-system lab

`lab-platform/` runs the same Module 1 stories against real Jenkins, a real SSH build agent, GitHub Actions, Prometheus and Alertmanager on Docker Compose, with a web dashboard for live traces and approvals. See **lab-platform/README.md**.
