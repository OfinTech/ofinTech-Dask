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


INCOMPLETE_FINISH = {"length", "content_filter"}


def require_complete(result: LLMResult | None, what: str) -> LLMResult:
    """A reply is complete only when the stream gave a result, the model finished on its own (not cut by the output
    limit or a content filter), and it said something. Every caller that shows or stores a model's reply uses this,
    so a truncated or empty reply never reaches anyone as a finished answer (audit AI-03)."""
    from ..errors import AppError

    if result is None:
        raise AppError(f"The model's {what} ended without a result. Nothing was kept; try again.",
                       code="llm_incomplete", status=502)
    if result.finish_reason in INCOMPLETE_FINISH:
        raise AppError(f"The model's {what} was cut off ({result.finish_reason}). Nothing was kept as complete; "
                       "try again.", code="answer_incomplete", status=502)
    if not result.text.strip() and not result.tool_calls:
        raise AppError(f"The model returned an empty {what}. Nothing was kept; try again.", code="answer_empty",
                       status=502)
    return result
