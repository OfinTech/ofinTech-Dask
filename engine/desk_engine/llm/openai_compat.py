"""Chat Completions protocol (OpenAI and every compatible provider: Gemini, DeepSeek, Mistral,
Groq, OpenRouter, xAI, Together, Azure OpenAI v1, Ollama, LM Studio, vLLM, LiteLLM, ...)."""

from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator
from typing import Any

import httpx2 as httpx

from ..errors import UpstreamError
from .base import ChatMessage, LLMResult, StreamEvent, ToolCall, ToolSpec, Usage

_TOOL_NAME = re.compile(r"[^a-zA-Z0-9_-]")


def safe_tool_name(name: str) -> str:
    return _TOOL_NAME.sub("_", name)[:64]


def to_openai_messages(messages: list[ChatMessage]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for m in messages:
        if m.role == "system" and out and out[-1]["role"] == "system":  # one system message for every server
            out[-1]["content"] += "\n\n" + m.content
            continue
        if m.role == "assistant" and m.tool_calls:
            out.append({
                "role": "assistant",
                "content": m.content or None,
                "tool_calls": [
                    {"id": t.id, "type": "function",
                     "function": {"name": safe_tool_name(t.name), "arguments": json.dumps(t.arguments)}}
                    for t in m.tool_calls
                ],
            })
        elif m.role == "tool":
            out.append({"role": "tool", "tool_call_id": m.tool_call_id, "content": m.content})
        else:
            out.append({"role": m.role, "content": m.content})
    return out


def upstream_error(provider: str, status: int, body: str) -> UpstreamError:
    if status in (401, 403):
        return UpstreamError(f"{provider} rejected the API key or the model is not available to it.",
                             code="llm_auth")
    if status == 429:
        return UpstreamError(f"{provider} is rate limiting requests or the account has no credit left.",
                             code="llm_rate_limited")
    if status == 404:
        return UpstreamError(f"{provider} does not know this model or endpoint.", code="llm_not_found")
    snippet = body[:300].replace("\n", " ")
    return UpstreamError(f"{provider} returned HTTP {status}: {snippet}", code="llm_error")


class OpenAICompatible:
    def __init__(self, *, label: str, base_url: str, api_key: str | None, model: str,
                 auth_header: str = "bearer", stream_usage: bool = True, timeout: float = 120.0,
                 transport: httpx.AsyncBaseTransport | None = None, extra_headers: dict[str, str] | None = None):
        self.label = label
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.auth_header = auth_header
        self.stream_usage = stream_usage
        self.timeout = timeout
        self.transport = transport
        self.extra_headers = extra_headers or {}

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json", **self.extra_headers}
        if self.api_key:
            if self.auth_header == "api-key":
                headers["api-key"] = self.api_key
            else:
                headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=httpx.Timeout(self.timeout, connect=15.0), transport=self.transport)

    async def list_models(self) -> list[str]:
        async with self._client() as client:
            resp = await client.get(f"{self.base_url}/models", headers=self._headers())
        if resp.status_code >= 400:
            raise upstream_error(self.label, resp.status_code, resp.text)
        data = resp.json()
        items = data.get("data", data.get("models", [])) if isinstance(data, dict) else data
        names = []
        for item in items:
            name = item.get("id") or item.get("name") if isinstance(item, dict) else str(item)
            if name:
                names.append(name.removeprefix("models/"))
        return sorted(set(names))

    async def stream(self, messages: list[ChatMessage], tools: list[ToolSpec], *,
                     temperature: float | None = None, max_tokens: int | None = None) -> AsyncIterator[StreamEvent]:
        name_map = {safe_tool_name(t.name): t.name for t in tools}
        body: dict[str, Any] = {
            "model": self.model,
            "messages": to_openai_messages(messages),
            "stream": True,
        }
        if tools:
            body["tools"] = [
                {"type": "function",
                 "function": {"name": safe_tool_name(t.name), "description": t.description[:1024],
                              "parameters": t.parameters}}
                for t in tools
            ]
            body["tool_choice"] = "auto"
        if temperature is not None:
            body["temperature"] = temperature
        if max_tokens is not None:
            body["max_tokens"] = max_tokens
        if self.stream_usage:
            body["stream_options"] = {"include_usage": True}

        text_parts: list[str] = []
        calls: dict[int, dict[str, Any]] = {}
        usage = Usage()
        finish = "stop"
        async with self._client() as client:
            async with client.stream("POST", f"{self.base_url}/chat/completions", headers=self._headers(),
                                     json=body) as resp:
                if resp.status_code == 400 and self.stream_usage:
                    text = (await resp.aread()).decode(errors="replace")
                    if "stream_options" in text:
                        self.stream_usage = False
                        async for ev in self.stream(messages, tools, temperature=temperature, max_tokens=max_tokens):
                            yield ev
                        return
                    raise upstream_error(self.label, resp.status_code, text)
                if resp.status_code >= 400:
                    raise upstream_error(self.label, resp.status_code, (await resp.aread()).decode(errors="replace"))
                async for line in resp.aiter_lines():
                    line = line.strip()
                    if not line.startswith("data:"):
                        continue
                    payload = line[5:].strip()
                    if payload == "[DONE]":
                        break
                    try:
                        chunk = json.loads(payload)
                    except json.JSONDecodeError:
                        continue
                    if chunk.get("error"):
                        raise UpstreamError(f"{self.label}: {str(chunk['error'])[:300]}", code="llm_error")
                    if chunk.get("usage"):
                        u = chunk["usage"]
                        cached = int((u.get("prompt_tokens_details") or {}).get("cached_tokens") or
                                     u.get("prompt_cache_hit_tokens") or 0)  # OpenAI / DeepSeek automatic caching
                        usage = Usage(int(u.get("prompt_tokens") or 0), int(u.get("completion_tokens") or 0), cached)
                    for choice in chunk.get("choices") or []:
                        delta = choice.get("delta") or {}
                        if delta.get("content"):
                            text_parts.append(delta["content"])
                            yield StreamEvent("text", delta=delta["content"])
                        for tc in delta.get("tool_calls") or []:
                            slot = calls.setdefault(int(tc.get("index", 0)), {"id": "", "name": "", "args": ""})
                            if tc.get("id"):
                                slot["id"] = tc["id"]
                            fn = tc.get("function") or {}
                            if fn.get("name"):
                                slot["name"] += fn["name"]
                            if fn.get("arguments"):
                                slot["args"] += fn["arguments"]
                        if choice.get("finish_reason"):
                            finish = choice["finish_reason"]

        tool_calls = []
        for index in sorted(calls):
            slot = calls[index]
            try:
                args = json.loads(slot["args"] or "{}")
            except json.JSONDecodeError:
                args = {"_invalid_json": slot["args"][:2000]}
            name = name_map.get(slot["name"], slot["name"])
            tool_calls.append(ToolCall(slot["id"] or f"call_{index}", name, args if isinstance(args, dict) else {}))
        yield StreamEvent("done", result=LLMResult("".join(text_parts), tool_calls, usage, finish))
