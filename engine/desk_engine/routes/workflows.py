"""Workflows: definitions, manual runs, run history; the tool catalogue for the builder."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from ..agent.tools import ToolContext
from ..db import loads
from ..errors import Invalid, NotFound
from ..web import Ctx, Router
from ..workflows.engine import WorkflowDef, upcoming
from ..workflows.templates import localized

router = Router()


class WorkflowIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=2000)
    definition: WorkflowDef
    enabled: bool = True


def _summary(row: dict[str, Any]) -> dict[str, Any]:
    d = loads(row["definition"], {}) if isinstance(row["definition"], str) else row["definition"]
    last = None
    return {"id": row["id"], "name": row["name"], "description": row["description"], "enabled": bool(row["enabled"]),
            "trigger": d.get("trigger"), "steps": len(d.get("steps", [])), "next_run_at": row["next_run_at"],
            "updated_at": row["updated_at"], "last_run": last, "webhook": bool(row.get("webhook_hash"))}


@router.get("/api/w/{ws}/workflows", role="viewer")
async def list_workflows(ctx: Ctx) -> list[dict[str, Any]]:
    rows = ctx.engine.db.all("SELECT * FROM workflows WHERE workspace_id = ? ORDER BY name", (ctx.ws_id,))
    out = []
    for r in rows:
        s = _summary(r)
        s["last_run"] = ctx.engine.db.one("SELECT id, status, started_at, finished_at FROM workflow_runs WHERE "
                                          "workflow_id = ? ORDER BY started_at DESC LIMIT 1", (r["id"],))
        out.append(s)
    return out


@router.post("/api/w/{ws}/workflows", role="editor", body=WorkflowIn)
async def create(ctx: Ctx) -> dict[str, Any]:
    b: WorkflowIn = ctx.body
    wf = ctx.engine.workflows.save(ctx.workspace, ctx.user_id, b.name, b.description, b.definition, b.enabled)
    ctx.audit("workflow.create", wf["id"])
    return wf


@router.get("/api/w/{ws}/workflows/{wid}", role="viewer")
async def get(ctx: Ctx) -> dict[str, Any]:
    wf = ctx.engine.workflows.get(ctx.ws_id, ctx.request.path_params["wid"])
    runs = ctx.engine.db.all("SELECT id, status, trigger, error, started_at, finished_at FROM workflow_runs WHERE "
                             "workflow_id = ? ORDER BY started_at DESC LIMIT 30", (wf["id"],))
    for r in runs:
        r["trigger"] = loads(r["trigger"], {})
    return {"workflow": wf, "runs": runs}


@router.put("/api/w/{ws}/workflows/{wid}", role="editor", body=WorkflowIn)
async def update(ctx: Ctx) -> dict[str, Any]:
    b: WorkflowIn = ctx.body
    wf = ctx.engine.workflows.save(ctx.workspace, ctx.user_id, b.name, b.description, b.definition, b.enabled,
                                   wf_id=ctx.request.path_params["wid"])
    ctx.audit("workflow.update", wf["id"])
    return wf


@router.delete("/api/w/{ws}/workflows/{wid}", role="editor")
async def delete(ctx: Ctx) -> None:
    wid = ctx.request.path_params["wid"]
    if not ctx.engine.db.execute("DELETE FROM workflows WHERE id = ? AND workspace_id = ?", (wid, ctx.ws_id)):
        raise NotFound("Workflow not found.")
    ctx.audit("workflow.delete", wid)


class RunIn(BaseModel):
    input: str = Field(default="", max_length=8000)


@router.post("/api/w/{ws}/workflows/{wid}/run", role="editor", body=RunIn)
async def run(ctx: Ctx) -> dict[str, Any]:
    wf = ctx.engine.workflows.get(ctx.ws_id, ctx.request.path_params["wid"])
    run_id = ctx.engine.workflows.start(ctx.workspace, wf, {"type": "manual", "text": ctx.body.input}, ctx.user_id)
    return {"run_id": run_id}


@router.get("/api/w/{ws}/workflow-runs/{rid}", role="viewer")
async def get_run(ctx: Ctx) -> dict[str, Any]:
    return ctx.engine.workflows.run_out(ctx.ws_id, ctx.request.path_params["rid"])


@router.get("/api/w/{ws}/tools", role="viewer")
async def tools(ctx: Ctx) -> list[dict[str, Any]]:
    tctx = ToolContext(ctx.engine, ctx.workspace, ctx.user_id)
    available = await ctx.engine.tools.for_context(tctx)
    return [{"name": t.name, "description": t.description, "effect": "write" if t.needs_approval() else "read",
             "category": t.category, "parameters": t.parameters} for t in sorted(available.values(),
                                                                                key=lambda x: (x.category, x.name))]


@router.get("/api/w/{ws}/workflow-templates", role="viewer")
async def templates(ctx: Ctx) -> list[dict[str, Any]]:
    return localized(ctx.request.query_params.get("lang") or (ctx.user or {}).get("language"))


class FromTemplateIn(BaseModel):
    key: str = Field(max_length=60)
    lang: str | None = Field(default=None, max_length=5)


@router.post("/api/w/{ws}/workflows/from-template", role="editor", body=FromTemplateIn)
async def from_template(ctx: Ctx) -> dict[str, Any]:
    t = next((x for x in localized(ctx.body.lang or (ctx.user or {}).get("language")) if x["key"] == ctx.body.key), None)
    if t is None:
        raise NotFound("No such template.")
    wf = ctx.engine.workflows.save(ctx.workspace, ctx.user_id, t["name"], t["description"],  # type: ignore[arg-type]
                                   WorkflowDef.model_validate(t["definition"]), False)
    ctx.audit("workflow.create", wf["id"], {"template": t["key"]})
    return wf


class ScheduleIn(BaseModel):
    cron: str = Field(max_length=100)
    timezone: str | None = Field(default=None, max_length=64)


@router.post("/api/w/{ws}/workflows/schedule-preview", role="viewer", body=ScheduleIn)
async def schedule_preview(ctx: Ctx) -> dict[str, Any]:
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

    tz = ctx.body.timezone or (ctx.workspace or {}).get("timezone") or "UTC"
    try:
        ZoneInfo(tz)
        return {"timezone": tz, "next": upcoming(ctx.body.cron, tz, 5)}
    except (ValueError, ZoneInfoNotFoundError) as exc:
        raise Invalid(f"That schedule is not valid: {exc}") from exc


@router.post("/api/w/{ws}/workflows/{wid}/webhook", role="editor")
async def webhook_secret(ctx: Ctx) -> dict[str, Any]:
    """Create (or replace) the secret another system uses to start this workflow. It is shown once."""
    from ..security import new_token, token_hash

    wf = ctx.engine.workflows.get(ctx.ws_id, ctx.request.path_params["wid"])
    if wf["definition"].get("trigger", {}).get("type") != "webhook":
        raise Invalid("Set the trigger to “From another system (webhook)” first.")
    secret = "dwh_" + new_token(24)
    ctx.engine.db.execute("UPDATE workflows SET webhook_hash = ? WHERE id = ?", (token_hash(secret), wf["id"]))
    ctx.audit("workflow.webhook", wf["id"])
    base = ctx.engine.settings.public_url.rstrip("/") or "http://127.0.0.1"
    return {"url": f"{base}/api/hooks/workflows/{wf['id']}", "secret": secret}


@router.delete("/api/w/{ws}/workflows/{wid}/webhook", role="editor")
async def webhook_off(ctx: Ctx) -> None:
    wf = ctx.engine.workflows.get(ctx.ws_id, ctx.request.path_params["wid"])
    ctx.engine.db.execute("UPDATE workflows SET webhook_hash = NULL WHERE id = ?", (wf["id"],))
    ctx.audit("workflow.webhook.off", wf["id"])
