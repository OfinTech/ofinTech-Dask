"""Executive overview and its drill-down: figures → profit and loss → accounts → ledger lines → one entry.

Everything is read-only and needs the viewer role, like the datasets the figures come from. The reporting month
is a query parameter on every step so the path keeps its context."""

from __future__ import annotations

import asyncio
from typing import Any

from ..errors import Invalid
from ..web import Ctx, Router

router = Router()
MONTH = r"^\d{4}-(0[1-9]|1[0-2])$"


def _month(ctx: Ctx) -> str | None:
    import re

    m = ctx.request.query_params.get("month") or None
    if m is not None and not re.match(MONTH, m):
        raise Invalid("The month is written YYYY-MM.")
    return m


@router.get("/api/w/{ws}/overview", role="viewer")
async def overview(ctx: Ctx) -> dict[str, Any]:
    return await ctx.engine.overview.get(ctx.ws_id, ctx.workspace, _month(ctx), ctx.request.query_params.get("ledger") or None)


@router.get("/api/w/{ws}/overview/profitability", role="viewer")
async def profitability(ctx: Ctx) -> dict[str, Any]:
    return await asyncio.to_thread(ctx.engine.overview.profitability, ctx.ws_id, ctx.workspace, _month(ctx))


@router.get("/api/w/{ws}/overview/breakdown/{measure}", role="viewer")
async def breakdown(ctx: Ctx) -> dict[str, Any]:
    return await asyncio.to_thread(ctx.engine.overview.breakdown, ctx.ws_id, ctx.workspace,
                                   ctx.request.path_params["measure"], _month(ctx))


@router.get("/api/w/{ws}/overview/transactions/{measure}", role="viewer")
async def transactions(ctx: Ctx) -> dict[str, Any]:
    key = ctx.request.query_params.get("account")
    if not key:
        raise Invalid("Which account? Pass its key as ?account=.")
    return await asyncio.to_thread(ctx.engine.overview.transactions, ctx.ws_id, ctx.workspace,
                                   ctx.request.path_params["measure"], key, _month(ctx))


@router.get("/api/w/{ws}/overview/entries/{entry}", role="viewer")
async def entry(ctx: Ctx) -> dict[str, Any]:
    return await asyncio.to_thread(ctx.engine.overview.entry, ctx.ws_id, ctx.workspace,
                                   ctx.request.path_params["entry"], _month(ctx))


@router.post("/api/w/{ws}/overview/insights", role="viewer")
async def insights(ctx: Ctx) -> dict[str, Any]:
    lang = (ctx.request.query_params.get("lang") or "en")[:2]
    names = {"en": "English", "ar": "Arabic", "es": "Spanish", "fr": "French"}
    return await ctx.engine.overview.ai_insights(ctx.ws_id, ctx.workspace, _month(ctx), names.get(lang, "English"))


# ---- the CFO's pages ------------------------------------------------------------------------------------------------
@router.get("/api/w/{ws}/overview/statements", role="viewer")
async def statements(ctx: Ctx) -> dict[str, Any]:
    periods = ctx.request.query_params.get("periods") or "12"
    if not periods.isdigit() or not 1 <= int(periods) <= 36:
        raise Invalid("periods is a number from 1 to 36.")
    return await asyncio.to_thread(ctx.engine.statements.statements, ctx.ws_id, ctx.workspace, _month(ctx), int(periods))


@router.get("/api/w/{ws}/overview/budget", role="viewer")
async def budget(ctx: Ctx) -> dict[str, Any]:
    return await asyncio.to_thread(ctx.engine.statements.budget, ctx.ws_id, ctx.workspace, _month(ctx))


@router.get("/api/w/{ws}/overview/parties/{kind}", role="viewer")
async def parties(ctx: Ctx) -> dict[str, Any]:
    return await asyncio.to_thread(ctx.engine.statements.parties, ctx.ws_id, ctx.workspace, ctx.request.path_params["kind"], _month(ctx))


@router.get("/api/w/{ws}/overview/anomalies", role="viewer")
async def anomalies(ctx: Ctx) -> dict[str, Any]:
    return await asyncio.to_thread(ctx.engine.statements.anomalies, ctx.ws_id, ctx.workspace, _month(ctx))
