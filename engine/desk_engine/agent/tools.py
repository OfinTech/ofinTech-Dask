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

    @property
    def role(self) -> str:
        """The user's role in the workspace, read from the membership at the time of the call (never from the
        model or the request body). A user without a membership gets the least: viewer."""
        row = self.engine.db.one("SELECT role FROM members WHERE workspace_id = ? AND user_id = ?",
                                 (self.ws_id, self.user_id))
        return row["role"] if row else "viewer"

    def may(self, required: str) -> bool:
        from ..web import role_at_least
        return role_at_least(self.role, required)


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


DEFAULT_TOOL_TIMEOUT = 300.0  # seconds a tool call may take before the run moves on with an error


_TYPES: dict[str, type | tuple[type, ...]] = {"string": str, "number": (int, float), "integer": int, "boolean": bool,
                                              "array": list, "object": dict, "null": type(None)}


def validate_args(schema: dict[str, Any], value: Any, path: str = "") -> list[str]:
    """Problems with ``value`` against the parts of a JSON schema the engine understands (type, enum, required,
    properties, additionalProperties, items, minimum/maximum, minLength/maxLength). Keywords it does not know are
    ignored, so a schema from an MCP server can only make this stricter, never fail on its own."""
    problems: list[str] = []
    where = path or "arguments"
    kinds = schema.get("type")
    if isinstance(kinds, str):
        kinds = [kinds]
    if kinds:
        ok = False
        for kind in kinds:
            expected = _TYPES.get(kind)
            if expected is None:
                ok = True
            elif kind == "number" and isinstance(value, bool):
                continue
            elif kind == "integer" and isinstance(value, bool):
                continue
            elif kind == "integer" and isinstance(value, float) and value.is_integer():
                ok = True
            elif isinstance(value, expected):
                ok = True
        if not ok:
            return [f"{where} must be of type {' or '.join(kinds)}."]
    if "enum" in schema and value not in schema["enum"]:
        return [f"{where} must be one of: {', '.join(str(v) for v in schema['enum'])}."]
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            problems.append(f"{where} must be at least {schema['minimum']}.")
        if "maximum" in schema and value > schema["maximum"]:
            problems.append(f"{where} must be at most {schema['maximum']}.")
    if isinstance(value, str):
        if "minLength" in schema and len(value) < schema["minLength"]:
            problems.append(f"{where} is too short.")
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            problems.append(f"{where} is too long (at most {schema['maxLength']} characters).")
    if isinstance(value, list):
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            problems.append(f"{where} has too many items (at most {schema['maxItems']}).")
        items = schema.get("items")
        if isinstance(items, dict):
            for i, item in enumerate(value):
                problems.extend(validate_args(items, item, f"{where}[{i}]"))
    if isinstance(value, dict):
        props = schema.get("properties") or {}
        for key in schema.get("required", []):
            if key not in value:
                problems.append(f"{where}.{key} is required." if path else f"Missing required argument: {key}.")
        for key, sub in value.items():
            if key in props and isinstance(props[key], dict):
                problems.extend(validate_args(props[key], sub, f"{where}.{key}" if path else key))
            elif schema.get("additionalProperties") is False and props:
                problems.append(f"{where}.{key} is not a known argument." if path else f"{key} is not a known argument.")
    return problems


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
    timeout: float = DEFAULT_TOOL_TIMEOUT
    # The least role that may call the tool, mirroring the API route that does the same thing: a tool that creates
    # or refreshes workspace records without an approval (dashboards, generated files, close runs, synced datasets)
    # is editor-only, exactly as its route is. Checked when tools are offered and again at every call.
    min_role: str = "viewer"

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
        if "_invalid_json" in args or not isinstance(args, dict):
            return ToolOutput("The arguments were not valid JSON. Call the tool again with a JSON object.")
        if self.min_role != "viewer" and not ctx.may(self.min_role):
            return ToolOutput(f"Refused: {self.name} needs the {self.min_role} role in this workspace; the user is a "
                              f"{ctx.role}. Tell the user what you would have done and who can do it.")
        problems = validate_args(self.parameters, args)
        if problems:
            return ToolOutput("Error: the arguments do not match the tool's schema. " + " ".join(problems[:6]))
        import asyncio
        try:
            return await asyncio.wait_for(self.handler(ctx, args), self.timeout)
        except TimeoutError:
            return ToolOutput(f"Error: {self.name} did not finish within {int(self.timeout)} seconds and was stopped.")
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
             available: Callable[[ToolContext], bool] | None = None,
             timeout: float = DEFAULT_TOOL_TIMEOUT, min_role: str = "viewer") -> Callable[[Handler], Handler]:
        def deco(fn: Handler) -> Handler:
            self.add(Tool(name, description, parameters, fn, effect, category, summarize, available, timeout=timeout,
                          min_role=min_role))
            return fn
        return deco

    def provider(self, fn: Callable[[ToolContext], Awaitable[list[Tool]]]) -> None:
        self._providers.append(fn)

    async def for_context(self, ctx: ToolContext, *, categories: set[str] | None = None) -> dict[str, Tool]:
        from ..egress import is_payment_tool, tool_sends_out, workspace_sealed

        tools = {n: t for n, t in self._tools.items() if t.available is None or t.available(ctx)}
        if any(t.min_role != "viewer" for t in tools.values()):
            role = ctx.role
            from ..web import role_at_least
            tools = {n: t for n, t in tools.items() if role_at_least(role, t.min_role)}
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
