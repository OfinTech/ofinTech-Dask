"""Financial analysis: KPI scorecard and settings, account roles, forecasts."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from ..errors import Invalid
from ..web import Ctx, Router

router = Router()
MONTH = r"^\d{4}-\d{2}$"


@router.get("/api/w/{ws}/analysis", role="viewer")
async def scorecard(ctx: Ctx) -> dict[str, Any]:
    q = ctx.request.query_params
    return await ctx.engine.analysis.scorecard(ctx.ws_id, q.get("month") or None, q.get("ledger") or None)


class LedgerIn(BaseModel):
    dataset_id: str = Field(min_length=1, max_length=40)


@router.put("/api/w/{ws}/analysis/ledger", role="admin", body=LedgerIn)
async def choose_ledger(ctx: Ctx) -> None:
    ctx.engine.analysis.choose_ledger(ctx.ws_id, ctx.user_id, ctx.body.dataset_id)


@router.get("/api/w/{ws}/analysis/kpis/{kid}", role="viewer")
async def kpi_detail(ctx: Ctx) -> dict[str, Any]:
    return await ctx.engine.analysis.detail(ctx.ws_id, ctx.request.path_params["kid"],
                                            ctx.request.query_params.get("month") or None)


class KpiIn(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    category: str | None = Field(default=None, max_length=40)
    formula: str | None = Field(default=None, max_length=400)
    unit: str | None = Field(default=None, max_length=20)
    direction: Literal["up", "down"] | None = None
    target: float | None = Field(default=None, ge=-1e13, le=1e13)
    warn: float | None = Field(default=None, ge=-1e13, le=1e13)
    enabled: bool | None = None


@router.put("/api/w/{ws}/analysis/kpis/{kid}", role="admin", body=KpiIn)
async def set_kpi(ctx: Ctx) -> dict[str, Any]:
    kid = ctx.request.path_params["kid"]
    if not kid.replace("_", "").isalnum() or len(kid) > 40:
        raise Invalid("A KPI id uses letters, digits and underscores.")
    return ctx.engine.analysis.set_kpi(ctx.ws_id, ctx.user_id, kid.lower(), ctx.body.model_dump(exclude_unset=True))


@router.delete("/api/w/{ws}/analysis/kpis/{kid}", role="admin")
async def reset_kpi(ctx: Ctx) -> None:
    ctx.engine.analysis.reset_kpi(ctx.ws_id, ctx.user_id, ctx.request.path_params["kid"])


@router.get("/api/w/{ws}/analysis/roles", role="viewer")
async def roles(ctx: Ctx) -> dict[str, Any]:
    return ctx.engine.analysis.roles(ctx.ws_id, ctx.request.query_params.get("ledger") or None)


class RoleItem(BaseModel):
    key: str = Field(min_length=1, max_length=300)
    role: str = Field(min_length=1, max_length=40)


class RolesIn(BaseModel):
    dataset_id: str | None = Field(default=None, max_length=40)
    items: list[RoleItem] = Field(min_length=1, max_length=5000)


@router.put("/api/w/{ws}/analysis/roles", role="editor", body=RolesIn)
async def set_roles(ctx: Ctx) -> dict[str, Any]:
    return ctx.engine.analysis.set_roles(ctx.ws_id, ctx.user_id, ctx.body.dataset_id,
                                         [i.model_dump() for i in ctx.body.items])


@router.get("/api/w/{ws}/forecasts", role="viewer")
async def list_forecasts(ctx: Ctx) -> list[dict[str, Any]]:
    return ctx.engine.analysis.list_forecasts(ctx.ws_id)


class ForecastIn(BaseModel):
    name: str = Field(default="Forecast", min_length=1, max_length=120)
    scenario: str = Field(default="Base", min_length=1, max_length=60)
    dataset_id: str | None = Field(default=None, max_length=40)
    start_month: str | None = Field(default=None, pattern=MONTH)
    months: int = Field(default=12, ge=1, le=36)
    assumptions: dict[str, Any] | None = None
    note: str | None = Field(default=None, max_length=1000)


@router.post("/api/w/{ws}/forecasts", role="editor", body=ForecastIn)
async def create(ctx: Ctx) -> dict[str, Any]:
    from ..platform.plans import require_feature

    require_feature(ctx.engine, ctx.ws_id, "financial_intelligence")
    return ctx.engine.analysis.create_forecast(ctx.ws_id, ctx.user_id, ctx.body.model_dump())


@router.get("/api/w/{ws}/forecasts/{fid}", role="viewer")
async def view(ctx: Ctx) -> dict[str, Any]:
    return await ctx.engine.analysis.forecast_view(ctx.ws_id, ctx.request.path_params["fid"])


class ForecastPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    scenario: str | None = Field(default=None, min_length=1, max_length=60)
    start_month: str | None = Field(default=None, pattern=MONTH)
    months: int | None = Field(default=None, ge=1, le=36)
    assumptions: dict[str, Any] | None = None
    note: str | None = Field(default=None, max_length=1000)


@router.patch("/api/w/{ws}/forecasts/{fid}", role="editor", body=ForecastPatch)
async def update(ctx: Ctx) -> dict[str, Any]:
    ctx.engine.analysis.update_forecast(ctx.ws_id, ctx.user_id, ctx.request.path_params["fid"],
                                        ctx.body.model_dump(exclude_unset=True))
    return await ctx.engine.analysis.forecast_view(ctx.ws_id, ctx.request.path_params["fid"])


@router.post("/api/w/{ws}/forecasts/{fid}/preview", role="viewer", body=ForecastPatch)
async def preview(ctx: Ctx) -> dict[str, Any]:
    return await ctx.engine.analysis.forecast_view(ctx.ws_id, ctx.request.path_params["fid"],
                                                   ctx.body.model_dump(exclude_unset=True))


class StatusIn(BaseModel):
    status: Literal["draft", "submitted", "approved", "archived"]
    note: str | None = Field(default=None, max_length=1000)


@router.post("/api/w/{ws}/forecasts/{fid}/status", role="editor", body=StatusIn)
async def set_status(ctx: Ctx) -> dict[str, Any]:
    ctx.engine.analysis.transition(ctx.ws_id, ctx.user_id, ctx.role or "viewer", ctx.request.path_params["fid"],
                                   ctx.body.status, ctx.body.note)
    return await ctx.engine.analysis.forecast_view(ctx.ws_id, ctx.request.path_params["fid"])


@router.post("/api/w/{ws}/forecasts/{fid}/plan", role="admin")
async def make_plan(ctx: Ctx) -> dict[str, Any]:
    return ctx.engine.analysis.make_plan(ctx.ws_id, ctx.user_id, ctx.request.path_params["fid"])


class CopyIn(BaseModel):
    scenario: str | None = Field(default=None, min_length=1, max_length=60)


@router.post("/api/w/{ws}/forecasts/{fid}/copy", role="editor", body=CopyIn)
async def copy(ctx: Ctx) -> dict[str, Any]:
    return ctx.engine.analysis.copy_forecast(ctx.ws_id, ctx.user_id, ctx.request.path_params["fid"], ctx.body.scenario)


@router.delete("/api/w/{ws}/forecasts/{fid}", role="editor")
async def delete(ctx: Ctx) -> None:
    ctx.engine.analysis.delete_forecast(ctx.ws_id, ctx.user_id, ctx.role or "viewer", ctx.request.path_params["fid"])


@router.get("/api/w/{ws}/forecasts/{fid}/compare", role="viewer")
async def compare(ctx: Ctx) -> list[dict[str, Any]]:
    f = ctx.engine.analysis._forecast(ctx.ws_id, ctx.request.path_params["fid"])
    return await ctx.engine.analysis.compare(ctx.ws_id, f["group_id"])
