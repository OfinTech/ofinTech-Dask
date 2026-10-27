"""Memory page: list, add, approve proposals, edit, pin, archive, delete."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from ..memory import CATEGORIES
from ..web import Ctx, Router

router = Router()
Category = Literal["fact", "preference", "policy", "mapping", "contact", "deadline"]
assert set(CATEGORIES) == set(Category.__args__)  # type: ignore[attr-defined]


@router.get("/api/w/{ws}/memories", role="viewer")
async def list_memories(ctx: Ctx) -> dict[str, Any]:
    q = ctx.request.query_params
    rows = ctx.engine.memory.list(ctx.ws_id, ctx.user_id, q.get("status") or None, q.get("q") or None)
    counts = {r["status"]: r["n"] for r in ctx.engine.db.all(
        "SELECT status, COUNT(*) AS n FROM memories m WHERE workspace_id = ? AND (scope = 'workspace' OR user_id = ?) "
        "GROUP BY status", (ctx.ws_id, ctx.user_id))}
    return {"memories": rows, "counts": counts}


class MemoryIn(BaseModel):
    content: str = Field(min_length=1, max_length=2000)
    scope: Literal["workspace", "personal"] = "workspace"
    category: Category = "fact"
    pinned: bool = False


@router.post("/api/w/{ws}/memories", role="viewer", body=MemoryIn)
async def add_memory(ctx: Ctx) -> dict[str, Any]:
    b: MemoryIn = ctx.body
    return ctx.engine.memory.add(ctx.ws_id, ctx.user_id, ctx.role or "viewer", b.content, scope=b.scope,
                                 category=b.category, pinned=b.pinned)


class DecideIn(BaseModel):
    approve: bool
    content: str | None = Field(default=None, max_length=2000)


@router.post("/api/w/{ws}/memories/{mid}/decide", role="viewer", body=DecideIn)
async def decide(ctx: Ctx) -> dict[str, Any]:
    return ctx.engine.memory.decide(ctx.ws_id, ctx.user_id, ctx.role or "viewer", ctx.request.path_params["mid"],
                                    approve=ctx.body.approve, content=ctx.body.content)


class MemoryPatch(BaseModel):
    content: str | None = Field(default=None, max_length=2000)
    category: Category | None = None
    pinned: bool | None = None
    status: Literal["active", "archived"] | None = None


@router.patch("/api/w/{ws}/memories/{mid}", role="viewer", body=MemoryPatch)
async def edit(ctx: Ctx) -> dict[str, Any]:
    return ctx.engine.memory.edit(ctx.ws_id, ctx.user_id, ctx.role or "viewer", ctx.request.path_params["mid"],
                                  ctx.body.model_dump(exclude_unset=True))


@router.delete("/api/w/{ws}/memories/{mid}", role="viewer")
async def delete(ctx: Ctx) -> None:
    ctx.engine.memory.delete(ctx.ws_id, ctx.user_id, ctx.role or "viewer", ctx.request.path_params["mid"])
