"""Tools about the tools: ``more_tools`` lets the model ask for tool groups that were not sent with the request
(see context.py), and ``list_capabilities`` tells it what it actually has."""

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


# What the assistant can do is a closed question: the registry holds the answer. A model sees only the groups a
# step was sent, so asked for its tools it lists what it was shown and fills in the rest. On 24 Nov 2026 one
# answered "53", with a list that included adding a bank transfer, a deposit and a withdrawal: tools that do not
# exist in a product that moves no money.
GROUP_NAMES = {"finance": "Finance: calculators, and the ledger's statements, ageing, anomalies, lines and bank reconciliation",
               "knowledge": "Documents", "memory": "Memory", "skills": "Skills (instructions for a task)",
               "meta": "About the tools themselves", "mcp": "Tools from servers the workspace connected",
               "analysis": "Analysis: KPIs, forecasts and the month-end close", "data": "Data: datasets and dashboards",
               "accounting": "The connected accounting system", "files": "Files: Excel workbooks and Word reports",
               "google": "Gmail, Google Drive and Sheets", "costcentres": "Cost centres and projects",
               "workflows": "Workflows", "collaboration": "Colleagues"}


def _brief(text: str, limit: int = 110) -> str:
    """The first sentence of a tool's description, or its first words."""
    first = " ".join(text.split()).split(". ")[0].rstrip(".")
    return first if len(first) <= limit else first[:limit].rsplit(" ", 1)[0] + "…"


@registry.tool("list_capabilities", "The tools you actually have in this workspace: how many, in which groups, what "
               "each does, and which need a person's approval before they run. Call it whenever the user asks what "
               "you can do, which tools or features you have, or how many; answer from its result and name no tool "
               "that is not in it. Never list or count your tools from memory: each step shows you only some of "
               "them.", schema({}), category="meta")
async def _capabilities(ctx: ToolContext, a: dict[str, Any]) -> ToolOutput:
    tools = await ctx.engine.tools.for_context(ctx)
    groups: dict[str, list[Any]] = {}
    for t in sorted(tools.values(), key=lambda t: t.name):
        groups.setdefault(t.category, []).append(t)
    order = sorted(groups, key=lambda g: (list(GROUP_NAMES).index(g) if g in GROUP_NAMES else len(GROUP_NAMES), g))
    approval = sorted(t.name for t in tools.values() if t.needs_approval())
    lines = [f"Tools available to you in this workspace: {len(tools)}, in {len(groups)} groups. A step of a "
             "conversation is sent only the groups it needs; the others are loaded with more_tools."]
    for g in order:
        lines.append(f"{GROUP_NAMES.get(g, g)} ({len(groups[g])}):")
        lines += [f"- {t.name}: {_brief(t.description)}" for t in groups[g]]
    lines.append((f"Run only after a person approves them in the app ({len(approval)}): " + ", ".join(approval) + ".")
                 if approval else "None of them changes anything outside this conversation.")
    lines.append("No tool pays, transfers money, or adds or changes a beneficiary or bank details. Anything not listed "
                 "above is not something you can do here: a group that is missing has no connection or no data yet.")
    cols = [{"key": "group", "label": "Group", "format": "text"}, {"key": "tool", "label": "Tool", "format": "text"},
            {"key": "what", "label": "What it does", "format": "text"},
            {"key": "approval", "label": "Needs approval", "format": "text"}]
    rows = [[GROUP_NAMES.get(g, g).split(":")[0], t.name, _brief(t.description), "yes" if t.needs_approval() else ""]
            for g in order for t in groups[g]]
    from .tools import table_block
    return ToolOutput("\n".join(lines), [table_block(cols, rows, "Tools")],
                      {"count": len(tools), "groups": {g: [t.name for t in groups[g]] for g in order},
                       "needs_approval": approval})

