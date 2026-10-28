"""Webhooks under Connections: incoming endpoints and outgoing event subscriptions, with their delivery logs."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from ..web import Ctx, Router
from ..webhooks import EVENT_GROUPS

router = Router()


@router.get("/api/w/{ws}/webhooks", role="viewer")
async def list_webhooks(ctx: Ctx) -> dict[str, Any]:
    return {"webhooks": ctx.engine.webhooks.list(ctx.ws_id), "event_groups": EVENT_GROUPS,
            "workflows": ctx.engine.db.all("SELECT id, name, enabled FROM workflows WHERE workspace_id = ? ORDER BY name",
                                           (ctx.ws_id,))}


class WebhookIn(BaseModel):
    direction: Literal["in", "out"]
    name: str = Field(min_length=1, max_length=120)
    url: str | None = Field(default=None, max_length=500)
    events: list[str] = Field(default_factory=list, max_length=20)
    workflow_id: str | None = Field(default=None, max_length=40)


@router.post("/api/w/{ws}/webhooks", role="admin", body=WebhookIn)
async def create(ctx: Ctx) -> dict[str, Any]:
    b: WebhookIn = ctx.body
    return ctx.engine.webhooks.create(ctx.ws_id, ctx.user_id, direction=b.direction, name=b.name, url=b.url,
                                      events=b.events, workflow_id=b.workflow_id)


class WebhookPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    enabled: bool | None = None
    url: str | None = Field(default=None, max_length=500)
    events: list[str] | None = Field(default=None, max_length=20)
    workflow_id: str | None = Field(default=None, max_length=40)


@router.patch("/api/w/{ws}/webhooks/{hid}", role="admin", body=WebhookPatch)
async def update(ctx: Ctx) -> dict[str, Any]:
    return ctx.engine.webhooks.update(ctx.ws_id, ctx.user_id, ctx.request.path_params["hid"],
                                      ctx.body.model_dump(exclude_unset=True))


@router.delete("/api/w/{ws}/webhooks/{hid}", role="admin")
async def delete(ctx: Ctx) -> None:
    ctx.engine.webhooks.delete(ctx.ws_id, ctx.user_id, ctx.request.path_params["hid"])


@router.post("/api/w/{ws}/webhooks/{hid}/secret", role="admin")
async def rotate(ctx: Ctx) -> dict[str, Any]:
    return {"secret": ctx.engine.webhooks.rotate_secret(ctx.ws_id, ctx.user_id, ctx.request.path_params["hid"])}


@router.post("/api/w/{ws}/webhooks/{hid}/test", role="admin")
async def test(ctx: Ctx) -> dict[str, Any]:
    return ctx.engine.webhooks.send_test(ctx.ws_id, ctx.user_id, ctx.request.path_params["hid"])


@router.get("/api/w/{ws}/webhooks/{hid}/deliveries", role="viewer")
async def deliveries(ctx: Ctx) -> list[dict[str, Any]]:
    try:
        limit = int(ctx.request.query_params.get("limit", 50))
    except ValueError:
        limit = 50
    return ctx.engine.webhooks.deliveries(ctx.ws_id, ctx.request.path_params["hid"], limit)


@router.post("/api/w/{ws}/webhooks/{hid}/deliveries/{did}/redeliver", role="admin")
async def redeliver(ctx: Ctx) -> None:
    ctx.engine.webhooks.redeliver(ctx.ws_id, ctx.user_id, ctx.request.path_params["hid"], ctx.request.path_params["did"])
