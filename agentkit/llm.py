"""One small LLM client for every provider the course supports.

Pick the provider in .env with LLM_PROVIDER:

    openai     OpenAI, or any OpenAI-compatible endpoint (Ollama, Gemini's
               OpenAI-compatible endpoint, vLLM, LiteLLM...) via OPENAI_BASE_URL
    azure      Azure OpenAI (a deployment in your Azure OpenAI resource)
    bedrock    Amazon Bedrock Converse API (needs: pip install boto3)

Set AGENT_MODE=mock (or pass --mock to a demo) to use scripted responses
instead: nothing leaves the laptop.

Every provider is wrapped to the same tiny interface:

    reply = client.chat(messages, tools=[...])
    reply.text          -> the model's words (may be empty when it calls a tool)
    reply.tool_calls    -> list of ToolCall(id, name, arguments)
    reply.message       -> the assistant message to append to the conversation

Messages use one neutral format, converted per provider:
    {"role": "system" | "user", "content": "..."}
    {"role": "assistant", "content": "..." | None, "tool_calls": [{"id", "name", "arguments"}]}
    {"role": "tool", "tool_call_id": "...", "name": "...", "content": "<json string>"}
"""
from __future__ import annotations

import json
import os
import ssl
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any

from .config import ConfigError, agent_mode, env, require


# --------------------------------------------------------------------------- types
@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict

    def as_dict(self) -> dict:
        return {"id": self.id, "name": self.name, "arguments": self.arguments}


@dataclass
class Reply:
    text: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    message: dict = field(default_factory=dict)
    raw: Any = None


class LLMError(RuntimeError):
    def __init__(self, msg: str, status: int | None = None, body: str = ""):
        super().__init__(msg)
        self.status = status
        self.body = body


# --------------------------------------------------------------------------- factory
def get_llm(mock_script: str | None = None, force_mock: bool = False):
    """Return the configured client. In mock mode, `mock_script` names the scripted
    responder to use (each demo registers its own)."""
    if force_mock or agent_mode() == "mock":
        from .mock_llm import ScriptedLLM

        if not mock_script:
            raise ConfigError("Mock mode needs a mock_script name.")
        return ScriptedLLM(mock_script)
    provider = (env("LLM_PROVIDER") or "").lower()
    if provider in ("openai", "azure", "ollama", "compatible"):
        return OpenAICompatLLM("azure" if provider == "azure" else "openai")
    if provider == "bedrock":
        return BedrockLLM()
    raise ConfigError(
        "LLM_PROVIDER is not set (use openai, azure or bedrock in .env), "
        "or run the demo with --mock to use scripted responses."
    )


def _temperature() -> float | None:
    raw = env("LLM_TEMPERATURE", "0")
    return None if raw.lower() in ("none", "default", "") else float(raw)


# --------------------------------------------------------------------------- HTTP
def _ssl_context() -> ssl.SSLContext:
    cafile = env("SSL_CERT_FILE") or env("REQUESTS_CA_BUNDLE")
    return ssl.create_default_context(cafile=cafile) if cafile else ssl.create_default_context()


def post_json(url: str, body: dict, headers: dict, timeout: float = 90.0, retries: int = 2) -> dict:
    """POST JSON and return the parsed reply. Honours HTTPS_PROXY and a corporate CA
    bundle (SSL_CERT_FILE / REQUESTS_CA_BUNDLE). Retries 429 and 5xx with backoff."""
    data = json.dumps(body).encode("utf-8")
    for attempt in range(retries + 1):
        req = urllib.request.Request(
            url, data=data, method="POST",
            headers={"Content-Type": "application/json", "Accept": "application/json", **headers},
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout, context=_ssl_context()) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            text = e.read().decode("utf-8", errors="replace")
            if e.code in (429, 500, 502, 503, 504) and attempt < retries:
                time.sleep(2 * (attempt + 1))
                continue
            raise LLMError(f"HTTP {e.code} from {url.split('?')[0]}: {text[:400]}", e.code, text) from None
        except urllib.error.URLError as e:
            if attempt < retries:
                time.sleep(2 * (attempt + 1))
                continue
            raise LLMError(f"Could not reach {url.split('?')[0]}: {e.reason}") from None
    raise LLMError("unreachable")


# --------------------------------------------------------------------------- OpenAI / Azure
def to_openai_messages(messages: list[dict]) -> list[dict]:
    out = []
    for m in messages:
        role = m["role"]
        if role in ("system", "user"):
            out.append({"role": role, "content": m["content"]})
        elif role == "assistant":
            msg: dict = {"role": "assistant", "content": m.get("content") or None}
            if m.get("tool_calls"):
                msg["tool_calls"] = [
                    {"id": c["id"], "type": "function",
                     "function": {"name": c["name"], "arguments": json.dumps(c["arguments"])}}
                    for c in m["tool_calls"]
                ]
            elif msg["content"] is None:
                msg["content"] = ""
            out.append(msg)
        elif role == "tool":
            out.append({"role": "tool", "tool_call_id": m["tool_call_id"], "content": m["content"]})
    return out


def to_openai_tools(tools: list[dict]) -> list[dict]:
    return [{"type": "function", "function": {"name": t["name"], "description": t["description"],
                                              "parameters": t["parameters"]}} for t in tools]


def from_openai_response(resp: dict) -> Reply:
    msg = resp["choices"][0]["message"]
    calls = []
    for i, tc in enumerate(msg.get("tool_calls") or []):
        fn = tc.get("function", {})
        raw_args = fn.get("arguments") or "{}"
        try:
            args = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args)
        except json.JSONDecodeError:
            args = {"_unparsed": raw_args}
        calls.append(ToolCall(tc.get("id") or f"call_{i + 1}", fn.get("name", "?"), args or {}))
    text = msg.get("content") or ""
    message = {"role": "assistant", "content": text or None, "tool_calls": [c.as_dict() for c in calls]}
    return Reply(text=text, tool_calls=calls, message=message, raw=resp)


class OpenAICompatLLM:
    def __init__(self, flavour: str = "openai"):
        self.flavour = flavour
        self.temperature = _temperature()
        if flavour == "azure":
            endpoint = require("AZURE_OPENAI_ENDPOINT").rstrip("/")
            self.model = require("AZURE_OPENAI_DEPLOYMENT")
            version = env("AZURE_OPENAI_API_VERSION", "2024-10-21")
            self.url = f"{endpoint}/openai/deployments/{self.model}/chat/completions?api-version={version}"
            self.headers = {"api-key": require("AZURE_OPENAI_API_KEY")}
            self.send_model = False
        else:
            base = (env("OPENAI_BASE_URL") or "https://api.openai.com/v1").rstrip("/")
            self.url = base + "/chat/completions"
            key = env("OPENAI_API_KEY")
            self.headers = {"Authorization": f"Bearer {key}"} if key else {}
            self.model = require("LLM_MODEL")
            self.send_model = True
        self.last_request: dict | None = None
        self.last_response: dict | None = None

    def describe(self) -> str:
        where = "Azure OpenAI" if self.flavour == "azure" else self.url.rsplit("/chat/completions", 1)[0]
        return f"{where} · {self.model}"

    def chat(self, messages: list[dict], tools: list[dict] | None = None) -> Reply:
        body: dict = {"messages": to_openai_messages(messages)}
        if self.send_model:
            body["model"] = self.model
        if tools:
            body["tools"] = to_openai_tools(tools)
        if self.temperature is not None:
            body["temperature"] = self.temperature
        try:
            resp = post_json(self.url, body, self.headers)
        except LLMError as e:
            # Some newer models only accept their default temperature: drop it and retry once.
            if e.status == 400 and "temperature" in e.body and "temperature" in body:
                self.temperature = None
                body.pop("temperature")
                resp = post_json(self.url, body, self.headers)
            else:
                raise
        self.last_request, self.last_response = body, resp
        return from_openai_response(resp)


# --------------------------------------------------------------------------- Amazon Bedrock
def to_bedrock_messages(messages: list[dict]) -> tuple[list[dict], list[dict]]:
    """Return (system_blocks, messages) for the Converse API. Bedrock wants tool results
    inside a *user* message and strictly alternating roles, so neighbours are merged."""
    system: list[dict] = []
    out: list[dict] = []

    def push(role: str, blocks: list[dict]) -> None:
        if out and out[-1]["role"] == role:
            out[-1]["content"].extend(blocks)
        else:
            out.append({"role": role, "content": blocks})

    for m in messages:
        role = m["role"]
        if role == "system":
            system.append({"text": m["content"]})
        elif role == "user":
            push("user", [{"text": m["content"]}])
        elif role == "assistant":
            blocks: list[dict] = []
            if m.get("content"):
                blocks.append({"text": m["content"]})
            for c in m.get("tool_calls") or []:
                blocks.append({"toolUse": {"toolUseId": c["id"], "name": c["name"], "input": c["arguments"]}})
            push("assistant", blocks or [{"text": "(no content)"}])
        elif role == "tool":
            try:
                parsed = json.loads(m["content"])
            except (json.JSONDecodeError, TypeError):
                parsed = None
            if isinstance(parsed, dict):
                content = [{"json": parsed}]
            elif parsed is not None:
                content = [{"json": {"result": parsed}}]
            else:
                content = [{"text": str(m["content"])}]
            result: dict = {"toolUseId": m["tool_call_id"], "content": content}
            if isinstance(parsed, dict) and "error" in parsed:
                result["status"] = "error"
            push("user", [{"toolResult": result}])
    return system, out


def to_bedrock_tools(tools: list[dict]) -> dict:
    return {"tools": [{"toolSpec": {"name": t["name"], "description": t["description"],
                                    "inputSchema": {"json": t["parameters"]}}} for t in tools]}


def from_bedrock_response(resp: dict) -> Reply:
    msg = resp["output"]["message"]
    texts, calls = [], []
    for block in msg.get("content", []):
        if "text" in block:
            texts.append(block["text"])
        if "toolUse" in block:
            tu = block["toolUse"]
            calls.append(ToolCall(tu["toolUseId"], tu["name"], tu.get("input") or {}))
    text = "\n".join(texts).strip()
    message = {"role": "assistant", "content": text or None, "tool_calls": [c.as_dict() for c in calls]}
    raw = {k: v for k, v in resp.items() if k != "ResponseMetadata"}
    return Reply(text=text, tool_calls=calls, message=message, raw=raw)


class BedrockLLM:
    def __init__(self):
        try:
            import boto3  # noqa: WPS433
        except ImportError:
            raise ConfigError("LLM_PROVIDER=bedrock needs boto3: pip install boto3") from None
        region = env("AWS_REGION") or env("AWS_DEFAULT_REGION")
        if not region:
            raise ConfigError("Set AWS_REGION in .env for Bedrock (for example us-east-1).")
        self.client = boto3.client("bedrock-runtime", region_name=region)
        self.model = require("LLM_MODEL")
        self.region = region
        self.temperature = _temperature()
        self.last_request: dict | None = None
        self.last_response: dict | None = None

    def describe(self) -> str:
        return f"Amazon Bedrock ({self.region}) · {self.model}"

    def chat(self, messages: list[dict], tools: list[dict] | None = None) -> Reply:
        system, msgs = to_bedrock_messages(messages)
        kwargs: dict = {"modelId": self.model, "messages": msgs, "inferenceConfig": {"maxTokens": 1024}}
        if self.temperature is not None:
            kwargs["inferenceConfig"]["temperature"] = self.temperature
        if system:
            kwargs["system"] = system
        if tools:
            kwargs["toolConfig"] = to_bedrock_tools(tools)
        resp = self.client.converse(**kwargs)
        self.last_request, self.last_response = kwargs, resp
        return from_bedrock_response(resp)


# Convenience for the slide example `from agentkit import llm; llm.chat(...)`
_default = None


def chat(messages: list[dict], tools: list[dict] | None = None) -> Reply:
    global _default
    if _default is None:
        _default = get_llm()
    return _default.chat(messages, tools)
