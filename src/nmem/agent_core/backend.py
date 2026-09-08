"""Provider-agnostic LLM backend — the agent's reasoning brain, pluggable.

Destined for `nmem-agent-core`: a public user can point their agent at a local
OpenAI-compatible endpoint (vLLM / LM Studio), a hosted OpenAI-compatible API
(OpenAI / OpenRouter / Together / Groq / DeepSeek / Fireworks), or Anthropic
(Claude) — by config alone. michelle herself always runs local Gemma; the
abstraction exists for the framework's future users, not for her.

Two orthogonal axes:
  provider — the API SHAPE:  "openai" (any /v1/chat/completions) | "anthropic"
  family   — the param DIALECT: "gemma" | "qwen" | "claude" | "generic"
             Qwen wants reasoning_effort + chat_template_kwargs.enable_thinking;
             Claude wants an optional `thinking` block; Gemma/generic want neither.

Both adapters speak OpenAI-style tool schemas on input and return a normalized
ChatResult, so callers never branch on provider.
"""
from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field

import httpx

log = logging.getLogger(__name__)

_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict


@dataclass
class ChatResult:
    content: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    finish_reason: str | None = None
    usage: dict | None = None


# ── OpenAI-compatible (vLLM, OpenAI, OpenRouter, Together, Groq, DeepSeek, …) ──
class OpenAICompatibleBackend:
    def __init__(self, base_url: str, model: str, api_key: str = "", family: str = "generic",
                 timeout: float = 300.0):
        if not base_url:
            raise ValueError("OpenAI-compatible backend needs a base_url")
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.family = family
        self.timeout = timeout

    def _headers(self) -> dict:
        h = {"Content-Type": "application/json"}
        if self.api_key:
            h["Authorization"] = f"Bearer {self.api_key}"
        return h

    def _apply_family(self, body: dict, extra: dict):
        """Inject the param dialect for this model family (only where it applies)."""
        if self.family == "qwen":
            body["reasoning_effort"] = extra.get("reasoning_effort", "medium")
            body["chat_template_kwargs"] = {"enable_thinking": extra.get("enable_thinking", False)}
        # gemma / generic: nothing — the server-side parser (e.g. gemma4) handles it.

    async def _post(self, body: dict) -> dict:
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            r = await client.post(f"{self.base_url}/chat/completions", json=body, headers=self._headers())
            r.raise_for_status()
            return r.json()

    async def chat(self, messages: list[dict], *, temperature: float = 0.3,
                   max_tokens: int = 1024, **extra) -> str:
        body = {"model": self.model, "messages": messages,
                "temperature": temperature, "max_tokens": max_tokens}
        self._apply_family(body, extra)
        j = await self._post(body)
        content = j["choices"][0]["message"].get("content") or ""
        return _THINK_RE.sub("", content).strip()

    async def chat_with_tools(self, messages: list[dict], tools: list[dict], *,
                              tool_choice: str = "auto", temperature: float = 0.3,
                              max_tokens: int = 1024, **extra) -> ChatResult:
        body = {"model": self.model, "messages": messages, "tools": tools,
                "tool_choice": tool_choice, "temperature": temperature, "max_tokens": max_tokens}
        self._apply_family(body, extra)
        j = await self._post(body)
        choice = j["choices"][0]
        msg = choice.get("message", {})
        calls = []
        for tc in (msg.get("tool_calls") or []):
            fn = tc.get("function", {})
            args = fn.get("arguments")
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except json.JSONDecodeError:
                    args = {"_raw": args}
            calls.append(ToolCall(id=tc.get("id", ""), name=fn.get("name", ""), arguments=args or {}))
        return ChatResult(
            content=_THINK_RE.sub("", msg.get("content") or "").strip(),
            tool_calls=calls,
            finish_reason=choice.get("finish_reason"),
            usage=j.get("usage"),
        )


# ── Anthropic (Claude) — different API shape, normalized to the same result ──
class AnthropicBackend:
    def __init__(self, model: str, api_key: str, family: str = "claude",
                 base_url: str = "https://api.anthropic.com", timeout: float = 300.0):
        if not api_key:
            raise ValueError("Anthropic backend needs an api_key")
        self.model = model
        self.api_key = api_key
        self.family = family
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def _headers(self) -> dict:
        return {"Content-Type": "application/json", "x-api-key": self.api_key,
                "anthropic-version": "2023-06-01"}

    @staticmethod
    def _split_system(messages: list[dict]) -> tuple[str | None, list[dict]]:
        system = None
        rest = []
        for m in messages:
            if m.get("role") == "system":
                system = (system + "\n\n" if system else "") + (m.get("content") or "")
            else:
                rest.append({"role": m["role"], "content": m.get("content", "")})
        return system, rest

    @staticmethod
    def _apply_thinking(body: dict, extra: dict):
        """Apply Anthropic 'extended thinking' with its API constraints:
        temperature MUST be default (so omit it), and budget_tokens MUST be
        < max_tokens. Without this, `thinking=True` at default params 400s."""
        if not extra.get("thinking"):
            return
        budget = int(extra.get("thinking_budget", 2048))
        if budget >= body["max_tokens"]:
            body["max_tokens"] = budget + 512   # ensure room for the answer after thinking
        body["thinking"] = {"type": "enabled", "budget_tokens": budget}
        body.pop("temperature", None)           # thinking requires the default temperature

    @staticmethod
    def _convert_tools(tools: list[dict]) -> list[dict]:
        # OpenAI {type:function, function:{name,description,parameters}} -> Anthropic tool
        out = []
        for t in tools or []:
            fn = t.get("function", t)
            out.append({"name": fn.get("name"), "description": fn.get("description", ""),
                        "input_schema": fn.get("parameters", {"type": "object", "properties": {}})})
        return out

    async def _post(self, body: dict) -> dict:
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            r = await client.post(f"{self.base_url}/v1/messages", json=body, headers=self._headers())
            r.raise_for_status()
            return r.json()

    async def chat(self, messages: list[dict], *, temperature: float = 0.3,
                   max_tokens: int = 1024, **extra) -> str:
        system, msgs = self._split_system(messages)
        body = {"model": self.model, "max_tokens": max_tokens, "temperature": temperature, "messages": msgs}
        if system:
            body["system"] = system
        self._apply_thinking(body, extra)
        j = await self._post(body)
        return "".join(b.get("text", "") for b in j.get("content", []) if b.get("type") == "text").strip()

    async def chat_with_tools(self, messages: list[dict], tools: list[dict], *,
                              tool_choice: str = "auto", temperature: float = 0.3,
                              max_tokens: int = 1024, **extra) -> ChatResult:
        system, msgs = self._split_system(messages)
        tc_map = {"auto": {"type": "auto"}, "required": {"type": "any"}, "any": {"type": "any"}}
        body = {"model": self.model, "max_tokens": max_tokens, "temperature": temperature,
                "messages": msgs, "tools": self._convert_tools(tools),
                "tool_choice": tc_map.get(tool_choice, {"type": "auto"})}
        if system:
            body["system"] = system
        self._apply_thinking(body, extra)
        j = await self._post(body)
        content, calls = "", []
        for b in j.get("content", []):
            if b.get("type") == "text":
                content += b.get("text", "")
            elif b.get("type") == "tool_use":
                calls.append(ToolCall(id=b.get("id", ""), name=b.get("name", ""),
                                      arguments=b.get("input") or {}))
        return ChatResult(content=content.strip(), tool_calls=calls,
                          finish_reason=j.get("stop_reason"), usage=j.get("usage"))


# ── factory ──
def build_backend(config: dict, role: str = "brain"):
    """Build the pluggable brain for `role` from config + env overrides.

    Env wins over config so a deployment can repoint the brain without editing
    the repo: MICHELLE_LLM_PROVIDER / _BASE_URL / _MODEL / _API_KEY / _FAMILY.
    """
    b = config.get("backends", {}).get(role, {})
    provider = os.environ.get("MICHELLE_LLM_PROVIDER") or b.get("provider", "openai")
    model = os.environ.get("MICHELLE_LLM_MODEL") or b.get("model")
    # key resolution, secret-safe: explicit env > api_key in config (discouraged) > the env var
    # NAMED by api_key_env (how the studio keeps hosted keys out of agent.yaml). Empty = keyless
    # (a local vLLM/Ollama endpoint), which is fine.
    api_key = (os.environ.get("MICHELLE_LLM_API_KEY") or b.get("api_key", "")
               or (os.environ.get(b["api_key_env"], "") if b.get("api_key_env") else ""))
    family = os.environ.get("MICHELLE_LLM_FAMILY") or b.get("family", "generic")

    if provider == "anthropic":
        base = os.environ.get("MICHELLE_LLM_BASE_URL") or b.get("url") or "https://api.anthropic.com"
        return AnthropicBackend(model=model, api_key=api_key, family=family, base_url=base)

    base = os.environ.get("MICHELLE_LLM_BASE_URL") or b.get("url")
    return OpenAICompatibleBackend(base_url=base, model=model, api_key=api_key, family=family)


_backend = None


def init_backend(config: dict, role: str = "brain"):
    global _backend
    _backend = build_backend(config, role)
    log.info("brain backend: provider=%s model=%s family=%s",
             type(_backend).__name__, getattr(_backend, "model", "?"), getattr(_backend, "family", "?"))
    return _backend


def get_backend():
    if _backend is None:
        raise RuntimeError("model backend not initialized — call init_backend() first")
    return _backend
