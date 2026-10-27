"""Comments with @mentions on conversations, dashboards, datasets and workflow runs."""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, Field

from ..db import dumps, loads, new_id, now
from ..errors import Forbidden, NotFound
from ..web import Ctx, Router

router = Router()
TargetType = Literal["thread", "dashboard", "dataset", "workflow_run", "document", "file"]
TABLES = {"thread": "threads", "dashboard": "dashboards", "dataset": "datasets", "workflow_run": "workflow_runs",
          "document": "documents", "file": "files"}
MENTION = re.compile(r"@\[([^\]]{1,80})\]\((u_[a-z0-9]+)\)")


def _check_target(ctx: Ctx, target_type: str, target_id: str) -> None:
    table = TABLES[target_type]
    row = ctx.engine.db.one(f"SELECT * FROM {table} WHERE id = ? AND workspace_id = ?", (target_id, ctx.ws_id))
    if row is None or (target_type == "thread" and row["visibility"] == "private" and row["created_by"] != ctx.user_id):
        raise NotFound("Not found.")


@router.get("/api/w/{ws}/comments", role="viewer")
async def list_comments(ctx: Ctx) -> list[dict[str, Any]]:
    tt = ctx.request.query_params.get("target_type", "")
    tid = ctx.request.query_params.get("target_id", "")
    if tt not in TABLES:
        raise NotFound("Unknown target.")
    _check_target(ctx, tt, tid)
    rows = ctx.engine.db.all("SELECT c.*, u.name AS author_name FROM comments c JOIN users u ON u.id = c.author_id WHERE "
                             "c.workspace_id = ? AND c.target_type = ? AND c.target_id = ? ORDER BY c.created_at",
                             (ctx.ws_id, tt, tid))
    for r in rows:
        r["mentions"] = loads(r["mentions"], [])
    return rows


class CommentIn(BaseModel):
    target_type: TargetType
    target_id: str
    body: str = Field(min_length=1, max_length=5000)


LINKS = {"thread": "/chat/{}", "dashboard": "/dashboards/{}", "dataset": "/data/{}", "workflow_run": "/workflows?run={}",
         "document": "/documents/{}", "file": "/files/{}"}


@router.post("/api/w/{ws}/comments", role="viewer", body=CommentIn)
async def add_comment(ctx: Ctx) -> dict[str, Any]:
    b: CommentIn = ctx.body
    _check_target(ctx, b.target_type, b.target_id)
    members = {m["user_id"] for m in ctx.engine.db.all("SELECT user_id FROM members WHERE workspace_id = ?", (ctx.ws_id,))}
    mentions = sorted({uid for _, uid in MENTION.findall(b.body) if uid in members and uid != ctx.user_id})
    cid = new_id("c_")
    ctx.engine.db.insert("comments", {"id": cid, "workspace_id": ctx.ws_id, "target_type": b.target_type,
                                      "target_id": b.target_id, "author_id": ctx.user_id, "body": b.body,
                                      "mentions": dumps(mentions), "created_at": now()})
    plain = MENTION.sub(lambda m: "@" + m.group(1), b.body)
    for uid in mentions:
        ctx.engine.notify(ctx.ws_id, uid, "mention", f"{ctx.user['name']} mentioned you: {plain[:200]}",
                          LINKS[b.target_type].format(b.target_id))
    return {"id": cid, "mentions": mentions}


@router.delete("/api/w/{ws}/comments/{cid}", role="viewer")
async def delete_comment(ctx: Ctx) -> None:
    row = ctx.engine.db.one("SELECT * FROM comments WHERE id = ? AND workspace_id = ?",
                            (ctx.request.path_params["cid"], ctx.ws_id))
    if row is None:
        raise NotFound("Comment not found.")
    if row["author_id"] != ctx.user_id and ctx.role not in {"admin", "owner"}:
        raise Forbidden("You can delete only your own comments.")
    ctx.engine.db.execute("DELETE FROM comments WHERE id = ?", (row["id"],))
