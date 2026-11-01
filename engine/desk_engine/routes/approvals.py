"""The approvals inbox: actions proposed by the agent or by workflows."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from ..db import loads
from ..web import Ctx, Router

router = Router()


@router.get("/api/w/{ws}/approvals", role="viewer")
async def list_approvals(ctx: Ctx) -> list[dict[str, Any]]:
    status = ctx.request.query_params.get("status", "pending")
    params: list[Any] = [ctx.ws_id]
    sql = ("SELECT a.*, u.name AS requested_by_name, d.name AS decided_by_name FROM approvals a "
           "LEFT JOIN users u ON u.id = a.requested_by LEFT JOIN users d ON d.id = a.decided_by WHERE a.workspace_id = ?")
    if status != "all":
        sql += " AND a.status = ?"
        params.append(status)
    rows = ctx.engine.db.all(sql + " ORDER BY a.created_at DESC LIMIT 200", params)
    for r in rows:
        r["arguments"] = loads(r["arguments"], {})
        r["result"] = loads(r["result"], None)
        if r["source"] == "agent":
            run = ctx.engine.db.one("SELECT thread_id FROM agent_runs WHERE id = ?", (r["source_id"],))
            r["thread_id"] = run["thread_id"] if run else None
        else:
            run = ctx.engine.db.one("SELECT workflow_id FROM workflow_runs WHERE id = ?", (r["source_id"],))
            r["workflow_id"] = run["workflow_id"] if run else None
    return rows


class DecideIn(BaseModel):
    approve: bool
    note: str | None = Field(default=None, max_length=1000)


@router.post("/api/w/{ws}/approvals/{aid}/decide", role="viewer", body=DecideIn)
async def decide(ctx: Ctx) -> dict[str, Any]:
    from ..approvals import INTERNAL_TOOLS

    appr = ctx.engine.db.one("SELECT tool FROM approvals WHERE id = ? AND workspace_id = ?",
                             (ctx.request.path_params["aid"], ctx.ws_id))
    if appr and ctx.body.approve and appr["tool"] not in INTERNAL_TOOLS:
        ctx.require_fresh()   # approving an action that leaves the workspace is a high-risk action of its own
    return await ctx.engine.agent.decide(ctx.workspace, ctx.request.path_params["aid"], ctx.user_id, ctx.role or "viewer",
                                         ctx.body.approve, ctx.body.note)
