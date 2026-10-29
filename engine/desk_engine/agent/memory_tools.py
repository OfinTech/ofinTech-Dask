"""Agent tools for memory: propose (a person approves) and search."""

from __future__ import annotations

from typing import Any

from ..memory import CATEGORIES
from .tools import ToolContext, ToolOutput, registry, schema


def _interactive(ctx: ToolContext) -> bool:
    return ctx.source in {"chat", "channel"}


@registry.tool("propose_memory", "Propose saving one lasting fact, preference, policy, account mapping, contact or deadline "
               "to memory so it is available in later conversations. A person approves it in the app first.",
               schema({"content": {"type": "string", "description": "one short, self-contained sentence"},
                       "scope": {"type": "string", "enum": ["workspace", "personal"],
                                 "description": "workspace: for everyone; personal: only this user's preference"},
                       "category": {"type": "string", "enum": CATEGORIES},
                       "reason": {"type": "string", "description": "why it is worth remembering"},
                       "replaces": {"type": "string", "description": "id of a saved memory this corrects (shown in "
                                    "brackets in the memory list); it is archived when the new one is saved"}},
               ["content"]),
               category="memory", available=_interactive)
async def _propose(ctx: ToolContext, a: dict[str, Any]) -> ToolOutput:
    last = ctx.engine.db.one("SELECT id FROM messages WHERE thread_id = ? AND role = 'user' ORDER BY created_at DESC LIMIT 1",
                             (ctx.thread_id,)) if ctx.thread_id else None
    m = ctx.engine.memory.propose(ctx.ws_id, ctx.user_id, a["content"], scope=a.get("scope") or "workspace",
                                  category=a.get("category") or "fact", reason=a.get("reason"), thread_id=ctx.thread_id,
                                  message_id=last["id"] if last else None, replaces=a.get("replaces"))
    state = "is already saved" if m["status"] == "active" else "is waiting for the user to save or discard it"
    old = ctx.engine.db.one("SELECT content FROM memories WHERE id = ?", (m.get("replaces_id"),)) if m.get("replaces_id") else None
    card = {k: m[k] for k in ("id", "content", "scope", "category", "status")} | ({"replaces": old["content"]} if old else {})
    return ToolOutput(f"Memory proposal {state}: “{m['content']}”" + (f", replacing “{old['content']}”" if old else "")
                      + ". Mention briefly that you suggested saving it.", [{"type": "memory", "memory": card}])


@registry.tool("search_memory", "Search the saved memory of this workspace (and the user's personal memory).",
               schema({"query": {"type": "string"}}, ["query"]), category="memory")
async def _search(ctx: ToolContext, a: dict[str, Any]) -> ToolOutput:
    rows = ctx.engine.memory.list(ctx.ws_id, ctx.user_id, "active", a["query"])[:15]
    if not rows:
        return ToolOutput("No saved memory matches.")
    ctx.engine.memory.mark_used([r["id"] for r in rows])
    return ToolOutput("\n".join(f"- ({r['scope']}, {r['category']}) {r['content']}" for r in rows))
