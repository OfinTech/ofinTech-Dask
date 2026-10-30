"""Workspaces, members, invitations, notifications, audit log and usage."""

from __future__ import annotations

from typing import Any, Literal
from zoneinfo import ZoneInfo, available_timezones

from pydantic import BaseModel, Field, field_validator

from ..db import loads, new_id, now
from ..errors import Conflict, Forbidden, Invalid, NotFound
from ..security import new_token, token_hash
from ..web import ROLES, Ctx, Email, Router

router = Router()

FRAMEWORKS = ["IFRS", "IFRS for SMEs", "US GAAP", "UK GAAP (FRS 102)", "Local GAAP", "Other"]


class WorkspaceIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    base_currency: str = Field(default="USD", pattern=r"^[A-Z]{3}$")
    country: str = Field(default="", pattern=r"^([A-Z]{2})?$")
    fiscal_year_start_month: int = Field(default=1, ge=1, le=12)
    accounting_framework: str = "IFRS"
    timezone: str = "UTC"

    @field_validator("timezone")
    @classmethod
    def _tz(cls, v: str) -> str:
        try:
            ZoneInfo(v)
        except Exception as exc:
            raise ValueError("unknown time zone") from exc
        return v

    @field_validator("accounting_framework")
    @classmethod
    def _fw(cls, v: str) -> str:
        if v not in FRAMEWORKS:
            raise ValueError(f"choose one of: {', '.join(FRAMEWORKS)}")
        return v


class WorkspacePatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    base_currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    country: str | None = Field(default=None, pattern=r"^([A-Z]{2})?$")
    fiscal_year_start_month: int | None = Field(default=None, ge=1, le=12)
    accounting_framework: str | None = None
    timezone: str | None = None
    egress_policy: str | None = Field(default=None, pattern=r"^(open|sealed)$")


@router.get("/api/meta", auth=True)
async def meta(ctx: Ctx) -> dict[str, Any]:
    return {"frameworks": FRAMEWORKS, "timezones": sorted(available_timezones()), "roles": ROLES}


@router.post("/api/workspaces", body=WorkspaceIn)
async def create_workspace(ctx: Ctx) -> dict[str, Any]:
    b: WorkspaceIn = ctx.body
    count = ctx.engine.db.scalar("SELECT COUNT(*) FROM members WHERE user_id = ? AND role = 'owner'", (ctx.user_id,))
    if count >= 50:
        raise Invalid("You own the maximum number of workspaces.")
    ws_id = new_id("ws_")
    with ctx.engine.db.tx() as c:
        c.execute("INSERT INTO workspaces (id, name, base_currency, country, fiscal_year_start_month, "
                  "accounting_framework, timezone, created_at) VALUES (?,?,?,?,?,?,?,?)",
                  (ws_id, b.name.strip(), b.base_currency, b.country, b.fiscal_year_start_month,
                   b.accounting_framework, b.timezone, now()))
        c.execute("INSERT INTO members (workspace_id, user_id, role, created_at) VALUES (?,?,?,?)",
                  (ws_id, ctx.user_id, "owner", now()))
    ctx.engine.audit(ws_id, ctx.user_id, "workspace.create", ws_id)
    return ctx.engine.db.one("SELECT * FROM workspaces WHERE id = ?", (ws_id,)) | {"role": "owner"}


@router.get("/api/w/{ws}", role="viewer")
async def get_workspace(ctx: Ctx) -> dict[str, Any]:
    return ctx.workspace | {"role": ctx.role}


@router.patch("/api/w/{ws}", role="admin", body=WorkspacePatch)
async def patch_workspace(ctx: Ctx) -> dict[str, Any]:
    values = {k: v for k, v in ctx.body.model_dump().items() if v is not None}
    if "accounting_framework" in values and values["accounting_framework"] not in FRAMEWORKS:
        raise Invalid("Unknown accounting framework.")
    if "timezone" in values:
        WorkspaceIn._tz(values["timezone"])
    if values.get("egress_policy") == "open" and ctx.engine.settings.sealed:
        raise Forbidden("This server keeps every workspace's data inside the organization (DESK_SEALED).",
                        code="egress_blocked")
    if "egress_policy" in values and ctx.role != "owner":
        raise Forbidden("Only the workspace owner can change where its data may go.")
    if values:
        ctx.engine.db.update("workspaces", ctx.ws_id, values)
        ctx.audit("workspace.update", ctx.ws_id, values)
    return ctx.engine.db.one("SELECT * FROM workspaces WHERE id = ?", (ctx.ws_id,)) | {"role": ctx.role}


@router.delete("/api/w/{ws}", role="owner")
async def delete_workspace(ctx: Ctx) -> None:
    from ..data.store import list_datasets, table_name

    for ds in list_datasets(ctx.engine, ctx.ws_id):
        ctx.engine.db.execute(f"DROP TABLE IF EXISTS {table_name(ds['id'])}")
    with ctx.engine.db.tx() as c:
        c.execute("DELETE FROM secrets WHERE workspace_id = ?", (ctx.ws_id,))
        c.execute("DELETE FROM workspaces WHERE id = ?", (ctx.ws_id,))
    from ..storage import delete_prefix

    for area in ("documents", "uploads", "files"):
        await delete_prefix(ctx.engine.storage, f"{area}/{ctx.ws_id}")
    ctx.engine.audit(None, ctx.user_id, "workspace.delete", ctx.ws_id)


# --- members -------------------------------------------------------------------------------------


@router.get("/api/w/{ws}/members", role="viewer")
async def members(ctx: Ctx) -> list[dict[str, Any]]:
    rows = ctx.engine.db.all("SELECT u.id, u.email, u.name, u.is_local, m.role, m.created_at FROM members m JOIN users u "
                             "ON u.id = m.user_id WHERE m.workspace_id = ? ORDER BY u.name", (ctx.ws_id,))
    for r in rows:
        r["channels"] = [x["provider"] for x in ctx.engine.db.all(
            "SELECT DISTINCT c.provider FROM channel_links l JOIN connections c ON c.id = l.connection_id "
            "WHERE l.workspace_id = ? AND l.user_id = ?", (ctx.ws_id, r["id"]))]
    return rows


class RoleIn(BaseModel):
    role: Literal["viewer", "editor", "admin", "owner"]


def _owner_count(ctx: Ctx) -> int:
    return ctx.engine.db.scalar("SELECT COUNT(*) FROM members WHERE workspace_id = ? AND role = 'owner'", (ctx.ws_id,))


@router.patch("/api/w/{ws}/members/{user_id}", role="admin", body=RoleIn)
async def set_role(ctx: Ctx) -> dict[str, Any]:
    target = ctx.request.path_params["user_id"]
    current = ctx.engine.db.one("SELECT role FROM members WHERE workspace_id = ? AND user_id = ?", (ctx.ws_id, target))
    if current is None:
        raise NotFound("Member not found.")
    new_role = ctx.body.role
    if (new_role == "owner" or current["role"] == "owner") and ctx.role != "owner":
        raise Forbidden("Only owners can grant or remove the owner role.")
    if current["role"] == "owner" and new_role != "owner" and _owner_count(ctx) <= 1:
        raise Conflict("A workspace needs at least one owner.")
    ctx.engine.db.execute("UPDATE members SET role = ? WHERE workspace_id = ? AND user_id = ?",
                          (new_role, ctx.ws_id, target))
    ctx.audit("member.role", target, {"from": current["role"], "to": new_role})
    return {"user_id": target, "role": new_role}


@router.delete("/api/w/{ws}/members/{user_id}", role="viewer")
async def remove_member(ctx: Ctx) -> None:
    target = ctx.request.path_params["user_id"]
    if target != ctx.user_id:
        ctx.require("admin")
    current = ctx.engine.db.one("SELECT role FROM members WHERE workspace_id = ? AND user_id = ?", (ctx.ws_id, target))
    if current is None:
        raise NotFound("Member not found.")
    if current["role"] == "owner":
        if ctx.role != "owner" and target != ctx.user_id:
            raise Forbidden("Only owners can remove an owner.")
        if _owner_count(ctx) <= 1:
            raise Conflict("A workspace needs at least one owner.")
    with ctx.engine.db.tx() as c:
        c.execute("DELETE FROM members WHERE workspace_id = ? AND user_id = ?", (ctx.ws_id, target))
        c.execute("DELETE FROM channel_links WHERE workspace_id = ? AND user_id = ?", (ctx.ws_id, target))
        c.execute("DELETE FROM mcp_keys WHERE workspace_id = ? AND user_id = ?", (ctx.ws_id, target))
    ctx.audit("member.remove", target)


class InviteIn(BaseModel):
    email: Email
    role: Literal["viewer", "editor", "admin"] = "editor"


@router.post("/api/w/{ws}/invites", role="admin", body=InviteIn)
async def invite(ctx: Ctx) -> dict[str, Any]:
    if ctx.engine.settings.is_desktop:
        raise Forbidden("Invitations need the Team Server. The desktop app is for one person.", code="needs_team_server")
    from ..mail import added_text, invite_text

    b: InviteIn = ctx.body
    existing = ctx.engine.db.one("SELECT 1 FROM members m JOIN users u ON u.id = m.user_id WHERE m.workspace_id = ? "
                                 "AND u.email = ?", (ctx.ws_id, b.email))
    if existing:
        raise Conflict("This person is already a member.")
    inviter = ctx.user["name"]  # type: ignore[index]
    wsname = ctx.workspace["name"]  # type: ignore[index]
    account = ctx.engine.db.one("SELECT id, email FROM users WHERE email = ? AND is_local = 0 AND disabled = 0", (b.email,))
    if account:  # already has an account on this server: add directly
        ctx.engine.db.insert("members", {"workspace_id": ctx.ws_id, "user_id": account["id"], "role": b.role,
                                         "created_at": now()})
        ctx.audit("member.add", account["id"], {"email": b.email, "role": b.role})
        ctx.engine.notify(ctx.ws_id, account["id"], "member", f"{inviter} added you to {wsname}.", "/chat")
        subject, text = added_text(inviter, wsname, f"{ctx.engine.settings.public_url.rstrip('/')}/#/chat?ws={ctx.ws_id}")
        emailed = await ctx.engine.mailer.send(account["email"], subject, text)
        return {"added": True, "email": b.email.lower(), "role": b.role, "emailed": emailed}
    token = new_token(24)
    iid = new_id("inv_")
    ctx.engine.db.insert("invites", {"id": iid, "workspace_id": ctx.ws_id, "email": b.email.lower(), "role": b.role,
                                     "token_hash": token_hash(token), "created_by": ctx.user_id, "created_at": now(),
                                     "expires_at": now() + 7 * 86400})
    ctx.audit("invite.create", iid, {"email": b.email, "role": b.role})
    link = f"{ctx.engine.settings.public_url.rstrip('/')}/#/invite/{token}"
    subject, text = invite_text(inviter, wsname, link)
    emailed = await ctx.engine.mailer.send(b.email, subject, text)
    return {"id": iid, "email": b.email.lower(), "role": b.role, "link": link, "emailed": emailed,
            "note": ("An invitation e-mail was sent. " if emailed else "Send this link to the person. ")
            + "It works once and expires in 7 days."}


@router.get("/api/w/{ws}/invites", role="admin")
async def list_invites(ctx: Ctx) -> list[dict[str, Any]]:
    return ctx.engine.db.all("SELECT id, email, role, created_at, expires_at, accepted_at FROM invites WHERE "
                             "workspace_id = ? ORDER BY created_at DESC LIMIT 100", (ctx.ws_id,))


@router.delete("/api/w/{ws}/invites/{invite_id}", role="admin")
async def revoke_invite(ctx: Ctx) -> None:
    ctx.engine.db.execute("DELETE FROM invites WHERE id = ? AND workspace_id = ?",
                          (ctx.request.path_params["invite_id"], ctx.ws_id))


# --- notifications, audit, usage ----------------------------------------------------------------------


@router.get("/api/w/{ws}/notifications", role="viewer")
async def notifications(ctx: Ctx) -> dict[str, Any]:
    items = ctx.engine.db.all("SELECT * FROM notifications WHERE workspace_id = ? AND user_id = ? ORDER BY created_at "
                              "DESC LIMIT 50", (ctx.ws_id, ctx.user_id))
    unread = ctx.engine.db.scalar("SELECT COUNT(*) FROM notifications WHERE workspace_id = ? AND user_id = ? AND "
                                  "read_at IS NULL", (ctx.ws_id, ctx.user_id))
    pending = ctx.engine.db.scalar("SELECT COUNT(*) FROM approvals WHERE workspace_id = ? AND status = 'pending'",
                                   (ctx.ws_id,))
    return {"items": items, "unread": unread, "pending_approvals": pending}


@router.post("/api/w/{ws}/notifications/read", role="viewer")
async def read_notifications(ctx: Ctx) -> None:
    ctx.engine.db.execute("UPDATE notifications SET read_at = ? WHERE workspace_id = ? AND user_id = ? AND read_at IS NULL",
                          (now(), ctx.ws_id, ctx.user_id))


@router.get("/api/w/{ws}/audit", role="admin")
async def audit_log(ctx: Ctx) -> list[dict[str, Any]]:
    before = ctx.request.query_params.get("before")
    params: list[Any] = [ctx.ws_id]
    sql = ("SELECT a.*, u.name AS user_name FROM audit_log a LEFT JOIN users u ON u.id = a.user_id WHERE "
           "a.workspace_id = ?")
    if before and before.isdigit():
        sql += " AND a.id < ?"
        params.append(int(before))
    rows = ctx.engine.db.all(sql + " ORDER BY a.id DESC LIMIT 200", params)
    for r in rows:
        r["detail"] = loads(r["detail"], None)
    return rows


@router.get("/api/w/{ws}/usage", role="admin")
async def usage(ctx: Ctx) -> dict[str, Any]:
    since = now() - 30 * 86400
    rows = ctx.engine.db.all("SELECT u.model, p.label AS provider, SUM(input_tokens) AS input_tokens, "
                             "SUM(output_tokens) AS output_tokens, SUM(cache_read) AS cache_read, COUNT(*) AS calls FROM usage u LEFT JOIN llm_providers p "
                             "ON p.id = u.provider_id WHERE u.workspace_id = ? AND u.created_at >= ? GROUP BY u.model, p.label "
                             "ORDER BY calls DESC", (ctx.ws_id, since))
    daily = ctx.engine.db.all("SELECT date(created_at, 'unixepoch') AS day, SUM(input_tokens + output_tokens) AS tokens "
                              "FROM usage WHERE workspace_id = ? AND created_at >= ? GROUP BY day ORDER BY day",
                              (ctx.ws_id, since))
    return {"by_model": rows, "daily": daily}
