"""Datasets: Excel/CSV import with preview, browsing, querying and export."""

from __future__ import annotations

import csv
import io
import json
import time
from typing import Any, Literal

from pydantic import BaseModel, Field
from starlette.responses import Response

from ..files.cells import csv_safe
from ..data import importer, store
from ..data.query import QuerySpec, dataset_summary, run_query
from ..db import new_id
from ..errors import Invalid, NotFound
from ..web import Ctx, Router

router = Router()
UPLOAD_TTL = 3600


async def _expire_uploads(ctx: Ctx) -> None:
    for old in await ctx.engine.storage.list(f"uploads/{ctx.ws_id}"):
        if time.time() - old.modified > UPLOAD_TTL:
            await ctx.engine.storage.delete(old.key)


@router.get("/api/w/{ws}/datasets", role="viewer")
async def list_datasets(ctx: Ctx) -> list[dict[str, Any]]:
    return [store.public(d) for d in store.list_datasets(ctx.engine, ctx.ws_id)]


@router.get("/api/w/{ws}/datasets/{did}", role="viewer")
async def get_dataset(ctx: Ctx) -> dict[str, Any]:
    did = ctx.request.path_params["did"]
    ds = store.get_dataset(ctx.engine, ctx.ws_id, did)
    offset = max(int(ctx.request.query_params.get("offset", 0)), 0)
    return {"dataset": store.public(ds), "rows": store.sample_rows(ctx.engine, ctx.ws_id, did, 200, offset),
            "summary": dataset_summary(ctx.engine, ctx.ws_id, did)}


class RenameIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)


@router.patch("/api/w/{ws}/datasets/{did}", role="editor", body=RenameIn)
async def rename(ctx: Ctx) -> dict[str, Any]:
    did = ctx.request.path_params["did"]
    if not ctx.engine.db.update("datasets", did, {"name": ctx.body.name.strip()}, workspace_id=ctx.ws_id):
        raise NotFound("Dataset not found.")
    return store.public(store.get_dataset(ctx.engine, ctx.ws_id, did))


@router.delete("/api/w/{ws}/datasets/{did}", role="editor", fresh=True)
async def delete(ctx: Ctx) -> None:
    store.delete_dataset(ctx.engine, ctx.ws_id, ctx.request.path_params["did"])
    ctx.audit("dataset.delete", ctx.request.path_params["did"])


@router.get("/api/w/{ws}/datasets/{did}/export.csv", role="viewer")
async def export_csv(ctx: Ctx) -> Response:
    did = ctx.request.path_params["did"]
    ds = store.get_dataset(ctx.engine, ctx.ws_id, did)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow([c["name"] for c in ds["columns"]])
    keys = ", ".join(c["key"] for c in ds["columns"])
    for row in ctx.engine.db.conn.execute(f"SELECT {keys} FROM {store.table_name(ds['id'])} ORDER BY _row"):
        w.writerow([csv_safe(v) for v in row])  # text that a spreadsheet would read as a formula stays text
    safe = "".join(ch if ch.isalnum() or ch in " -_." else "_" for ch in ds["name"])[:80]
    return Response(buf.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"content-disposition": f'attachment; filename="{safe}.csv"'})


@router.post("/api/w/{ws}/query", role="viewer", body=QuerySpec)
async def query(ctx: Ctx) -> dict[str, Any]:
    import asyncio

    # aggregation over a large ledger can take a while; it runs on its own connection in a thread so the event loop
    # (and every other request and event stream) keeps going
    return await asyncio.to_thread(run_query, ctx.engine, ctx.workspace, ctx.body)


# --- import -------------------------------------------------------------------------------------------


@router.post("/api/w/{ws}/imports", role="editor")
async def upload(ctx: Ctx) -> dict[str, Any]:
    limit = ctx.engine.settings.max_upload_mb * 1024 * 1024
    if int(ctx.request.headers.get("content-length") or 0) > limit + 10000:
        raise Invalid(f"Files up to {ctx.engine.settings.max_upload_mb} MB can be imported.")
    async with ctx.request.form(max_files=1, max_fields=5) as form:
        f = form.get("file")
        if f is None or isinstance(f, str):
            raise Invalid("Choose a file to import.")
        data = await f.read()
        filename = f.filename or "upload.xlsx"
    if len(data) > limit:
        raise Invalid(f"Files up to {ctx.engine.settings.max_upload_mb} MB can be imported.")
    tables = importer.read_workbook(data, filename)
    upload_id = new_id("up_")
    await _expire_uploads(ctx)
    await ctx.engine.storage.put(f"uploads/{ctx.ws_id}/{upload_id}", data)
    await ctx.engine.storage.put(f"uploads/{ctx.ws_id}/{upload_id}.json", json.dumps({"filename": filename}).encode())
    previews = []
    for t in tables[:20]:
        try:
            previews.append(importer.preview(importer.shape(t)))
        except Exception as exc:  # one odd sheet should not block the others
            previews.append({"sheet": t.name, "error": getattr(exc, "message", str(exc))})
    return {"upload_id": upload_id, "filename": filename, "sheets": previews}


class Unpivot(BaseModel):
    id_columns: list[str] = Field(min_length=1)
    value_name: str = "Amount"
    variable_name: str = "Period"


class ShapeIn(BaseModel):
    sheet: str | None = None
    header_row: int | None = Field(default=None, ge=0, le=1000)
    decimal_comma: bool | None = None
    date_order: Literal["auto", "DMY", "MDY"] | None = "auto"
    unpivot: Unpivot | None = None
    name: str | None = Field(default=None, max_length=120)


async def _load(ctx: Ctx, upload_id: str, b: ShapeIn) -> tuple[importer.Table, str]:
    if not upload_id.startswith("up_") or not upload_id[3:].isalnum():
        raise Invalid("Bad upload id.")
    try:
        data = await ctx.engine.storage.get(f"uploads/{ctx.ws_id}/{upload_id}")
        meta = json.loads(await ctx.engine.storage.get(f"uploads/{ctx.ws_id}/{upload_id}.json"))
    except NotFound as exc:
        raise NotFound("The uploaded file has expired. Upload it again.") from exc
    import asyncio

    def parse() -> importer.Table:   # parsing and reshaping a 200,000-row file is CPU work: off the event loop
        tables = importer.read_workbook(data, meta["filename"])
        table = next((t for t in tables if t.name == b.sheet), None) if b.sheet else tables[0]
        if table is None:
            raise NotFound("Sheet not found.")
        shaped = importer.shape(table, header_row=b.header_row, decimal_comma=b.decimal_comma, date_order=b.date_order)
        if b.unpivot:
            shaped = importer.unpivot(shaped, b.unpivot.id_columns, b.unpivot.value_name, b.unpivot.variable_name)
        return shaped

    return await asyncio.to_thread(parse), meta["filename"]


@router.post("/api/w/{ws}/imports/{upload_id}/preview", role="editor", body=ShapeIn)
async def reshape(ctx: Ctx) -> dict[str, Any]:
    shaped, _ = await _load(ctx, ctx.request.path_params["upload_id"], ctx.body)
    return importer.preview(shaped)


@router.post("/api/w/{ws}/imports/{upload_id}/commit", role="editor", body=ShapeIn)
async def commit(ctx: Ctx) -> dict[str, Any]:
    upload_id = ctx.request.path_params["upload_id"]
    shaped, filename = await _load(ctx, upload_id, ctx.body)
    if not shaped.rows:
        raise Invalid("There are no data rows to import with these settings.")
    name = (ctx.body.name or f"{filename.rsplit('.', 1)[0]} · {shaped.name}").strip()
    import asyncio

    ds = await asyncio.to_thread(store.create_dataset, ctx.engine, ctx.ws_id, name=name,
                                 source="excel" if not filename.lower().endswith(".csv") else "csv",
                                 columns=[c.to_dict() for c in shaped.columns], rows=shaped.rows,
                                 source_ref={"filename": filename, "sheet": shaped.name}, created_by=ctx.user_id)
    await ctx.engine.storage.delete(f"uploads/{ctx.ws_id}/{upload_id}")
    await ctx.engine.storage.delete(f"uploads/{ctx.ws_id}/{upload_id}.json")
    ctx.audit("dataset.import", ds["id"], {"filename": filename, "rows": ds["row_count"]})
    return store.public(ds)
