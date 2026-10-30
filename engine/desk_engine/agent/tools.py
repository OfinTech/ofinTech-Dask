"""Tool registry for the agent.

Every tool declares its ``effect``:

* ``read``  — looks things up or computes; runs without asking.
* ``write`` — changes something outside the conversation (sends an e-mail or chat message,
  creates a record in an accounting system, runs a workflow). The agent pauses and a workspace
  member with the editor role or higher must approve the exact arguments first.

Tool results go back to the model as text and to the app as typed ``blocks`` (tables, KPI rows,
charts, links). The app renders blocks itself; it never renders HTML produced by a model.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

from ..errors import AppError, Invalid
from ..llm.base import ToolSpec

if TYPE_CHECKING:
    from ..app import Engine

Effect = Literal["read", "write"]


@dataclass
class ToolContext:
    engine: Engine
    workspace: dict[str, Any]
    user_id: str
    thread_id: str | None = None
    run_id: str | None = None
    source: str = "chat"  # chat | workflow | channel | mcp

    @property
    def ws_id(self) -> str:
        return self.workspace["id"]


@dataclass
class ToolOutput:
    text: str
    blocks: list[dict[str, Any]] = field(default_factory=list)
    data: Any = None  # structured value for workflows ({{steps.x.data}})

    def for_model(self, limit: int = 12000) -> str:
        from ..egress import mask_bank_identifiers
        text = mask_bank_identifiers(self.text)   # the model never needs a full IBAN or card number
        if len(text) <= limit:
            return text
        return text[:limit] + f"\n…(truncated, {len(text) - limit} more characters)"


Handler = Callable[[ToolContext, dict[str, Any]], Awaitable[ToolOutput]]


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict[str, Any]
    handler: Handler
    effect: Effect = "read"
    category: str = "finance"
    summarize: Callable[[dict[str, Any]], str] | None = None
    available: Callable[[ToolContext], bool] | None = None
    policy: str | None = None  # MCP tools: auto | ask | off (overrides effect)

    def spec(self) -> ToolSpec:
        return ToolSpec(self.name, self.description, self.parameters)

    def needs_approval(self) -> bool:
        if self.policy is not None:
            return self.policy != "auto"
        return self.effect == "write"

    def describe_call(self, args: dict[str, Any]) -> str:
        if self.summarize:
            try:
                return self.summarize(args)
            except Exception:  # pragma: no cover - summary must never break the run
                pass
        return f"{self.name}({json.dumps(args, default=str)[:300]})"

    async def run(self, ctx: ToolContext, args: dict[str, Any]) -> ToolOutput:
        # checked again at the call, whatever path reached it (agent, workflow step, MCP server, approval)
        from ..egress import SEALED_MESSAGE, is_payment_tool, tool_sends_out, workspace_sealed
        if is_payment_tool(self.name, self.description, acts=self.category == "mcp" or self.needs_approval()):
            return ToolOutput("Refused: OfinTech Desk never initiates, approves or changes payments, transfers or "
                              "beneficiaries.")
        if tool_sends_out(self) and workspace_sealed(ctx.engine, ctx.workspace):
            return ToolOutput(SEALED_MESSAGE)
        if "_invalid_json" in args:
            return ToolOutput("The arguments were not valid JSON. Call the tool again with a JSON object.")
        missing = [k for k in self.parameters.get("required", []) if k not in args]
        if missing:
            return ToolOutput(f"Missing required arguments: {', '.join(missing)}.")
        try:
            return await self.handler(ctx, args)
        except AppError as exc:
            return ToolOutput(f"Error: {exc.message}")
        except (ValueError, TypeError, KeyError, ZeroDivisionError, ArithmeticError) as exc:
            return ToolOutput(f"Error: {type(exc).__name__}: {exc}")


def schema(properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    return {"type": "object", "properties": properties, "required": required or [], "additionalProperties": False}


class ToolRegistry:
    """Static tools registered at start-up plus dynamic providers (MCP servers, connections)."""

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}
        self._providers: list[Callable[[ToolContext], Awaitable[list[Tool]]]] = []

    def add(self, tool: Tool) -> Tool:
        if tool.name in self._tools:
            raise ValueError(f"duplicate tool {tool.name}")
        from ..egress import is_payment_tool
        if is_payment_tool(tool.name, tool.description, acts=tool.effect == "write"):
            raise ValueError(f"{tool.name}: OfinTech Desk never initiates, approves or changes payments, transfers or "
                             "beneficiaries, so tools that do are not registered.")
        self._tools[tool.name] = tool
        return tool

    def tool(self, name: str, description: str, parameters: dict[str, Any], *, effect: Effect = "read",
             category: str = "finance", summarize: Callable[[dict[str, Any]], str] | None = None,
             available: Callable[[ToolContext], bool] | None = None) -> Callable[[Handler], Handler]:
        def deco(fn: Handler) -> Handler:
            self.add(Tool(name, description, parameters, fn, effect, category, summarize, available))
            return fn
        return deco

    def provider(self, fn: Callable[[ToolContext], Awaitable[list[Tool]]]) -> None:
        self._providers.append(fn)

    async def for_context(self, ctx: ToolContext, *, categories: set[str] | None = None) -> dict[str, Tool]:
        from ..egress import is_payment_tool, tool_sends_out, workspace_sealed

        tools = {n: t for n, t in self._tools.items() if t.available is None or t.available(ctx)}
        for provider in self._providers:
            for t in await provider(ctx):
                if t.policy == "off" or is_payment_tool(t.name, t.description, acts=True):   # never trust a server's read-only claim
                    continue
                tools[t.name] = t
        if workspace_sealed(ctx.engine, ctx.workspace):
            tools = {n: t for n, t in tools.items() if not tool_sends_out(t)}
        if categories is not None:
            tools = {n: t for n, t in tools.items() if t.category in categories}
        return tools

    def get_static(self, name: str) -> Tool:
        try:
            return self._tools[name]
        except KeyError as exc:
            raise Invalid(f"Unknown tool: {name}") from exc

    def static_tools(self) -> list[Tool]:
        return list(self._tools.values())


registry = ToolRegistry()


def fmt_money(value: float | int | None, currency: str = "") -> str:
    if value is None:
        return "—"
    sign = "-" if value < 0 else ""
    body = f"{abs(value):,.2f}"
    return f"{sign}{currency + ' ' if currency else ''}{body}"


def table_block(columns: list[dict[str, str]], rows: list[list[Any]], title: str | None = None) -> dict[str, Any]:
    """columns: [{"key": "name", "label": "Name", "format": "text|number|money|percent|date"}]"""
    return {"type": "table", "title": title, "columns": columns, "rows": rows[:500],
            "truncated": len(rows) > 500, "total_rows": len(rows)}


def table_text(columns: list[dict[str, str]], rows: list[list[Any]], limit: int = 60) -> str:
    head = " | ".join(c["label"] for c in columns)
    lines = [head, "-" * min(len(head), 80)]
    for row in rows[:limit]:
        lines.append(" | ".join("" if v is None else (f"{v:,.4g}" if isinstance(v, float) and abs(v) < 1 else
                                                         f"{v:,.2f}" if isinstance(v, float) else str(v))
                                for v in row))
    if len(rows) > limit:
        lines.append(f"… {len(rows) - limit} more rows")
    return "\n".join(lines)
