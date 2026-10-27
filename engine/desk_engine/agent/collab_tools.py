"""Agent tools that reach colleagues and start workflows (all require approval)."""

from __future__ import annotations

from typing import Any

from ..db import loads
from .tools import ToolContext, ToolOutput, registry, schema


def _has_workflows(ctx: ToolContext) -> bool:
    return ctx.engine.db.one("SELECT 1 FROM workflows WHERE workspace_id = ? AND enabled = 1 LIMIT 1",
                             (ctx.ws_id,)) is not None


@registry.tool("list_workflows", "List the workspace's workflows (id, name, trigger).", schema({}),
               category="workflows", available=_has_workflows)
async def _list(ctx: ToolContext, a: dict[str, Any]) -> ToolOutput:
    rows = ctx.engine.db.all("SELECT id, name, description, definition FROM workflows WHERE workspace_id = ? AND "
                             "enabled = 1 ORDER BY name", (ctx.ws_id,))
    lines = [f"- {r['name']} (id {r['id']}, trigger {loads(r['definition'], {}).get('trigger', {}).get('type')}): "
             f"{r['description']}" for r in rows]
    return ToolOutput("\n".join(lines) or "No workflows.", [], rows)


@registry.tool("run_workflow", "Start one of the workspace's workflows now.",
               schema({"workflow_id": {"type": "string"}, "input": {"type": "string",
                                                                    "description": "text passed as {{trigger.text}}"}},
                      ["workflow_id"]),
               effect="write", category="workflows", available=_has_workflows,
               summarize=lambda a: f"Run workflow {a.get('workflow_id')}" + (f" with input: {a['input'][:200]}"
                                                                              if a.get("input") else ""))
async def _run(ctx: ToolContext, a: dict[str, Any]) -> ToolOutput:
    wf = ctx.engine.workflows.get(ctx.ws_id, a["workflow_id"])
    run_id = ctx.engine.workflows.start(ctx.workspace, wf, {"type": "agent", "text": a.get("input") or ""}, ctx.user_id)
    return ToolOutput(f"Started workflow '{wf['name']}' (run {run_id}).",
                      [{"type": "link", "kind": "workflow_run", "id": run_id, "workflow_id": wf["id"],
                        "label": wf["name"]}], {"run_id": run_id})


def _members(ctx: ToolContext) -> list[dict[str, Any]]:
    return ctx.engine.db.all("SELECT u.id, u.email, u.name FROM users u JOIN members m ON m.user_id = u.id "
                             "WHERE m.workspace_id = ?", (ctx.ws_id,))


@registry.tool("message_colleague", "Send a short message to a workspace member through the app's inbox and, if they "
               "linked one, Telegram or WhatsApp.",
               schema({"member": {"type": "string", "description": "member e-mail or name"},
                       "message": {"type": "string"},
                       "channel": {"type": "string", "enum": ["in_app", "telegram", "whatsapp"], "default": "in_app"}},
                      ["member", "message"]),
               effect="write", category="collaboration",
               summarize=lambda a: f"Message {a.get('member')} via {a.get('channel', 'in_app')}: {a.get('message', '')[:500]}")
async def _message(ctx: ToolContext, a: dict[str, Any]) -> ToolOutput:
    needle = a["member"].strip().lower()
    matches = [m for m in _members(ctx) if needle in (m["email"].lower(), m["name"].lower())]
    if len(matches) != 1:
        return ToolOutput("Member not found (or ambiguous). Members: " + ", ".join(m["name"] for m in _members(ctx)))
    member = matches[0]
    channel = a.get("channel") or "in_app"
    sender = ctx.engine.db.one("SELECT name FROM users WHERE id = ?", (ctx.user_id,))
    text = f"From {sender['name'] if sender else 'a colleague'}: {a['message']}"
    if channel == "in_app":
        ctx.engine.notify(ctx.ws_id, member["id"], "message", text[:300], f"/chat/{ctx.thread_id}" if ctx.thread_id else None)
        return ToolOutput(f"Sent to {member['name']} in the app.")
    n = await ctx.engine.channels.send_to_member(ctx.ws_id, member["id"], channel, text)
    if not n:
        return ToolOutput(f"{member['name']} has not linked {channel}. Sent nothing.")
    return ToolOutput(f"Sent to {member['name']} on {channel}.")
