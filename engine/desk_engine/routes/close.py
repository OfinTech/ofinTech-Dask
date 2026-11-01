"""Month-end close: prepare a month from the ledger, add the bank statement, decide proposed entries and items,
export the approved journal and the close pack, and close the month."""

from __future__ import annotations

import asyncio
from typing import Any, Literal

from pydantic import BaseModel, Field
from starlette.responses import Response

from ..errors import Invalid
from ..web import Ctx, Router

router = Router()
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@router.get("/api/w/{ws}/close", role="viewer")
async def overview(ctx: Ctx) -> dict[str, Any]:
    return await asyncio.to_thread(ctx.engine.close.overview, ctx.ws_id)


class PrepareIn(BaseModel):
    month: str | None = Field(default=None, pattern=r"^\d{4}-(0[1-9]|1[0-2])$")
    dataset_id: str | None = Field(default=None, max_length=40)
    materiality: float | None = Field(default=None, ge=0, le=1e12)


@router.post("/api/w/{ws}/close", role="editor", body=PrepareIn)
async def prepare(ctx: Ctx) -> dict[str, Any]:
    b: PrepareIn = ctx.body
    return await asyncio.to_thread(ctx.engine.close.prepare, ctx.ws_id, ctx.user_id, month=b.month,
                                   dataset_id=b.dataset_id, materiality=b.materiality)


@router.get("/api/w/{ws}/close/{rid}", role="viewer")
async def view(ctx: Ctx) -> dict[str, Any]:
    return ctx.engine.close.view(ctx.ws_id, ctx.request.path_params["rid"])


class RefreshIn(BaseModel):
    materiality: float | None = Field(default=None, ge=0, le=1e12)


@router.post("/api/w/{ws}/close/{rid}/refresh", role="editor", body=RefreshIn)
async def refresh(ctx: Ctx) -> dict[str, Any]:
    run = ctx.engine.close._run(ctx.ws_id, ctx.request.path_params["rid"])
    return await asyncio.to_thread(ctx.engine.close.prepare, ctx.ws_id, ctx.user_id, month=run["month"],
                                   dataset_id=run["dataset_id"], materiality=ctx.body.materiality)


def _float(v: Any) -> float | None:
    from ..data.importer import parse_number

    if v in (None, ""):
        return None
    n = parse_number(str(v))
    if n is None:
        raise Invalid("The closing balance is a number.")
    return float(n)


@router.post("/api/w/{ws}/close/{rid}/statement", role="editor")
async def statement(ctx: Ctx) -> dict[str, Any]:
    """The month's bank statement (.xlsx or .csv), with optional bank account and closing balance."""
    limit = ctx.engine.settings.max_upload_mb * 1024 * 1024
    if int(ctx.request.headers.get("content-length") or 0) > limit + 10000:
        raise Invalid(f"Files up to {ctx.engine.settings.max_upload_mb} MB can be uploaded.")
    async with ctx.request.form(max_files=1, max_fields=5) as form:
        f = form.get("file")
        if f is None or isinstance(f, str):
            raise Invalid("Choose the bank statement file.")
        data = await f.read()
        filename = f.filename or "statement.csv"
        account = form.get("account_key")
        closing = form.get("closing_balance")
    if len(data) > limit:
        raise Invalid(f"Files up to {ctx.engine.settings.max_upload_mb} MB can be uploaded.")
    return await asyncio.to_thread(ctx.engine.close.add_statement, ctx.ws_id, ctx.user_id,
                                   ctx.request.path_params["rid"], filename, data,
                                   account_key_=str(account) if account else None, closing_balance=_float(closing))


class StatementOptions(BaseModel):
    account_key: str | None = Field(default=None, max_length=300)
    closing_balance: float | None = Field(default=None, ge=-1e13, le=1e13)


@router.put("/api/w/{ws}/close/{rid}/statement", role="editor", body=StatementOptions)
async def statement_options(ctx: Ctx) -> dict[str, Any]:
    b: StatementOptions = ctx.body
    return await asyncio.to_thread(ctx.engine.close.set_statement_options, ctx.ws_id, ctx.user_id,
                                   ctx.request.path_params["rid"], account_key_=b.account_key,
                                   closing_balance=b.closing_balance)


class LineChange(BaseModel):
    index: int = Field(ge=0, le=20)
    account: str = Field(min_length=1, max_length=200)
    account_code: str | None = Field(default=None, max_length=40)


class ItemIn(BaseModel):
    status: Literal["proposed", "approved", "rejected", "open", "explained", "reviewed"] | None = None
    note: str | None = Field(default=None, max_length=1000)
    amount: float | None = Field(default=None, gt=0, le=1e12)
    lines: list[LineChange] | None = Field(default=None, max_length=10)


@router.patch("/api/w/{ws}/close/{rid}/items/{iid}", role="editor", body=ItemIn)
async def decide(ctx: Ctx) -> dict[str, Any]:
    body = ctx.body.model_dump(exclude_unset=True)
    if "lines" in body and body["lines"] is not None:
        body["lines"] = [dict(x) for x in body["lines"]]
    return await asyncio.to_thread(ctx.engine.close.decide, ctx.ws_id, ctx.user_id, ctx.request.path_params["rid"],
                                   ctx.request.path_params["iid"], body)


class StatusIn(BaseModel):
    status: Literal["open", "closed"]
    note: str | None = Field(default=None, max_length=1000)


@router.post("/api/w/{ws}/close/{rid}/status", role="admin", body=StatusIn, fresh=True)
async def set_status(ctx: Ctx) -> dict[str, Any]:
    return ctx.engine.close.set_status(ctx.ws_id, ctx.user_id, ctx.request.path_params["rid"], ctx.body.status,
                                       ctx.body.note)


@router.delete("/api/w/{ws}/close/{rid}", role="admin", fresh=True)
async def delete(ctx: Ctx) -> None:
    ctx.engine.close.delete(ctx.ws_id, ctx.user_id, ctx.request.path_params["rid"])


@router.get("/api/w/{ws}/close/{rid}/journal.{fmt}", role="viewer")
async def journal(ctx: Ctx) -> Response:
    from ..close.pack import journal_csv, journal_xlsx

    v = ctx.engine.close.view(ctx.ws_id, ctx.request.path_params["rid"])
    fmt = ctx.request.path_params["fmt"]
    name = f"OfinTech-Desk-close-journal-{v['month']}"
    if fmt == "csv":
        return Response(journal_csv(v), media_type="text/csv; charset=utf-8",
                        headers={"content-disposition": f'attachment; filename="{name}.csv"'})
    if fmt == "xlsx":
        data = await asyncio.to_thread(journal_xlsx, v)
        return Response(data, media_type=XLSX, headers={"content-disposition": f'attachment; filename="{name}.xlsx"'})
    raise Invalid("The journal downloads as csv or xlsx.")


@router.get("/api/w/{ws}/close/{rid}/pack.xlsx", role="viewer")
async def pack(ctx: Ctx) -> Response:
    from ..close.pack import pack_xlsx

    v = ctx.engine.close.view(ctx.ws_id, ctx.request.path_params["rid"])
    data = await asyncio.to_thread(pack_xlsx, v, ctx.workspace.get("name") or "", ctx.workspace.get("base_currency") or "")
    return Response(data, media_type=XLSX, headers={
        "content-disposition": f'attachment; filename="OfinTech-Desk-close-pack-{v["month"]}.xlsx"'})
