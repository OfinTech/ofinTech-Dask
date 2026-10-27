"""Anthropic Messages API with streaming and tool use."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx2 as httpx

from ..errors import UpstreamError
from .base import ChatMessage, LLMResult, StreamEvent, ToolCall, ToolSpec, Usage
from .openai_compat import safe_tool_name, upstream_error

API_VERSION = "2023-06-01"


CACHE = {"type": "ephemeral"}


def to_anthropic(messages: list[ChatMessage]) -> tuple[str, list[dict[str, Any]]]:
    system_parts: list[str] = []
    out: list[dict[str, Any]] = []

    def push(role: str, block: dict[str, Any]) -> None:
        if out and out[-1]["role"] == role:
            out[-1]["content"].append(block)
        else:
            out.append({"role": role, "content": [block]})

    for m in messages:
        if m.role == "system":
            system_parts.append(m.content)
        elif m.role == "user":
            push("user", {"type": "text", "text": m.content or " "})
        elif m.role == "assistant":
            if m.content:
                push("assistant", {"type": "text", "text": m.content})
            for t in m.tool_calls:
                push("assistant", {"type": "tool_use", "id": t.id, "name": safe_tool_name(t.name),
                                   "input": t.arguments})
            if not m.content and not m.tool_calls:
                push("assistant", {"type": "text", "text": " "})
        elif m.role == "tool":
            push("user", {"type": "tool_result", "tool_use_id": m.tool_call_id, "content": m.content})
    return "\n\n".join(system_parts), out


def with_cache(messages: list[ChatMessage], tools: list[dict[str, Any]]) -> tuple[list[dict[str, Any]],
                                                                               list[dict[str, Any]], list[dict[str, Any]]]:
    """Prompt caching: breakpoints after the tools, after the first (stable) system block and on the latest message,
    so each step of a run, and the next turn, re-reads the shared prefix from the cache at a tenth of the price."""
    _, msgs = to_anthropic(messages)
    system = [{"type": "text", "text": m.content} for m in messages if m.role == "system" and m.content]
    if system:
        system[0]["cache_control"] = CACHE
    if tools:
        tools = [*tools[:-1], {**tools[-1], "cache_control": CACHE}]
    if msgs:
        last = msgs[-1]["content"][-1]
        if last.get("type") in {"text", "tool_result"}:
            msgs[-1]["content"][-1] = {**last, "cache_control": CACHE}
    return system, tools, msgs


class AnthropicModel:
    def __init__(self, *, label: str, base_url: str, api_key: str | None, model: str,
                 timeout: float = 120.0, transport: httpx.AsyncBaseTransport | None = None,
                 default_max_tokens: int = 4096):
        self.label = label
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key or ""
        self.model = model
        self.timeout = timeout
        self.transport = transport
        self.default_max_tokens = default_max_tokens

    def _headers(self) -> dict[str, str]:
        return {"x-api-key": self.api_key, "anthropic-version": API_VERSION, "content-type": "application/json"}

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=httpx.Timeout(self.timeout, connect=15.0), transport=self.transport)

    async def list_models(self) -> list[str]:
        async with self._client() as client:
            resp = await client.get(f"{self.base_url}/models", headers=self._headers(), params={"limit": 100})
        if resp.status_code >= 400:
            raise upstream_error(self.label, resp.status_code, resp.text)
        return sorted(item["id"] for item in resp.json().get("data", []))

    async def stream(self, messages: list[ChatMessage], tools: list[ToolSpec], *,
                     temperature: float | None = None, max_tokens: int | None = None) -> AsyncIterator[StreamEvent]:
        name_map = {safe_tool_name(t.name): t.name for t in tools}
        tool_defs = [{"name": safe_tool_name(t.name), "description": t.description[:1024], "input_schema": t.parameters}
                     for t in tools]
        system, tool_defs, msgs = with_cache(messages, tool_defs)
        body: dict[str, Any] = {
            "model": self.model,
            "max_tokens": max_tokens or self.default_max_tokens,
            "messages": msgs,
            "stream": True,
        }
        if system:
            body["system"] = system
        if tool_defs:
            body["tools"] = tool_defs
        if temperature is not None:
            body["temperature"] = temperature

        text_parts: list[str] = []
        blocks: dict[int, dict[str, Any]] = {}
        usage = Usage()
        finish = "stop"
        async with self._client() as client:
            async with client.stream("POST", f"{self.base_url}/messages", headers=self._headers(), json=body) as resp:
                if resp.status_code >= 400:
                    raise upstream_error(self.label, resp.status_code, (await resp.aread()).decode(errors="replace"))
                async for line in resp.aiter_lines():
                    line = line.strip()
                    if not line.startswith("data:"):
                        continue
                    try:
                        event = json.loads(line[5:].strip())
                    except json.JSONDecodeError:
                        continue
                    etype = event.get("type")
                    if etype == "message_start":
                        u = event.get("message", {}).get("usage", {})
                        usage.cache_read = int(u.get("cache_read_input_tokens") or 0)
                        usage.cache_write = int(u.get("cache_creation_input_tokens") or 0)
                        usage.input_tokens = int(u.get("input_tokens") or 0) + usage.cache_read + usage.cache_write
                    elif etype == "content_block_start":
                        cb = event.get("content_block", {})
                        blocks[event["index"]] = {"type": cb.get("type"), "id": cb.get("id"), "name": cb.get("name"),
                                                  "json": ""}
                    elif etype == "content_block_delta":
                        delta = event.get("delta", {})
                        if delta.get("type") == "text_delta":
                            text_parts.append(delta.get("text", ""))
                            yield StreamEvent("text", delta=delta.get("text", ""))
                        elif delta.get("type") == "input_json_delta":
                            blocks.setdefault(event["index"], {"type": "tool_use", "json": ""})
                            blocks[event["index"]]["json"] += delta.get("partial_json", "")
                    elif etype == "message_delta":
                        u = event.get("usage", {})
                        usage.output_tokens = int(u.get("output_tokens") or usage.output_tokens)
                        finish = event.get("delta", {}).get("stop_reason") or finish
                    elif etype == "error":
                        raise UpstreamError(f"{self.label}: {str(event.get('error'))[:300]}", code="llm_error")

        calls = []
        for index in sorted(blocks):
            b = blocks[index]
            if b.get("type") != "tool_use":
                continue
            try:
                args = json.loads(b["json"] or "{}")
            except json.JSONDecodeError:
                args = {"_invalid_json": b["json"][:2000]}
            calls.append(ToolCall(b.get("id") or f"toolu_{index}", name_map.get(b.get("name") or "", b.get("name") or ""),
                                  args))
        yield StreamEvent("done", result=LLMResult("".join(text_parts), calls, usage,
                                                   "tool_calls" if finish == "tool_use" else finish))
