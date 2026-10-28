"""Agent tool over incoming webhooks: what other systems sent (read-only)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from .tools import ToolContext, ToolOutput, registry, schema


def _has_incoming(ctx: ToolContext) -> bool:
    return ctx.engine.db.one("SELECT 1 FROM webhooks WHERE workspace_id = ? AND direction = 'in' LIMIT 1",
                             (ctx.ws_id,)) is not None


@registry.tool("webhook_events", "The latest deliveries received by the workspace's incoming webhooks (Connections → "
               "Webhooks): when they arrived, from which webhook, the event name, whether a workflow started, and the "
               "payload. Use it to answer what another system sent.",
               schema({"webhook": {"type": "string", "description": "webhook name or id; all when omitted"},
                       "limit": {"type": "integer", "description": "1–50, default 10"}}),
               category="workflows", available=_has_incoming)
async def _events(ctx: ToolContext, a: dict[str, Any]) -> ToolOutput:
    limit = min(max(int(a.get("limit") or 10), 1), 50)
    want = (a.get("webhook") or "").strip().lower()
    hooks = ctx.engine.db.all("SELECT id, name FROM webhooks WHERE workspace_id = ? AND direction = 'in'", (ctx.ws_id,))
    if want:
        hooks = [h for h in hooks if h["id"] == want or want in h["name"].lower()]
        if not hooks:
            return ToolOutput(f"No incoming webhook matches “{a.get('webhook')}”.")
    ids = [h["id"] for h in hooks]
    marks = ",".join("?" for _ in ids)
    rows = ctx.engine.db.all(f"SELECT d.*, w.name AS hook FROM webhook_deliveries d JOIN webhooks w ON w.id = d.webhook_id "
                             f"WHERE d.webhook_id IN ({marks}) AND d.direction = 'in' ORDER BY d.created_at DESC LIMIT ?",
                             (*ids, limit))
    if not rows:
        return ToolOutput("No deliveries have been received yet.")
    from ..db import loads

    lines = []
    for r in rows:
        when = datetime.fromtimestamp(r["created_at"], tz=UTC).strftime("%Y-%m-%d %H:%M UTC")
        payload = loads(r["payload"], None)
        text = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
        lines.append(f"- {when} · {r['hook']} · event {r['event'] or '—'} · {r['status']}"
                     + (f" (run {r['run_id']})" if r["run_id"] else "") + f"\n  {str(text)[:600]}")
    return ToolOutput("\n".join(lines), [], [{k: r[k] for k in ("id", "hook", "event", "status", "run_id", "created_at")}
                                             for r in rows])
