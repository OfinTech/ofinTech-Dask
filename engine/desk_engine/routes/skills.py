"""Skills library: built-in finance skills, custom skills, import and export."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field
from starlette.responses import Response

from ..errors import Forbidden, Invalid, NotFound
from ..skills.loader import (get_skill, list_skills, parse_archive, parse_skill_md, render_skill_md, save_custom,
                             set_enabled)
from ..web import Ctx, Router

router = Router()


@router.get("/api/w/{ws}/skills", role="viewer")
async def skills(ctx: Ctx) -> list[dict[str, Any]]:
    return list_skills(ctx.engine, ctx.ws_id)


@router.get("/api/w/{ws}/skills/{name}", role="viewer")
async def skill(ctx: Ctx) -> dict[str, Any]:
    return get_skill(ctx.engine, ctx.ws_id, ctx.request.path_params["name"])


class SkillIn(BaseModel):
    markdown: str | None = Field(default=None, max_length=1_000_000)
    name: str | None = None
    description: str | None = None
    body: str | None = Field(default=None, max_length=1_000_000)
    tags: list[str] = Field(default_factory=list)
    version: str = "1.0.0"
    files: dict[str, str] = Field(default_factory=dict)


def _parse(b: SkillIn) -> dict[str, Any]:
    if b.markdown:
        return parse_skill_md(b.markdown)
    if not (b.name and b.description and b.body):
        raise Invalid("Give name, description and instructions, or the full SKILL.md.")
    return parse_skill_md(render_skill_md({"name": b.name, "description": b.description, "body": b.body,
                                           "tags": b.tags, "version": b.version}))


@router.post("/api/w/{ws}/skills", role="editor", body=SkillIn)
async def create_skill(ctx: Ctx) -> dict[str, Any]:
    skill = save_custom(ctx.engine, ctx.ws_id, _parse(ctx.body), ctx.body.files, user_id=ctx.user_id)
    ctx.audit("skill.create", skill["name"])
    return skill


@router.put("/api/w/{ws}/skills/{sid}", role="editor", body=SkillIn)
async def update_skill(ctx: Ctx) -> dict[str, Any]:
    sid = ctx.request.path_params["sid"]
    if sid.startswith("builtin:"):
        raise Forbidden("Built-in skills cannot be edited. Duplicate it to make your own version.")
    row = ctx.engine.db.one("SELECT files FROM skills WHERE id = ? AND workspace_id = ?", (sid, ctx.ws_id))
    if row is None:
        raise NotFound("Skill not found.")
    files = ctx.body.files or None
    if files is None:
        from ..db import loads

        files = loads(row["files"], {})
    skill = save_custom(ctx.engine, ctx.ws_id, _parse(ctx.body), files, user_id=ctx.user_id, skill_id=sid)
    ctx.audit("skill.update", skill["name"])
    return skill


@router.delete("/api/w/{ws}/skills/{sid}", role="editor")
async def delete_skill(ctx: Ctx) -> None:
    sid = ctx.request.path_params["sid"]
    if sid.startswith("builtin:"):
        raise Forbidden("Built-in skills can be turned off but not deleted.")
    if not ctx.engine.db.execute("DELETE FROM skills WHERE id = ? AND workspace_id = ?", (sid, ctx.ws_id)):
        raise NotFound("Skill not found.")
    ctx.audit("skill.delete", sid)


class EnabledIn(BaseModel):
    enabled: bool


@router.put("/api/w/{ws}/skills/{sid}/enabled", role="admin", body=EnabledIn)
async def toggle(ctx: Ctx) -> dict[str, Any]:
    set_enabled(ctx.engine, ctx.ws_id, ctx.request.path_params["sid"], ctx.body.enabled)
    ctx.audit("skill.enabled", ctx.request.path_params["sid"], {"enabled": ctx.body.enabled})
    return {"enabled": ctx.body.enabled}


@router.post("/api/w/{ws}/skills/import", role="editor")
async def import_skill(ctx: Ctx) -> dict[str, Any]:
    async with ctx.request.form(max_files=1, max_fields=5) as form:
        upload = form.get("file")
        if upload is None or isinstance(upload, str):
            raise Invalid("Choose a SKILL.md or .zip file.")
        data = await upload.read()
        name = (upload.filename or "").lower()
    ignored: list[str] = []
    if name.endswith(".zip"):
        skill, files, ignored = parse_archive(data)
    elif name.endswith(".md"):
        skill, files = parse_skill_md(data.decode("utf-8", errors="replace")), {}
    else:
        raise Invalid("Import a SKILL.md file or a .zip archive that contains one.")
    saved = save_custom(ctx.engine, ctx.ws_id, skill, files, user_id=ctx.user_id)
    ctx.audit("skill.import", saved["name"], {"ignored": ignored})
    return {"skill": saved, "ignored_files": ignored,
            "note": "Scripts and binary files are not imported: skills here are instructions only." if ignored else None}


@router.get("/api/w/{ws}/skills/{name}/export", role="viewer")
async def export_skill(ctx: Ctx) -> Response:
    skill = get_skill(ctx.engine, ctx.ws_id, ctx.request.path_params["name"])
    return Response(render_skill_md(skill), media_type="text/markdown; charset=utf-8",
                    headers={"content-disposition": f'attachment; filename="{skill["name"]}-SKILL.md"'})
