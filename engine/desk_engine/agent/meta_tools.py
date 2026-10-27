"""``more_tools``: lets the model ask for tool groups that were not sent with the request (see context.py)."""

from __future__ import annotations

from typing import Any

from .context import GROUP_HELP
from .tools import ToolContext, ToolOutput, registry, schema


@registry.tool("more_tools", "Load more tool groups for the next step when the tools you have cannot do the task: "
               + "; ".join(f"{k} = {v}" for k, v in GROUP_HELP.items()) + ".",
               schema({"groups": {"type": "array", "items": {"type": "string", "enum": sorted(GROUP_HELP)}}},
                      ["groups"]), category="meta")
async def _more(ctx: ToolContext, a: dict[str, Any]) -> ToolOutput:
    groups = [g for g in a.get("groups") or [] if g in GROUP_HELP]
    if not groups:
        return ToolOutput("Name at least one group: " + ", ".join(sorted(GROUP_HELP)) + ".")
    tools = await ctx.engine.tools.for_context(ctx, categories=set(groups))
    if not tools:
        return ToolOutput(f"No tools are available in {', '.join(groups)} (the connection may not be set up).")
    return ToolOutput(f"Loaded {', '.join(groups)}: {', '.join(sorted(tools))}. Use them from your next step.")
