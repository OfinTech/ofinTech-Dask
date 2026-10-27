"""Provider-neutral types for chat models with tool calling."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

Role = Literal["system", "user", "assistant", "tool"]


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class ChatMessage:
    role: Role
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_call_id: str | None = None
    name: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "content": self.content,
            "tool_calls": [{"id": t.id, "name": t.name, "arguments": t.arguments} for t in self.tool_calls],
            "tool_call_id": self.tool_call_id,
            "name": self.name,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ChatMessage:
        return cls(
            role=d["role"],
            content=d.get("content") or "",
            tool_calls=[ToolCall(t["id"], t["name"], t.get("arguments") or {}) for t in d.get("tool_calls") or []],
            tool_call_id=d.get("tool_call_id"),
            name=d.get("name"),
        )


@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]


@dataclass
class Usage:
    """``input_tokens`` counts every prompt token, including the cached ones in ``cache_read``/``cache_write``."""
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read: int = 0
    cache_write: int = 0

    def add(self, other: Usage) -> None:
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens
        self.cache_read += other.cache_read
        self.cache_write += other.cache_write


@dataclass
class LLMResult:
    text: str
    tool_calls: list[ToolCall]
    usage: Usage
    finish_reason: str = "stop"


@dataclass
class StreamEvent:
    """``text`` events carry a delta; the final ``done`` event carries the full result."""

    type: Literal["text", "done"]
    delta: str = ""
    result: LLMResult | None = None


class ChatModel(Protocol):
    async def stream(self, messages: list[ChatMessage], tools: list[ToolSpec], *,
                     temperature: float | None = None, max_tokens: int | None = None) -> AsyncIterator[StreamEvent]:
        ...

    async def list_models(self) -> list[str]:
        ...


async def complete(model: ChatModel, messages: list[ChatMessage], tools: list[ToolSpec] | None = None,
                   **kw: Any) -> LLMResult:
    """Non-streaming convenience wrapper."""
    result: LLMResult | None = None
    async for event in model.stream(messages, tools or [], **kw):
        if event.type == "done":
            result = event.result
    assert result is not None
    return result
