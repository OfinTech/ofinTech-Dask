"""Cost centres: rules over the general ledger, reports, the lines behind each figure, and budgets from a file."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from ..web import Ctx, Router

router = Router()
ISO = r"^\d{4}-\d{2}-\d{2}$"


@router.get("/api/w/{ws}/cost-centres", role="viewer")
async def list_cost_centres(ctx: Ctx) -> dict[str, Any]:
    return {"cost_centres": await ctx.engine.costcentres.summaries(ctx.ws_id),
            "ledgers": ctx.engine.costcentres.ledgers(ctx.ws_id)}


@router.get("/api/w/{ws}/cost-centres/ledgers/{did}", role="viewer")
async def ledger_values(ctx: Ctx) -> dict[str, Any]:
    return ctx.engine.costcentres.ledger_values(ctx.ws_id, ctx.request.path_params["did"])


class CostCentreIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    code: str | None = Field(default=None, max_length=40)
    description: str | None = Field(default=None, max_length=1000)
    dataset_id: str = Field(min_length=1, max_length=40)
    start_date: str | None = Field(default=None, pattern=ISO)
    end_date: str | None = Field(default=None, pattern=ISO)
    rules: dict[str, Any]
    include_assets: bool = False
    budget_amount: float | None = Field(default=None, ge=0, le=1e13)
    burn_months: int = Field(default=3, ge=1, le=12)


@router.post("/api/w/{ws}/cost-centres", role="editor", body=CostCentreIn)
async def create(ctx: Ctx) -> dict[str, Any]:
    values = ctx.body.model_dump()
    values["name"] = values["name"].strip()
    return ctx.engine.costcentres.create(ctx.ws_id, ctx.user_id, values)


class PreviewIn(BaseModel):
    dataset_id: str = Field(min_length=1, max_length=40)
    rules: dict[str, Any]
    include_assets: bool = False
    start_date: str | None = Field(default=None, pattern=ISO)


@router.post("/api/w/{ws}/cost-centres/preview", role="viewer", body=PreviewIn)
async def preview(ctx: Ctx) -> dict[str, Any]:
    b: PreviewIn = ctx.body
    return await ctx.engine.costcentres.preview(ctx.ws_id, b.dataset_id, b.rules, include_assets=b.include_assets,
                                                start_date=b.start_date)


@router.get("/api/w/{ws}/cost-centres/{cid}", role="viewer")
async def get(ctx: Ctx) -> dict[str, Any]:
    return ctx.engine.costcentres.get(ctx.ws_id, ctx.request.path_params["cid"])


class CostCentrePatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    code: str | None = Field(default=None, max_length=40)
    description: str | None = Field(default=None, max_length=1000)
    status: Literal["active", "closed"] | None = None
    dataset_id: str | None = Field(default=None, min_length=1, max_length=40)
    start_date: str | None = Field(default=None, pattern=ISO)
    end_date: str | None = Field(default=None, pattern=ISO)
    rules: dict[str, Any] | None = None
    note: str | None = Field(default=None, max_length=300)
    include_assets: bool | None = None
    budget_amount: float | None = Field(default=None, ge=0, le=1e13)
    burn_months: int | None = Field(default=None, ge=1, le=12)


@router.patch("/api/w/{ws}/cost-centres/{cid}", role="editor", body=CostCentrePatch)
async def update(ctx: Ctx) -> dict[str, Any]:
    return ctx.engine.costcentres.update(ctx.ws_id, ctx.user_id, ctx.request.path_params["cid"],
                                         ctx.body.model_dump(exclude_unset=True))


@router.delete("/api/w/{ws}/cost-centres/{cid}", role="editor")
async def delete(ctx: Ctx) -> None:
    ctx.engine.costcentres.delete(ctx.ws_id, ctx.user_id, ctx.request.path_params["cid"])


@router.get("/api/w/{ws}/cost-centres/{cid}/report", role="viewer")
async def report(ctx: Ctx) -> dict[str, Any]:
    return await ctx.engine.costcentres.report(ctx.ws_id, ctx.request.path_params["cid"],
                                               ctx.request.query_params.get("as_of") or None)


@router.get("/api/w/{ws}/cost-centres/{cid}/lines", role="viewer")
async def lines(ctx: Ctx) -> dict[str, Any]:
    q = ctx.request.query_params
    return await ctx.engine.costcentres.matched(ctx.ws_id, ctx.request.path_params["cid"], q.get("kind") or "matched",
                                                q.get("as_of") or None, max(int(q.get("offset", 0) or 0), 0),
                                                int(q.get("limit", 200) or 200))


class BudgetImportIn(BaseModel):
    file_id: str = Field(min_length=1, max_length=40)
    sheet: str | None = Field(default=None, max_length=120)


@router.post("/api/w/{ws}/cost-centres/{cid}/budget/import", role="editor", body=BudgetImportIn)
async def import_budget(ctx: Ctx) -> dict[str, Any]:
    return await ctx.engine.costcentres.import_budget(ctx.ws_id, ctx.user_id, ctx.request.path_params["cid"],
                                                      ctx.body.file_id, ctx.body.sheet)


class BudgetLineIn(BaseModel):
    id: str | None = Field(default=None, max_length=40)
    label: str = Field(min_length=1, max_length=200)
    amount: float = Field(ge=-1e13, le=1e13)
    accounts: list[str] = Field(default_factory=list, max_length=50)


class BudgetLinesIn(BaseModel):
    lines: list[BudgetLineIn] = Field(max_length=500)


@router.put("/api/w/{ws}/cost-centres/{cid}/budget/lines", role="editor", body=BudgetLinesIn)
async def set_budget_lines(ctx: Ctx) -> dict[str, Any]:
    return ctx.engine.costcentres.set_budget_lines(ctx.ws_id, ctx.user_id, ctx.request.path_params["cid"],
                                                   [ln.model_dump() for ln in ctx.body.lines])
