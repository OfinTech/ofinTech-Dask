"""Evidence-linked files: list, view (with the source of every cell), download, refresh, review."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError
from starlette.responses import Response

from ..errors import Invalid
from ..files.spec import FileSpec
from ..web import Ctx, Router

router = Router()


def _version(ctx: Ctx) -> int | None:
    v = ctx.request.query_params.get("version")
    return int(v) if v and v.isdigit() else None


@router.get("/api/w/{ws}/files", role="viewer")
async def list_files(ctx: Ctx) -> list[dict[str, Any]]:
    return ctx.engine.files.list(ctx.ws_id)


@router.post("/api/w/{ws}/files", role="editor")
async def create_file(ctx: Ctx) -> dict[str, Any]:
    """Create from a definition (the same JSON the assistant uses), e.g. from a workflow or an integration."""
    try:
        raw = await ctx.request.json()
        spec = FileSpec.model_validate(raw.get("spec") if isinstance(raw, dict) and "spec" in raw else raw)
    except ValidationError as exc:
        raise Invalid("The file definition is not valid.", details=exc.errors(include_url=False)[:10]) from exc
    except ValueError as exc:
        raise Invalid("Send a JSON file definition.") from exc
    return await ctx.engine.files.create(ctx.workspace, ctx.user_id, spec, thread_id=None)  # type: ignore[arg-type]


@router.post("/api/w/{ws}/files/upload", role="editor")
async def upload_file(ctx: Ctx) -> dict[str, Any]:
    """Keep your own .xlsx or .docx (a template, last month's pack) so the assistant can fill or update it."""
    if int(ctx.request.headers.get("content-length") or 0) > 26 * 1024 * 1024:
        raise Invalid("Files up to 25 MB can be edited.")
    async with ctx.request.form(max_files=1, max_fields=2) as form:
        f = form.get("file")
        if f is None or isinstance(f, str):
            raise Invalid("Choose an Excel or Word file.")
        data = await f.read()
        name = f.filename or "file"
    return await ctx.engine.files.import_upload(ctx.workspace, ctx.user_id, name, data)  # type: ignore[arg-type]


class EditIn(BaseModel):
    edits: list[dict[str, Any]] = Field(min_length=1, max_length=200)
    note: str | None = Field(default=None, max_length=300)


@router.post("/api/w/{ws}/files/{fid}/edit", role="editor", body=EditIn)
async def edit_file(ctx: Ctx) -> dict[str, Any]:
    """The same edits the assistant makes (edit_file), for integrations and workflows."""
    return await ctx.engine.files.edit_upload(ctx.workspace, ctx.user_id, ctx.request.path_params["fid"],  # type: ignore[arg-type]
                                              ctx.body.edits, thread_id=None, note=ctx.body.note)


@router.get("/api/w/{ws}/files/{fid}/inspect", role="viewer")
async def inspect_file(ctx: Ctx) -> dict[str, Any]:
    q = ctx.request.query_params
    text = await ctx.engine.files.inspect_upload(ctx.ws_id, ctx.request.path_params["fid"], sheet=q.get("sheet"),
                                                 cell_range=q.get("range"), version=_version(ctx))
    return {"text": text}


@router.get("/api/w/{ws}/files/{fid}", role="viewer")
async def get_file(ctx: Ctx) -> dict[str, Any]:
    return ctx.engine.files.get(ctx.ws_id, ctx.request.path_params["fid"], _version(ctx))


@router.get("/api/w/{ws}/files/{fid}/cell", role="viewer")
async def cell(ctx: Ctx) -> dict[str, Any]:
    address = ctx.request.query_params.get("a") or ""
    if not address or len(address) > 80:
        raise Invalid("Give a cell address, e.g. ?a=P%26L!C7")
    return ctx.engine.files.cell(ctx.workspace, ctx.request.path_params["fid"], address, _version(ctx))  # type: ignore[arg-type]


@router.get("/api/w/{ws}/files/{fid}/download", role="viewer")
async def download(ctx: Ctx) -> Response:
    fmt = ctx.request.query_params.get("format", "xlsx")
    if fmt not in {"xlsx", "docx"}:
        raise Invalid("format must be xlsx or docx")
    data, name, mime = await ctx.engine.files.download(ctx.ws_id, ctx.request.path_params["fid"], fmt, _version(ctx))
    safe = "".join(ch if ch.isalnum() or ch in " -_.()&" else "_" for ch in name)
    ctx.audit("file.download", ctx.request.path_params["fid"], {"format": fmt})
    return Response(data, media_type=mime, headers={"content-disposition": f'attachment; filename="{safe}"'})


@router.post("/api/w/{ws}/files/{fid}/refresh", role="editor")
async def refresh(ctx: Ctx) -> dict[str, Any]:
    return await ctx.engine.files.refresh(ctx.workspace, ctx.user_id, ctx.request.path_params["fid"])  # type: ignore[arg-type]


@router.post("/api/w/{ws}/files/{fid}/review", role="editor")
async def review(ctx: Ctx) -> dict[str, Any]:
    ctx.engine.files.start_review(ctx.workspace, ctx.request.path_params["fid"], ctx.user_id)  # type: ignore[arg-type]
    return {"status": "running"}


class FindingIn(BaseModel):
    status: Literal["open", "resolved", "dismissed"]


@router.patch("/api/w/{ws}/files/{fid}/findings/{finding}", role="editor", body=FindingIn)
async def decide(ctx: Ctx) -> dict[str, Any]:
    return await ctx.engine.files.decide(ctx.ws_id, ctx.request.path_params["fid"], ctx.request.path_params["finding"],
                                         ctx.body.status, ctx.user_id)


@router.delete("/api/w/{ws}/files/{fid}", role="editor")
async def delete(ctx: Ctx) -> None:
    await ctx.engine.files.delete(ctx.ws_id, ctx.request.path_params["fid"], ctx.user_id)


class ReviewSettingsIn(BaseModel):
    auto_review: bool = True
    provider_id: str | None = None
    model: str | None = Field(default=None, max_length=200)


@router.get("/api/w/{ws}/files-settings", role="viewer")
async def settings(ctx: Ctx) -> dict[str, Any]:
    w = ctx.workspace or {}
    return {"auto_review": bool(w.get("auto_review", 1)), "provider_id": w.get("reviewer_provider_id"),
            "model": w.get("reviewer_model"), "storage": ctx.engine.storage.describe()}


@router.put("/api/w/{ws}/files-settings", role="admin", body=ReviewSettingsIn)
async def put_settings(ctx: Ctx) -> dict[str, Any]:
    b: ReviewSettingsIn = ctx.body
    if b.provider_id:
        from ..llm.registry import provider_row

        provider_row(ctx.engine, ctx.ws_id, b.provider_id)
    ctx.engine.db.update("workspaces", ctx.ws_id, {"auto_review": int(b.auto_review),
                                                   "reviewer_provider_id": b.provider_id or None,
                                                   "reviewer_model": (b.model or None) if b.provider_id else None})
    ctx.audit("files.settings", ctx.ws_id, b.model_dump())
    ctx.workspace = ctx.engine.db.one("SELECT * FROM workspaces WHERE id = ?", (ctx.ws_id,))
    return await settings(ctx)
