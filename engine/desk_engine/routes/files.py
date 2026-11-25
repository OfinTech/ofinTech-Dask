"""Evidence-linked files: list, view (with the source of every cell), download, refresh, review."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError
from starlette.responses import Response

from ..errors import Invalid
from ..files.folders import STORED
from ..files.spec import FileSpec
from ..web import Ctx, Router

router = Router()


def _version(ctx: Ctx) -> int | None:
    v = ctx.request.query_params.get("version")
    return int(v) if v and v.isdigit() else None


@router.get("/api/w/{ws}/files", role="viewer")
async def list_files(ctx: Ctx) -> list[dict[str, Any]]:
    """Every file, or with ``?folder=`` the files of one folder (``root`` for the top level)."""
    return ctx.engine.files.list(ctx.ws_id, ctx.request.query_params.get("folder", "all"))


# -- folders. Declared before the routes that take a file id, so that "folders" is never read as one. -----------------
class FolderIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    parent_id: str | None = Field(default=None, max_length=64)


class FolderChange(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    parent_id: str | None = Field(default=None, max_length=64)
    move: bool = False          # true when parent_id is meant, since the top level is null


class MoveIn(BaseModel):
    folder_id: str | None = Field(default=None, max_length=64)


@router.get("/api/w/{ws}/files/folders", role="viewer")
async def list_folders(ctx: Ctx) -> list[dict[str, Any]]:
    return ctx.engine.files.folders.list(ctx.ws_id)


@router.post("/api/w/{ws}/files/folders", role="editor", body=FolderIn)
async def create_folder(ctx: Ctx) -> dict[str, Any]:
    return ctx.engine.files.folders.create(ctx.ws_id, ctx.user_id, ctx.body.name, ctx.body.parent_id)  # type: ignore[arg-type]


@router.patch("/api/w/{ws}/files/folders/{folder}", role="editor", body=FolderChange)
async def change_folder(ctx: Ctx) -> dict[str, Any]:
    """Rename a folder, move it, or both."""
    return ctx.engine.files.folders.update(ctx.ws_id, ctx.user_id, ctx.request.path_params["folder"],  # type: ignore[arg-type]
                                           name=ctx.body.name, parent_id=ctx.body.parent_id, move=ctx.body.move)


@router.delete("/api/w/{ws}/files/folders/{folder}", role="editor")
async def delete_folder(ctx: Ctx) -> dict[str, Any]:
    """Remove a folder. What is in it moves to the folder above; a folder that is not empty needs ``?confirm=1``."""
    return ctx.engine.files.folders.delete(ctx.ws_id, ctx.user_id, ctx.request.path_params["folder"],  # type: ignore[arg-type]
                                           confirmed=ctx.request.query_params.get("confirm") in ("1", "true", "yes"))


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
    """Add a file, into a folder (form field ``folder``) or at the top level. An .xlsx or .docx (a template, last
    month's pack) becomes one the assistant can fill or update; any other document is kept as it arrived."""
    if int(ctx.request.headers.get("content-length") or 0) > 26 * 1024 * 1024:
        raise Invalid("Files up to 25 MB can be added.", code="file_too_large")
    async with ctx.request.form(max_files=1, max_fields=3) as form:
        f = form.get("file")
        if f is None or isinstance(f, str):
            raise Invalid("Choose a file.")
        folder = form.get("folder")
        data = await f.read()
        name = f.filename or "file"
    return await ctx.engine.files.add(ctx.workspace, ctx.user_id, name, data,  # type: ignore[arg-type]
                                      folder_id=folder if isinstance(folder, str) else None)


@router.post("/api/w/{ws}/files/{fid}/move", role="editor", body=MoveIn)
async def move_file(ctx: Ctx) -> dict[str, Any]:
    """Put a file in a folder, or at the top level with ``folder_id`` null."""
    return ctx.engine.files.folders.move_file(ctx.ws_id, ctx.user_id, ctx.request.path_params["fid"],  # type: ignore[arg-type]
                                              ctx.body.folder_id)


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
    if fmt not in STORED:           # xlsx and docx are in it; the service says when this file has no such version
        raise Invalid("That is not a format a file is kept in.")
    data, name, mime = await ctx.engine.files.download(ctx.ws_id, ctx.request.path_params["fid"], fmt, _version(ctx))
    safe = "".join(ch if ch.isalnum() or ch in " -_.()&" else "_" for ch in name)
    ctx.audit("file.download", ctx.request.path_params["fid"], {"format": fmt})
    # Always a download, and never a type the browser may guess its way into showing: a stored document is
    # whatever its owner uploaded.
    return Response(data, media_type=mime, headers={"content-disposition": f'attachment; filename="{safe}"',
                                                    "x-content-type-options": "nosniff"})


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


@router.put("/api/w/{ws}/files-settings", role="admin", body=ReviewSettingsIn, fresh=True)
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
