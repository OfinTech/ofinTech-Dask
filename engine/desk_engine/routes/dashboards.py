"""Dashboards."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from ..data.dashboards import TEMPLATES, DashboardIn, get_dashboard, render_dashboard, save_dashboard, template_widgets
from ..data.query import run_query
from ..data.store import get_dataset
from ..errors import NotFound
from ..web import Ctx, Router

router = Router()


@router.get("/api/w/{ws}/dashboards", role="viewer")
async def list_dashboards(ctx: Ctx) -> list[dict[str, Any]]:
    rows = ctx.engine.db.all("SELECT d.id, d.name, d.description, d.updated_at, u.name AS created_by_name, "
                             "json_array_length(d.widgets) AS widget_count FROM dashboards d LEFT JOIN users u ON "
                             "u.id = d.created_by WHERE d.workspace_id = ? ORDER BY d.updated_at DESC", (ctx.ws_id,))
    return rows


@router.get("/api/dashboard-templates")
async def templates(ctx: Ctx) -> dict[str, Any]:
    return TEMPLATES


class FromTemplateIn(BaseModel):
    template: str
    dataset_id: str
    name: str | None = Field(default=None, max_length=120)


@router.post("/api/w/{ws}/dashboards/from-template", role="editor", body=FromTemplateIn)
async def from_template(ctx: Ctx) -> dict[str, Any]:
    b: FromTemplateIn = ctx.body
    ds = get_dataset(ctx.engine, ctx.ws_id, b.dataset_id)
    widgets = template_widgets(b.template, ds)
    name = b.name or f"{TEMPLATES.get(b.template, {}).get('label', 'Dashboard')} ({ds['name']})"
    dash = save_dashboard(ctx.engine, ctx.ws_id, DashboardIn(name=name[:120], widgets=widgets), user_id=ctx.user_id)
    ctx.audit("dashboard.create", dash["id"], {"template": b.template})
    return dash


@router.post("/api/w/{ws}/dashboards", role="editor", body=DashboardIn)
async def create(ctx: Ctx) -> dict[str, Any]:
    for w in ctx.body.widgets:
        run_query(ctx.engine, ctx.workspace, w.query)
    dash = save_dashboard(ctx.engine, ctx.ws_id, ctx.body, user_id=ctx.user_id)
    ctx.audit("dashboard.create", dash["id"])
    return dash


@router.get("/api/w/{ws}/dashboards/{did}", role="viewer")
async def view(ctx: Ctx) -> dict[str, Any]:
    return render_dashboard(ctx.engine, ctx.workspace, ctx.request.path_params["did"])


@router.put("/api/w/{ws}/dashboards/{did}", role="editor", body=DashboardIn)
async def update(ctx: Ctx) -> dict[str, Any]:
    did = ctx.request.path_params["did"]
    get_dashboard(ctx.engine, ctx.ws_id, did)
    for w in ctx.body.widgets:
        run_query(ctx.engine, ctx.workspace, w.query)
    dash = save_dashboard(ctx.engine, ctx.ws_id, ctx.body, user_id=ctx.user_id, dashboard_id=did)
    ctx.audit("dashboard.update", did)
    return dash


@router.delete("/api/w/{ws}/dashboards/{did}", role="editor")
async def delete(ctx: Ctx) -> None:
    did = ctx.request.path_params["did"]
    if not ctx.engine.db.execute("DELETE FROM dashboards WHERE id = ? AND workspace_id = ?", (did, ctx.ws_id)):
        raise NotFound("Dashboard not found.")
    ctx.audit("dashboard.delete", did)
