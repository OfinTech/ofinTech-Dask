"""Conversations with the agent, streamed to the app with server-sent events."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any, Literal

from pydantic import BaseModel, Field
from starlette.responses import StreamingResponse

from ..db import dumps, loads, new_id, now
from ..errors import Forbidden, Invalid, NotFound
from ..web import Ctx, Router

router = Router()
KEEP_ALIVE_SECONDS = 15  # how often an idle event stream sends a comment, and re-checks its authorisation


def thread_for(ctx: Ctx, thread_id: str) -> dict[str, Any]:
    t = ctx.engine.db.one("SELECT * FROM threads WHERE id = ? AND workspace_id = ?", (thread_id, ctx.ws_id))
    if t is None or (t["visibility"] == "private" and t["created_by"] != ctx.user_id):
        raise NotFound("Conversation not found.")
    return t


def _thread_out(t: dict[str, Any]) -> dict[str, Any]:
    return {**t, "skills": loads(t["skills"], []), "channel": loads(t["channel"], None)}


@router.get("/api/w/{ws}/threads", role="viewer")
async def list_threads(ctx: Ctx) -> list[dict[str, Any]]:
    q = ctx.request.query_params.get("q", "").strip()
    sql = ("SELECT t.*, u.name AS created_by_name FROM threads t LEFT JOIN users u ON u.id = t.created_by "
           "WHERE t.workspace_id = ? AND (t.visibility = 'workspace' OR t.created_by = ?)")
    params: list[Any] = [ctx.ws_id, ctx.user_id]
    if q:
        sql += " AND t.title LIKE ?"
        params.append(f"%{q[:100]}%")
    rows = ctx.engine.db.all(sql + " ORDER BY t.updated_at DESC LIMIT 200", params)
    return [_thread_out(r) for r in rows]


class ThreadIn(BaseModel):
    title: str | None = Field(default=None, max_length=120)
    provider_id: str | None = None
    model: str | None = Field(default=None, max_length=200)
    skills: list[str] = Field(default_factory=list, max_length=10)
    visibility: Literal["workspace", "private"] = "workspace"


@router.post("/api/w/{ws}/threads", role="viewer", body=ThreadIn)
async def create_thread(ctx: Ctx) -> dict[str, Any]:
    b: ThreadIn = ctx.body
    if b.provider_id and not ctx.engine.db.one("SELECT 1 FROM llm_providers WHERE id = ? AND workspace_id = ?",
                                               (b.provider_id, ctx.ws_id)):
        raise NotFound("Model provider not found.")
    tid = new_id("t_")
    ts = now()
    ctx.engine.db.insert("threads", {"id": tid, "workspace_id": ctx.ws_id, "title": (b.title or "New conversation").strip(),
                                     "created_by": ctx.user_id, "visibility": b.visibility, "provider_id": b.provider_id,
                                     "model": b.model, "skills": dumps(b.skills), "created_at": ts, "updated_at": ts})
    return _thread_out(thread_for(ctx, tid))


@router.get("/api/w/{ws}/threads/{tid}", role="viewer")
async def get_thread(ctx: Ctx) -> dict[str, Any]:
    t = thread_for(ctx, ctx.request.path_params["tid"])
    msgs = ctx.engine.db.all("SELECT m.*, u.name AS author_name FROM messages m LEFT JOIN users u ON u.id = m.author_id "
                             "WHERE m.thread_id = ? ORDER BY m.created_at LIMIT 2000", (t["id"],))
    for m in msgs:
        m["payload"] = loads(m["payload"], {})
    run = ctx.engine.db.one("SELECT id, status, error FROM agent_runs WHERE thread_id = ? ORDER BY created_at DESC LIMIT 1",
                            (t["id"],))
    approvals = []
    if run:
        approvals = [ctx.engine.agent.approval_out(a["id"]) for a in ctx.engine.db.all(
            "SELECT id FROM approvals WHERE source = 'agent' AND source_id IN (SELECT id FROM agent_runs WHERE thread_id = ?) "
            "ORDER BY created_at", (t["id"],))]
    return {"thread": _thread_out(t), "messages": msgs, "run": run, "approvals": approvals}


class ThreadPatch(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=120)
    provider_id: str | None = None
    model: str | None = None
    skills: list[str] | None = Field(default=None, max_length=10)
    visibility: Literal["workspace", "private"] | None = None


@router.patch("/api/w/{ws}/threads/{tid}", role="viewer", body=ThreadPatch)
async def patch_thread(ctx: Ctx) -> dict[str, Any]:
    t = thread_for(ctx, ctx.request.path_params["tid"])
    if t["created_by"] != ctx.user_id:
        ctx.require("editor")
    values = {k: v for k, v in ctx.body.model_dump(exclude_unset=True).items()}
    if "skills" in values:
        values["skills"] = dumps(values["skills"] or [])
    if values.get("provider_id") and not ctx.engine.db.one(
            "SELECT 1 FROM llm_providers WHERE id = ? AND workspace_id = ?", (values["provider_id"], ctx.ws_id)):
        raise NotFound("Model provider not found.")
    if values:
        ctx.engine.db.update("threads", t["id"], values, workspace_id=ctx.ws_id)
    return _thread_out(thread_for(ctx, t["id"]))


@router.delete("/api/w/{ws}/threads/{tid}", role="viewer")
async def delete_thread(ctx: Ctx) -> None:
    t = thread_for(ctx, ctx.request.path_params["tid"])
    if t["created_by"] != ctx.user_id:
        ctx.require("admin")
    if ctx.engine.agent.thread_busy(t["id"]):
        task = ctx.engine.agent.tasks.get(t["id"])
        if task:
            task.cancel()
    ctx.engine.db.execute("DELETE FROM threads WHERE id = ? AND workspace_id = ?", (t["id"], ctx.ws_id))
    ctx.audit("thread.delete", t["id"])


class MessageIn(BaseModel):
    text: str = Field(default="", max_length=20000)
    attachments: list[str] = Field(default_factory=list, max_length=10)


@router.post("/api/w/{ws}/threads/{tid}/messages", role="viewer", body=MessageIn)
async def send_message(ctx: Ctx) -> dict[str, Any]:
    from ..platform.plans import check_ai_allowance, require_feature

    require_feature(ctx.engine, ctx.ws_id, "ask_finance")
    check_ai_allowance(ctx.engine, ctx.ws_id)
    t = thread_for(ctx, ctx.request.path_params["tid"])
    if t["visibility"] == "workspace" and t["created_by"] != ctx.user_id and ctx.role == "viewer":
        raise Forbidden("Viewers can only write in their own conversations.")
    text = ctx.body.text.strip()
    docs: list[dict[str, Any]] = []
    if ctx.body.attachments:
        from ..agent.harness import attachment_note

        docs = await ctx.engine.knowledge.wait_ready(ctx.ws_id, list(dict.fromkeys(ctx.body.attachments)))
        text = (text or "Please look at the attached file" + ("s." if len(docs) > 1 else ".")) + attachment_note(docs)
    if not text:
        raise Invalid("Write a message or attach a file.")
    if t["title"] == "New conversation":
        first = ctx.body.text.strip().split("\n")[0][:80] or (docs[0]["name"][:80] if docs else "")
        ctx.engine.db.update("threads", t["id"], {"title": first or "New conversation"})
    run_id = ctx.engine.agent.start_turn(ctx.workspace, ctx.user_id, t["id"], text, attachments=[
        {"id": d["id"], "name": d["name"], "status": d["status"]} for d in docs] or None)
    return {"run_id": run_id}


@router.post("/api/w/{ws}/threads/{tid}/stop", role="viewer")
async def stop(ctx: Ctx) -> None:
    t = thread_for(ctx, ctx.request.path_params["tid"])
    task = ctx.engine.agent.tasks.get(t["id"])
    if task and not task.done():
        task.cancel()
    ctx.engine.db.execute("UPDATE agent_runs SET status = 'error', error = 'Stopped by the user.', updated_at = ? "
                          "WHERE thread_id = ? AND status IN ('running','waiting_approval')", (now(), t["id"]))
    ctx.engine.db.execute("UPDATE approvals SET status = 'expired' WHERE status = 'pending' AND source = 'agent' AND "
                          "source_id IN (SELECT id FROM agent_runs WHERE thread_id = ?)", (t["id"],))
    ctx.engine.events.publish(t["id"], {"type": "run", "run": {"status": "error", "error": "Stopped by the user."}})


@router.get("/api/w/{ws}/threads/{tid}/events", role="viewer")
async def events(ctx: Ctx) -> StreamingResponse:
    t = thread_for(ctx, ctx.request.path_params["tid"])
    hub = ctx.engine.events
    queue = hub.subscribe(t["id"])
    request = ctx.request

    engine = ctx.engine

    def still_allowed() -> bool:
        """The stream was authorised when it opened; a session that has ended or a membership that was removed
        since must close it, so the check is repeated at every keep-alive and before every event."""
        from ..errors import AppError
        from ..web import Ctx as _Ctx, authenticate, load_workspace

        try:
            user = authenticate(engine, request)
            c = _Ctx(request=request, engine=engine, user=user)
            load_workspace(c, ctx.ws_id, "viewer")
            thread_for(c, t["id"])
        except AppError:
            return False
        return True

    async def stream() -> AsyncIterator[bytes]:
        try:
            yield b": connected\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(queue.get(), KEEP_ALIVE_SECONDS)
                except TimeoutError:
                    if not still_allowed():
                        break
                    yield b": keep-alive\n\n"
                    continue
                if not still_allowed():
                    break
                yield f"data: {json.dumps(event, default=str)}\n\n".encode()
        finally:
            hub.unsubscribe(t["id"], queue)

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"cache-control": "no-cache", "x-accel-buffering": "no"})
