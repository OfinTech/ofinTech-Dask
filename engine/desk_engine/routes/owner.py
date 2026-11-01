"""The Owner Control Center API: ``/api/owner/*``.

Every route names the platform permission it needs (``platform/owner.py``: deny by default, two platform roles). Every
mutation is ``fresh=True`` — the person must have re-entered their password within the step-up window — and is
audited by the service it calls. Critical changes (billing credentials, plans, security policy, delegating
administration) go through a second owner's approval when the ``security.dual_approval`` policy is on.

What is deliberately absent: any route that returns a workspace's ledgers, documents, files, conversations or
credentials. The dashboard sees accounts, plans, usage figures and security events — platform administration, not
customer data. The one bridge, support access, grants the viewer role on one workspace for a bounded time and is
listed, audited and visible to that workspace's own administrators.
"""

from __future__ import annotations

import datetime as dt
from typing import Any, Literal

from pydantic import BaseModel, Field
from starlette.responses import PlainTextResponse

from ..config import VERSION
from ..db import dumps, loads, now
from ..errors import Forbidden, Invalid, NotFound
from ..platform import analytics, billing, coupons, email as email_svc, mfa, notifications, owner, plans, sales, storage, support
from ..platform.settings import FEATURE_FLAG_KEYS, REGISTRY, clear_secret, get_setting, group_view, secret_status, set_setting
from ..web import Ctx, Router

router = Router()


def need(ctx: Ctx, permission: str) -> str:
    return owner.require(ctx, permission)


def _page(ctx: Ctx, default: int = 50, cap: int = 200) -> tuple[int, int]:
    q = ctx.request.query_params
    try:
        limit = max(1, min(cap, int(q.get("limit", default))))
        offset = max(0, int(q.get("offset", 0)))
    except ValueError:
        raise Invalid("limit and offset are whole numbers.") from None
    return limit, offset


def _range(ctx: Ctx) -> tuple[str | None, float | None, float | None]:
    q = ctx.request.query_params
    period = q.get("period")
    start, end = q.get("from"), q.get("to")
    try:
        return period, float(start) if start else None, float(end) if end else None
    except ValueError:
        raise Invalid("from and to are timestamps.") from None


def _user_summary(engine: Any, u: dict[str, Any]) -> dict[str, Any]:
    """What the directory shows about an account: never a password hash, token, secret or workspace content."""
    plan = plans.plan_for(engine, u["id"])
    sub = plans.current_subscription(engine, u["id"])
    return {"id": u["id"], "name": u["name"], "email": u["email"], "created_at": u["created_at"],
            "signup_method": u.get("signup_method") or ("google" if u.get("google_sub") else "password"),
            "email_verified": bool(u.get("email_verified_at")), "disabled": bool(u["disabled"]),
            "suspended_reason": u.get("suspended_reason"), "must_reset_password": bool(u.get("must_reset_password")),
            "mfa_enabled": bool(u.get("mfa_enabled_at")), "restrictions": loads(u.get("restrictions"), {}) or {},
            "language": u.get("language"), "last_login_at": u.get("last_login_at"),
            "last_activity_at": engine.db.scalar("SELECT MAX(last_seen) FROM sessions WHERE user_id = ?", (u["id"],)),
            "active_sessions": engine.db.scalar("SELECT COUNT(*) FROM sessions WHERE user_id = ?", (u["id"],)) or 0,
            "workspaces": engine.db.scalar("SELECT COUNT(*) FROM members WHERE user_id = ?", (u["id"],)) or 0,
            "owned_workspaces": engine.db.scalar("SELECT COUNT(*) FROM members WHERE user_id = ? AND role = 'owner'", (u["id"],)) or 0,
            "plan": plan["code"] if plan else None, "subscription_status": sub["status"] if sub else None,
            "platform_role": owner.platform_role(engine, u["id"]),
            "storage_bytes": sum(storage.usage_bytes(engine, m["workspace_id"])["total"] for m in
                                 engine.db.all("SELECT workspace_id FROM members WHERE user_id = ? AND role = 'owner'", (u["id"],)))}


# --- overview ----------------------------------------------------------------------------------------------------------

@router.get("/api/owner/overview")
async def overview(ctx: Ctx) -> dict[str, Any]:
    role = need(ctx, "dashboard.read")
    period, start, end = _range(ctx)
    k = analytics.kpis(ctx.engine, period, start, end)
    alerts = notifications.list_for_user(ctx.engine, ctx.user_id, unread_only=True, limit=10)
    return {"role": role, "permissions": owner.permissions_for(role), "kpis": k, "alerts": alerts,
            "pending_changes": owner.pending_changes(ctx.engine) if role == "owner" else [],
            "billing": billing.status(ctx.engine) if role else None, "email": email_svc.provider_status(ctx.engine),
            "hosted": ctx.engine.settings.hosted, "version": VERSION,
            "definitions": analytics.METRIC_DEFINITIONS}


@router.get("/api/owner/health")
async def health(ctx: Ctx) -> dict[str, Any]:
    need(ctx, "dashboard.read")
    info = await ctx.engine.storage.check()
    failed_mail = ctx.engine.db.scalar("SELECT COUNT(*) FROM email_jobs WHERE status = 'failed'") or 0
    return {"storage": {"ok": True, "kind": ctx.engine.settings.storage.get("kind"), "info": str(info)[:200]},
            "database": {"ok": True, "schema_version": ctx.engine.db.scalar("PRAGMA user_version")},
            "email": {"configured": email_svc.mailer(ctx.engine).configured, "failed_jobs": failed_mail},
            "billing": billing.status(ctx.engine)["ready"], "maintenance_mode": bool(get_setting(ctx.engine, "platform.maintenance_mode")),
            "incidents": ctx.engine.db.all("SELECT action, target, created_at, detail FROM audit_log WHERE result = 'failed' ORDER BY id DESC LIMIT 20")}


# --- users -------------------------------------------------------------------------------------------------------------

@router.get("/api/owner/users")
async def users(ctx: Ctx) -> dict[str, Any]:
    need(ctx, "users.read")
    q = ctx.request.query_params
    where, params = ["u.is_local = 0"], []
    if q.get("q"):
        where.append("(u.email LIKE ? OR u.name LIKE ? OR u.id = ?)")
        like = f"%{q['q'].strip()}%"
        params += [like, like, q["q"].strip()]
    if q.get("status") == "suspended":
        where.append("u.disabled = 1")
    elif q.get("status") == "active":
        where.append("u.disabled = 0")
    if q.get("plan"):
        where.append("u.id IN (SELECT s.user_id FROM subscriptions s JOIN plans p ON p.id = s.plan_id WHERE p.code = ? AND s.status IN "
                     "('trialing','active','past_due'))" if q["plan"] != "free" else
                     "u.id NOT IN (SELECT user_id FROM subscriptions WHERE status IN ('trialing','active','past_due'))")
        if q["plan"] != "free":
            params.append(q["plan"])
    if q.get("mfa") in ("on", "off"):
        where.append("u.mfa_enabled_at IS NOT NULL" if q["mfa"] == "on" else "u.mfa_enabled_at IS NULL")
    if q.get("verified") in ("yes", "no"):
        where.append("u.email_verified_at IS NOT NULL" if q["verified"] == "yes" else "u.email_verified_at IS NULL")
    limit, offset = _page(ctx)
    sql = f"FROM users u WHERE {' AND '.join(where)}"
    total = ctx.engine.db.scalar(f"SELECT COUNT(*) {sql}", params) or 0
    rows = ctx.engine.db.all(f"SELECT u.* {sql} ORDER BY u.created_at DESC LIMIT ? OFFSET ?", [*params, limit, offset])
    return {"total": total, "items": [_user_summary(ctx.engine, u) for u in rows]}


def _load_user(ctx: Ctx) -> dict[str, Any]:
    u = ctx.engine.db.one("SELECT * FROM users WHERE id = ? AND is_local = 0", (ctx.request.path_params["uid"],))
    if u is None:
        raise NotFound("User not found.")
    return u


@router.get("/api/owner/users/{uid}")
async def user_detail(ctx: Ctx) -> dict[str, Any]:
    need(ctx, "users.read")
    u = _load_user(ctx)
    e = ctx.engine
    return {**_user_summary(e, u),
            "memberships": e.db.all("SELECT m.workspace_id, m.role, m.created_at, w.name FROM members m JOIN workspaces w ON w.id = m.workspace_id "
                                    "WHERE m.user_id = ? ORDER BY w.name", (u["id"],)),
            "subscriptions": plans.subscription_history(e, u["id"]),
            "entitlements": plans.entitlements_for(e, u["id"]),
            "overrides": e.db.one("SELECT * FROM entitlement_overrides WHERE user_id = ?", (u["id"],)),
            "invoices": billing.list_invoices(e, user_id=u["id"], limit=24),
            "sessions": e.db.all("SELECT substr(id, 1, 12) AS id, created_at, last_seen, user_agent, mfa_at FROM sessions WHERE user_id = ? ORDER BY last_seen DESC", (u["id"],)),
            "auth_history": e.db.all("SELECT action, user_id AS actor_id, decision, result, detail, created_at, request_id FROM audit_log WHERE (user_id = ? OR target = ?) "
                                     "AND (action LIKE 'user.%' OR action LIKE 'session.%') ORDER BY id DESC LIMIT 50", (u["id"], u["id"])),
            "security_events": e.db.all("SELECT action, target, decision, result, workspace_id, created_at FROM audit_log WHERE user_id = ? AND decision = 'denied' "
                                        "ORDER BY id DESC LIMIT 50", (u["id"],))}


class SuspendIn(BaseModel):
    reason: str = Field(min_length=3, max_length=300)


@router.post("/api/owner/users/{uid}/suspend", body=SuspendIn, fresh=True)
async def suspend_user(ctx: Ctx) -> dict[str, Any]:
    need(ctx, "users.manage")
    u = _load_user(ctx)
    if owner.platform_role(ctx.engine, u["id"]) == "owner" and need(ctx, "users.manage") != "owner":
        raise Forbidden("Only a platform owner can suspend another owner.")
    if u["id"] == ctx.user_id:
        raise Invalid("You cannot suspend your own account.")
    with ctx.engine.db.tx() as c:
        c.execute("UPDATE users SET disabled = 1, suspended_reason = ? WHERE id = ?", (ctx.body.reason.strip(), u["id"]))
        c.execute("DELETE FROM sessions WHERE user_id = ?", (u["id"],))
    ctx.engine.audit(None, ctx.user_id, "user.suspend", u["id"], {"reason": ctx.body.reason.strip()}, resource_type="user", decision="allowed", result="ok")
    return _user_summary(ctx.engine, ctx.engine.db.one("SELECT * FROM users WHERE id = ?", (u["id"],)))


@router.post("/api/owner/users/{uid}/reactivate", fresh=True)
async def reactivate_user(ctx: Ctx) -> dict[str, Any]:
    need(ctx, "users.manage")
    u = _load_user(ctx)
    ctx.engine.db.execute("UPDATE users SET disabled = 0, suspended_reason = NULL WHERE id = ?", (u["id"],))
    ctx.engine.audit(None, ctx.user_id, "user.reactivate", u["id"], None, resource_type="user", decision="allowed", result="ok")
    return _user_summary(ctx.engine, ctx.engine.db.one("SELECT * FROM users WHERE id = ?", (u["id"],)))


@router.post("/api/owner/users/{uid}/sign-out", fresh=True)
async def sign_out_user(ctx: Ctx) -> dict[str, Any]:
    need(ctx, "users.manage")
    u = _load_user(ctx)
    with ctx.engine.db.tx() as c:
        n = c.execute("DELETE FROM sessions WHERE user_id = ?", (u["id"],)).rowcount
    ctx.engine.audit(None, ctx.user_id, "session.revoked", u["id"], {"by": "platform_admin", "count": n}, resource_type="session",
                     decision="allowed", result="signed_out")
    return {"revoked": n}


@router.post("/api/owner/users/{uid}/require-password-reset", fresh=True)
async def require_reset(ctx: Ctx) -> dict[str, Any]:
    from ..platform.email import enqueue
    from ..security import new_token, token_hash

    need(ctx, "users.manage")
    u = _load_user(ctx)

    token = new_token(32)
    with ctx.engine.db.tx() as c:
        c.execute("UPDATE users SET must_reset_password = 1 WHERE id = ?", (u["id"],))
        c.execute("DELETE FROM sessions WHERE user_id = ?", (u["id"],))
        c.execute("INSERT INTO password_resets (token_hash, user_id, created_at, expires_at) VALUES (?,?,?,?)",
                  (token_hash(token), u["id"], now(), now() + 24 * 3600))
    link = f"{ctx.engine.settings.public_url.rstrip('/')}/#/reset/{token}"
    enqueue(ctx.engine, to=u["email"], template_key="password_reset", lang=u.get("language") or "en", vars={"name": u["name"], "link": link},
            category="security")
    ctx.engine.audit(None, ctx.user_id, "user.require_reset", u["id"], None, resource_type="user", decision="allowed", result="ok")
    # the link is returned to the administrator only when e-mail is not configured (as the existing reset-link flow does)
    return {"ok": True, "emailed": email_svc.mailer(ctx.engine).configured, "link": None if email_svc.mailer(ctx.engine).configured else link}


class RestrictionsIn(BaseModel):
    mfa_required: bool | None = None
    read_only: bool | None = None
    no_new_workspaces: bool | None = None


@router.put("/api/owner/users/{uid}/restrictions", body=RestrictionsIn, fresh=True)
async def set_restrictions(ctx: Ctx) -> dict[str, Any]:
    need(ctx, "users.manage")
    u = _load_user(ctx)
    current = loads(u.get("restrictions"), {}) or {}
    for k, v in ctx.body.model_dump().items():
        if v is None:
            continue
        if v:
            current[k] = True
        else:
            current.pop(k, None)
    ctx.engine.db.update("users", u["id"], {"restrictions": dumps(current)})
    ctx.engine.audit(None, ctx.user_id, "user.restrictions", u["id"], current, resource_type="user", decision="allowed", result="ok")
    return {"restrictions": current}


@router.post("/api/owner/users/{uid}/reset-mfa", fresh=True)
async def reset_user_mfa(ctx: Ctx) -> dict[str, Any]:
    need(ctx, "users.manage")
    u = _load_user(ctx)
    mfa.admin_reset(ctx.engine, user_id=u["id"], by=ctx.user_id)
    return {"ok": True}


class OverridesIn(BaseModel):
    overrides: dict[str, Any] = Field(default_factory=dict)
    note: str | None = Field(default=None, max_length=300)
    expires_at: float | None = None


@router.put("/api/owner/users/{uid}/entitlements", body=OverridesIn, fresh=True)
async def set_entitlements(ctx: Ctx) -> dict[str, Any]:
    need(ctx, "subscriptions.manage")
    u = _load_user(ctx)
    plans.set_overrides(ctx.engine, user_id=u["id"], overrides=ctx.body.overrides, note=ctx.body.note, by=ctx.user_id,
                        expires_at=ctx.body.expires_at)
    return plans.entitlements_for(ctx.engine, u["id"])


class AssignIn(BaseModel):
    plan_id: str = Field(max_length=60)
    interval: Literal["month", "year"] = "month"
    months: int | None = Field(default=None, ge=1, le=120)
    note: str | None = Field(default=None, max_length=300)


@router.post("/api/owner/users/{uid}/subscription", body=AssignIn, fresh=True)
async def assign_subscription(ctx: Ctx) -> dict[str, Any]:
    need(ctx, "subscriptions.manage")
    u = _load_user(ctx)
    return plans.assign(ctx.engine, user_id=u["id"], plan_id=ctx.body.plan_id, by=ctx.user_id, interval=ctx.body.interval,
                        note=ctx.body.note, months=ctx.body.months)


# --- subscriptions --------------------------------------------------------------------------------------------------

@router.get("/api/owner/subscriptions")
async def subscriptions(ctx: Ctx) -> dict[str, Any]:
    need(ctx, "subscriptions.read")
    q = ctx.request.query_params
    where, params = ["1=1"], []
    if q.get("status"):
        where.append("s.status = ?")
        params.append(q["status"])
    if q.get("plan"):
        where.append("p.code = ?")
        params.append(q["plan"])
    if q.get("q"):
        where.append("(u.email LIKE ? OR s.id = ? OR s.stripe_subscription_id = ?)")
        params += [f"%{q['q']}%", q["q"], q["q"]]
    limit, offset = _page(ctx)
    sql = f"FROM subscriptions s JOIN plans p ON p.id = s.plan_id JOIN users u ON u.id = s.user_id WHERE {' AND '.join(where)}"
    total = ctx.engine.db.scalar(f"SELECT COUNT(*) {sql}", params) or 0
    rows = ctx.engine.db.all(f"SELECT s.*, p.code AS plan_code, p.name AS plan_name, p.monthly_price, p.annual_price, p.currency, u.email {sql} "
                             "ORDER BY s.updated_at DESC LIMIT ? OFFSET ?", [*params, limit, offset])
    return {"total": total, "items": rows, "mrr": billing.mrr(ctx.engine)}


@router.post("/api/owner/subscriptions/{sid}/cancel", fresh=True)
async def cancel_subscription(ctx: Ctx) -> dict[str, Any]:
    need(ctx, "subscriptions.manage")
    sid = ctx.request.path_params["sid"]
    sub = ctx.engine.db.one("SELECT * FROM subscriptions WHERE id = ?", (sid,))
    if sub is None:
        raise NotFound("Subscription not found.")
    if sub["source"] == "stripe" and billing.enabled(ctx.engine):
        c = billing.client(ctx.engine)
        stripe_sub = await c.cancel_subscription(sub["stripe_subscription_id"], at_period_end=True)
        billing._upsert_from_stripe(ctx.engine, stripe_sub, event_created=now())
        ctx.engine.audit(None, ctx.user_id, "subscription.cancel", sid, {"at_period_end": True, "via": "stripe"}, resource_type="subscription",
                         decision="allowed", result="ok")
        return ctx.engine.db.one("SELECT * FROM subscriptions WHERE id = ?", (sid,))
    return plans.cancel(ctx.engine, sid, by=ctx.user_id, at_period_end=ctx.request.query_params.get("now") != "1")


class ChangePlanIn(BaseModel):
    plan_id: str = Field(max_length=60)


@router.post("/api/owner/subscriptions/{sid}/change", body=ChangePlanIn, fresh=True)
async def change_subscription(ctx: Ctx) -> dict[str, Any]:
    need(ctx, "subscriptions.manage")
    sid = ctx.request.path_params["sid"]
    sub = ctx.engine.db.one("SELECT * FROM subscriptions WHERE id = ?", (sid,))
    if sub is None:
        raise NotFound("Subscription not found.")
    if sub["source"] == "stripe":
        raise Invalid("A Stripe subscription is changed by the customer through the billing portal, or reconciled here.", code="stripe_managed")
    return plans.schedule_change(ctx.engine, sid, ctx.body.plan_id, by=ctx.user_id)


@router.post("/api/owner/subscriptions/{sid}/reconcile", fresh=True)
async def reconcile_subscription(ctx: Ctx) -> dict[str, Any]:
    need(ctx, "subscriptions.manage")
    return await billing.reconcile(ctx.engine, ctx.request.path_params["sid"], by=ctx.user_id)


@router.get("/api/owner/invoices")
async def invoices(ctx: Ctx) -> dict[str, Any]:
    need(ctx, "billing.read")
    limit, _ = _page(ctx, 100)
    return {"items": billing.list_invoices(ctx.engine, limit=limit)}


# --- workspaces -------------------------------------------------------------------------------------------------

def _ws_summary(engine: Any, w: dict[str, Any]) -> dict[str, Any]:
    owner_id = plans.workspace_owner(engine, w["id"])
    o = engine.db.one("SELECT id, email, name FROM users WHERE id = ?", (owner_id,)) if owner_id else None
    ent = plans.workspace_entitlements(engine, w["id"])
    return {"id": w["id"], "name": w["name"], "created_at": w["created_at"], "base_currency": w["base_currency"], "country": w["country"],
            "owner": o, "members": engine.db.scalar("SELECT COUNT(*) FROM members WHERE workspace_id = ?", (w["id"],)) or 0,
            "plan": ent.get("plan_code"), "state": support.get_state(engine, w["id"]), "storage": storage.summary(engine, w["id"]),
            "egress_policy": w.get("egress_policy"),
            "last_activity_at": engine.db.scalar("SELECT MAX(created_at) FROM audit_log WHERE workspace_id = ?", (w["id"],))}


@router.get("/api/owner/workspaces")
async def workspaces(ctx: Ctx) -> dict[str, Any]:
    need(ctx, "workspaces.read")
    q = ctx.request.query_params
    where, params = ["1=1"], []
    if q.get("q"):
        where.append("(w.name LIKE ? OR w.id = ?)")
        params += [f"%{q['q'].strip()}%", q["q"].strip()]
    if q.get("state"):
        where.append("COALESCE((SELECT status FROM workspace_state s WHERE s.workspace_id = w.id), 'active') = ?")
        params.append(q["state"])
    limit, offset = _page(ctx)
    sql = f"FROM workspaces w WHERE {' AND '.join(where)}"
    total = ctx.engine.db.scalar(f"SELECT COUNT(*) {sql}", params) or 0
    rows = ctx.engine.db.all(f"SELECT w.* {sql} ORDER BY w.created_at DESC LIMIT ? OFFSET ?", [*params, limit, offset])
    return {"total": total, "items": [_ws_summary(ctx.engine, w) for w in rows]}


def _load_ws(ctx: Ctx) -> dict[str, Any]:
    w = ctx.engine.db.one("SELECT * FROM workspaces WHERE id = ?", (ctx.request.path_params["wid"],))
    if w is None:
        raise NotFound("Workspace not found.")
    return w


@router.get("/api/owner/workspaces/{wid}")
async def workspace_detail(ctx: Ctx) -> dict[str, Any]:
    need(ctx, "workspaces.read")
    w = _load_ws(ctx)
    e = ctx.engine
    # usage figures and membership only — no datasets, documents, threads or files are listed here
    return {**_ws_summary(e, w),
            "members": e.db.all("SELECT m.user_id, m.role, m.created_at, u.email, u.name FROM members m JOIN users u ON u.id = m.user_id "
                                "WHERE m.workspace_id = ? ORDER BY m.role DESC, u.email", (w["id"],)),
            "counts": {"datasets": e.db.scalar("SELECT COUNT(*) FROM datasets WHERE workspace_id = ?", (w["id"],)),
                       "documents": e.db.scalar("SELECT COUNT(*) FROM documents WHERE workspace_id = ?", (w["id"],)),
                       "files": e.db.scalar("SELECT COUNT(*) FROM files WHERE workspace_id = ?", (w["id"],)),
                       "threads": e.db.scalar("SELECT COUNT(*) FROM threads WHERE workspace_id = ?", (w["id"],)),
                       "connections": e.db.scalar("SELECT COUNT(*) FROM connections WHERE workspace_id = ?", (w["id"],)),
                       "agent_runs_30d": e.db.scalar("SELECT COUNT(*) FROM agent_runs WHERE workspace_id = ? AND created_at > ?", (w["id"], now() - 30 * 86400))},
            "ai_tokens_30d": e.db.scalar("SELECT COALESCE(SUM(input_tokens + output_tokens), 0) FROM usage WHERE workspace_id = ? AND created_at > ?",
                                         (w["id"], now() - 30 * 86400)),
            "support_grants": [g for g in support.list_support(e, active_only=False) if g["workspace_id"] == w["id"]][:20],
            "security_events": e.db.all("SELECT action, user_id, decision, result, created_at FROM audit_log WHERE workspace_id = ? AND decision = 'denied' "
                                        "ORDER BY id DESC LIMIT 30", (w["id"],))}


class StateIn(BaseModel):
    status: Literal["active", "read_only", "suspended", "archived"]
    reason: str | None = Field(default=None, max_length=300)


@router.post("/api/owner/workspaces/{wid}/state", body=StateIn, fresh=True)
async def workspace_state(ctx: Ctx) -> dict[str, Any]:
    need(ctx, "workspaces.manage")
    w = _load_ws(ctx)
    state = support.set_state(ctx.engine, w["id"], ctx.body.status, reason=ctx.body.reason, by=ctx.user_id)
    if ctx.body.status != "active":
        ctx.engine.notify_role(w["id"], "admin", "security", f"The platform owner set this workspace to {ctx.body.status}"
                               + (f": {ctx.body.reason}" if ctx.body.reason else "."), "#/settings")
    return state


class MemberRoleIn(BaseModel):
    role: Literal["viewer", "editor", "admin", "owner"]


@router.patch("/api/owner/workspaces/{wid}/members/{uid}", body=MemberRoleIn, fresh=True)
async def workspace_member_role(ctx: Ctx) -> dict[str, Any]:
    need(ctx, "workspaces.manage")
    w = _load_ws(ctx)
    uid = ctx.request.path_params["uid"]
    if ctx.engine.db.execute("UPDATE members SET role = ? WHERE workspace_id = ? AND user_id = ?", (ctx.body.role, w["id"], uid)) != 1:
        raise NotFound("Member not found.")
    if ctx.body.role == "viewer":
        ctx.engine.approvals.expire_for_member(w["id"], uid, "demoted")
    ctx.engine.audit(w["id"], ctx.user_id, "member.role", uid, {"role": ctx.body.role, "by": "platform_admin"}, resource_type="member",
                     decision="allowed", result="ok")
    return {"ok": True}


@router.delete("/api/owner/workspaces/{wid}/members/{uid}", fresh=True)
async def workspace_member_remove(ctx: Ctx) -> None:
    need(ctx, "workspaces.manage")
    w = _load_ws(ctx)
    uid = ctx.request.path_params["uid"]
    owners = ctx.engine.db.scalar("SELECT COUNT(*) FROM members WHERE workspace_id = ? AND role = 'owner'", (w["id"],))
    role = ctx.engine.db.scalar("SELECT role FROM members WHERE workspace_id = ? AND user_id = ?", (w["id"], uid))
    if role == "owner" and owners <= 1:
        raise Invalid("A workspace keeps at least one owner. Transfer ownership first.")
    if ctx.engine.db.execute("DELETE FROM members WHERE workspace_id = ? AND user_id = ?", (w["id"], uid)) != 1:
        raise NotFound("Member not found.")
    ctx.engine.approvals.expire_for_member(w["id"], uid, "removed")
    ctx.engine.audit(w["id"], ctx.user_id, "member.remove", uid, {"by": "platform_admin"}, resource_type="member", decision="allowed", result="ok")


@router.delete("/api/owner/workspaces/{wid}", fresh=True)
async def workspace_delete(ctx: Ctx) -> None:
    need(ctx, "workspaces.delete")
    w = _load_ws(ctx)
    state = support.get_state(ctx.engine, w["id"])
    if state["status"] != "archived":
        raise Invalid("Archive the workspace first; deletion is the second step and cannot be undone.", code="archive_first")
    from ..data.store import list_datasets, table_name
    from ..storage import delete_prefix

    for ds in list_datasets(ctx.engine, w["id"]):
        ctx.engine.db.execute(f"DROP TABLE IF EXISTS {table_name(ds['id'])}")
    with ctx.engine.db.tx() as c:
        c.execute("DELETE FROM secrets WHERE workspace_id = ?", (w["id"],))
        c.execute("DELETE FROM workspaces WHERE id = ?", (w["id"],))
    for area in ("documents", "uploads", "files"):
        await delete_prefix(ctx.engine.storage, f"{area}/{w['id']}")
    ctx.engine.audit(None, ctx.user_id, "workspace.delete", w["id"], {"by": "platform_admin", "name": w["name"]}, resource_type="workspace",
                     decision="allowed", result="ok")


class SupportIn(BaseModel):
    reason: str = Field(min_length=5, max_length=500)
    hours: int = Field(default=4, ge=1, le=support.MAX_SUPPORT_HOURS)


@router.post("/api/owner/workspaces/{wid}/support-access", body=SupportIn, fresh=True)
async def grant_support(ctx: Ctx) -> dict[str, Any]:
    need(ctx, "support.access")
    w = _load_ws(ctx)
    if ctx.engine.db.one("SELECT 1 FROM members WHERE workspace_id = ? AND user_id = ?", (w["id"], ctx.user_id)):
        raise Invalid("You are already a member of this workspace.")
    return support.grant_support(ctx.engine, admin_id=ctx.user_id, ws_id=w["id"], reason=ctx.body.reason, hours=ctx.body.hours)


@router.get("/api/owner/support-access")
async def support_grants(ctx: Ctx) -> dict[str, Any]:
    need(ctx, "support.access")
    return {"items": support.list_support(ctx.engine, active_only=ctx.request.query_params.get("all") != "1")}


@router.delete("/api/owner/support-access/{gid}", fresh=True)
async def revoke_support(ctx: Ctx) -> None:
    need(ctx, "support.access")
    support.revoke_support(ctx.engine, grant_id=ctx.request.path_params["gid"], by=ctx.user_id)


# --- plans -------------------------------------------------------------------------------------------------------------

@router.get("/api/owner/plans")
async def list_plans(ctx: Ctx) -> dict[str, Any]:
    need(ctx, "plans.read")
    items = plans.list_plans(ctx.engine)
    for p in items:
        p["subscribers"] = ctx.engine.db.scalar("SELECT COUNT(*) FROM subscriptions WHERE plan_id = ? AND status IN ('trialing','active','past_due')", (p["id"],)) or 0
    return {"items": items, "features": plans.FEATURES, "enterprise": plans.ENTERPRISE_CAPABILITIES, "hosted": ctx.engine.settings.hosted}


class PlanIn(BaseModel):
    model_config = {"extra": "forbid"}
    code: str | None = None
    name: str | None = None
    description: str | None = None
    monthly_price: int | None = None
    annual_price: int | None = None
    currency: str | None = None
    max_workspaces: int | None = None
    storage_quota_mb: int | None = None
    max_members: int | None = None
    features: dict[str, Any] | None = None
    trial_days: int | None = None
    billing_interval: str | None = None
    visibility: str | None = None
    contact_sales: bool | None = None
    status: str | None = None
    is_default: bool | None = None
    position: int | None = None
    stripe_product_id: str | None = None
    stripe_price_monthly: str | None = None
    stripe_price_annual: str | None = None


def _critical(ctx: Ctx, kind: str, payload: dict[str, Any]) -> dict[str, Any] | None:
    """When dual approval is on, a critical change is parked for a second owner and nothing is applied now."""
    if owner.dual_approval_on(ctx.engine):
        row = owner.request_change(ctx.engine, kind=kind, payload=payload, requested_by=ctx.user_id)
        return {"pending_approval": True, "change_request": row["id"]}
    return None


@router.post("/api/owner/plans", body=PlanIn, fresh=True)
async def create_plan(ctx: Ctx) -> dict[str, Any]:
    need(ctx, "plans.manage")
    values = {k: v for k, v in ctx.body.model_dump().items() if v is not None}
    return plans.create_plan(ctx.engine, values, by=ctx.user_id)


@router.patch("/api/owner/plans/{pid}", body=PlanIn, fresh=True)
async def update_plan(ctx: Ctx) -> dict[str, Any]:
    need(ctx, "plans.manage")
    values = {k: v for k, v in ctx.body.model_dump().items() if v is not None}
    pid = ctx.request.path_params["pid"]
    plans.get_plan(ctx.engine, pid)
    price_change = any(k in values for k in ("monthly_price", "annual_price", "currency", "max_workspaces", "storage_quota_mb", "features"))
    if price_change:
        parked = _critical(ctx, "plan.update", {"plan_id": pid, "values": values})
        if parked:
            return parked
    return plans.update_plan(ctx.engine, pid, values, by=ctx.user_id)


@router.post("/api/owner/plans/{pid}/sync-stripe", fresh=True)
async def sync_plan(ctx: Ctx) -> dict[str, Any]:
    need(ctx, "plans.manage")
    return await billing.sync_plan(ctx.engine, ctx.request.path_params["pid"], by=ctx.user_id)


# --- billing -----------------------------------------------------------------------------------------------------------

@router.get("/api/owner/billing")
async def billing_status(ctx: Ctx) -> dict[str, Any]:
    need(ctx, "billing.read")
    return {**billing.status(ctx.engine), "events": billing.list_events(ctx.engine, 50),
            "plans_unsynced": [p["code"] for p in plans.list_plans(ctx.engine) if (p["monthly_price"] or p["annual_price"]) and not p["contact_sales"]
                               and not (p.get("stripe_price_monthly") or p.get("stripe_price_annual"))]}


class BillingSettingsIn(BaseModel):
    enabled: bool | None = None
    mode: Literal["test", "live"] | None = None
    publishable_key: str | None = Field(default=None, max_length=200)
    secret_key: str | None = Field(default=None, max_length=300)
    webhook_secret: str | None = Field(default=None, max_length=300)
    currencies: list[str] | None = None
    portal_enabled: bool | None = None
    tax_enabled: bool | None = None
    grace_days: int | None = None
    allow_promotion_codes: bool | None = None


@router.put("/api/owner/billing", body=BillingSettingsIn, fresh=True)
async def billing_configure(ctx: Ctx) -> dict[str, Any]:
    need(ctx, "billing.configure")
    b = ctx.body.model_dump(exclude_none=True)
    if b.get("publishable_key") and not b["publishable_key"].startswith(("pk_test_", "pk_live_")):
        raise Invalid("The publishable key starts with pk_test_ or pk_live_.")
    if b.get("secret_key") and not b["secret_key"].startswith(("sk_test_", "sk_live_", "rk_test_", "rk_live_")):
        raise Invalid("The secret key starts with sk_test_, sk_live_, rk_test_ or rk_live_.")
    if b.get("webhook_secret") and not b["webhook_secret"].startswith("whsec_"):
        raise Invalid("The webhook signing secret starts with whsec_.")
    parked = _critical(ctx, "billing.configure", {k: ("[secret]" if k in ("secret_key", "webhook_secret") else v) for k, v in b.items()})
    if parked:
        # the secrets themselves are kept sealed on the request so the approver never sees them
        ctx.engine.secrets.put("change_request", {k: b[k] for k in ("secret_key", "webhook_secret") if k in b}, workspace_id=None,
                               secret_id=parked["change_request"])
        return parked
    _apply_billing(ctx.engine, b, by=ctx.user_id)
    return billing.status(ctx.engine)


def _apply_billing(engine: Any, b: dict[str, Any], *, by: str) -> None:
    mapping = {"enabled": "stripe.enabled", "mode": "stripe.mode", "publishable_key": "stripe.publishable_key", "currencies": "stripe.currencies",
               "portal_enabled": "stripe.portal_enabled", "tax_enabled": "stripe.tax_enabled", "grace_days": "billing.grace_days",
               "allow_promotion_codes": "billing.allow_promotion_codes", "secret_key": "stripe.secret_key", "webhook_secret": "stripe.webhook_secret"}
    for k, v in b.items():
        if k in ("secret_key", "webhook_secret") and v == "":
            clear_secret(engine, mapping[k], by=by)
        else:
            set_setting(engine, mapping[k], v, by=by)


@router.post("/api/owner/billing/test", fresh=True)
async def billing_test(ctx: Ctx) -> dict[str, Any]:
    need(ctx, "billing.configure")
    result = await billing.health(ctx.engine)
    ctx.engine.audit(None, ctx.user_id, "billing.test", None, result, resource_type="billing", decision="allowed", result="ok" if result["ok"] else "failed")
    return result


@router.get("/api/owner/billing/events")
async def billing_events(ctx: Ctx) -> dict[str, Any]:
    need(ctx, "billing.read")
    limit, _ = _page(ctx, 100)
    return {"items": billing.list_events(ctx.engine, limit, ctx.request.query_params.get("status"))}


@router.post("/api/owner/billing/events/{eid}/retry", fresh=True)
async def billing_retry(ctx: Ctx) -> dict[str, Any]:
    need(ctx, "billing.configure")
    row = ctx.engine.db.one("SELECT id, payload FROM stripe_events WHERE id = ?", (ctx.request.path_params["eid"],))
    if row is None:
        raise NotFound("Event not found.")
    from ..platform.stripe import parse_event

    ctx.engine.db.update("stripe_events", row["id"], {"attempts": 0, "status": "failed"})
    return billing.process_event(ctx.engine, parse_event(row["payload"].encode()))


# --- coupons -----------------------------------------------------------------------------------------------------------

@router.get("/api/owner/coupons")
async def list_coupons(ctx: Ctx) -> dict[str, Any]:
    need(ctx, "coupons.read")
    return {"items": coupons.list_coupons(ctx.engine), "performance": coupons.performance(ctx.engine)}


class CouponIn(BaseModel):
    model_config = {"extra": "forbid"}
    code: str
    percent_off: float | None = None
    amount_off: int | None = None
    currency: str | None = None
    duration: Literal["once", "repeating", "forever"] = "once"
    duration_months: int | None = None
    expires_at: float | None = None
    max_redemptions: int | None = None
    per_customer_limit: int | None = 1
    plan_ids: list[str] = Field(default_factory=list)
    first_time_only: bool = False
    min_amount: int | None = None


@router.post("/api/owner/coupons", body=CouponIn, fresh=True)
async def create_coupon(ctx: Ctx) -> dict[str, Any]:
    need(ctx, "coupons.manage")
    c = coupons.create(ctx.engine, ctx.body.model_dump(), by=ctx.user_id)
    if billing.enabled(ctx.engine):
        try:
            c = await coupons.sync_to_stripe(ctx.engine, c["id"], by=ctx.user_id)
        except Exception as exc:  # noqa: BLE001 - the coupon exists locally; the owner can sync again
            c["sync_error"] = getattr(exc, "message", str(exc))
    return c


class CouponPatch(BaseModel):
    model_config = {"extra": "forbid"}
    active: bool | None = None
    expires_at: float | None = None
    plan_ids: list[str] | None = None
    per_customer_limit: int | None = None
    first_time_only: bool | None = None
    max_redemptions: int | None = None
    clear_expiry: bool = False


@router.patch("/api/owner/coupons/{cid}", body=CouponPatch, fresh=True)
async def update_coupon(ctx: Ctx) -> dict[str, Any]:
    need(ctx, "coupons.manage")
    values = ctx.body.model_dump(exclude_none=True, exclude={"clear_expiry"})
    if ctx.body.clear_expiry:
        values["expires_at"] = None
    cid = ctx.request.path_params["cid"]
    if "active" in values:
        c = await coupons.set_active(ctx.engine, cid, values.pop("active"), by=ctx.user_id)
        if not values:
            return c
    return coupons.update(ctx.engine, cid, values, by=ctx.user_id)


@router.post("/api/owner/coupons/{cid}/sync-stripe", fresh=True)
async def sync_coupon(ctx: Ctx) -> dict[str, Any]:
    need(ctx, "coupons.manage")
    return await coupons.sync_to_stripe(ctx.engine, ctx.request.path_params["cid"], by=ctx.user_id)


@router.get("/api/owner/coupons/{cid}/redemptions")
async def coupon_redemptions(ctx: Ctx) -> dict[str, Any]:
    need(ctx, "coupons.read")
    return {"items": coupons.redemptions(ctx.engine, ctx.request.path_params["cid"])}


# --- sales leads -------------------------------------------------------------------------------------------------------

@router.get("/api/owner/leads")
async def leads(ctx: Ctx) -> dict[str, Any]:
    need(ctx, "sales.read")
    q = ctx.request.query_params
    limit, offset = _page(ctx)
    return {**sales.list_leads(ctx.engine, status=q.get("status"), q=q.get("q"), assigned_to=q.get("assigned_to"), limit=limit, offset=offset),
            "stats": sales.stats(ctx.engine), "statuses": sales.STATUSES, "admins": owner.list_admins(ctx.engine)}


@router.get("/api/owner/leads/{lid}")
async def lead(ctx: Ctx) -> dict[str, Any]:
    need(ctx, "sales.read")
    return sales.get(ctx.engine, ctx.request.path_params["lid"])


class LeadPatch(BaseModel):
    model_config = {"extra": "forbid"}
    status: str | None = None
    assigned_to: str | None = None
    follow_up_at: float | None = None
    converted_user_id: str | None = None
    clear_assignee: bool = False
    clear_follow_up: bool = False


@router.patch("/api/owner/leads/{lid}", body=LeadPatch, fresh=True)
async def update_lead(ctx: Ctx) -> dict[str, Any]:
    need(ctx, "sales.manage")
    values = ctx.body.model_dump(exclude_none=True, exclude={"clear_assignee", "clear_follow_up"})
    if ctx.body.clear_assignee:
        values["assigned_to"] = None
    if ctx.body.clear_follow_up:
        values["follow_up_at"] = None
    return sales.update(ctx.engine, ctx.request.path_params["lid"], values, by=ctx.user_id)


class NoteIn(BaseModel):
    body: str = Field(min_length=1, max_length=4000)


@router.post("/api/owner/leads/{lid}/notes", body=NoteIn, fresh=True)
async def add_lead_note(ctx: Ctx) -> dict[str, Any]:
    need(ctx, "sales.manage")
    return sales.add_note(ctx.engine, ctx.request.path_params["lid"], ctx.body.body, by=ctx.user_id)


# --- analytics -----------------------------------------------------------------------------------------------------------

@router.get("/api/owner/analytics/kpis")
async def analytics_kpis(ctx: Ctx) -> dict[str, Any]:
    need(ctx, "analytics.read")
    period, start, end = _range(ctx)
    return analytics.kpis(ctx.engine, period, start, end)


@router.get("/api/owner/analytics/series/{metric}")
async def analytics_series(ctx: Ctx) -> dict[str, Any]:
    need(ctx, "analytics.read")
    period, start, end = _range(ctx)
    return analytics.series(ctx.engine, ctx.request.path_params["metric"], period, start, end, ctx.request.query_params.get("bucket", "day"))


@router.get("/api/owner/analytics/definitions")
async def analytics_definitions(ctx: Ctx) -> dict[str, Any]:
    need(ctx, "analytics.read")
    return {"metrics": analytics.METRIC_DEFINITIONS, "events": analytics.AUDIT_TO_EVENT, "web_events": sorted(analytics.WEB_EVENTS)}


@router.get("/api/owner/export/{report}.csv")
async def export_csv(ctx: Ctx) -> Any:
    need(ctx, "analytics.export")
    period, start, end = _range(ctx)
    report = ctx.request.path_params["report"]
    text = analytics.export_csv(ctx.engine, report, period, start, end)
    ctx.engine.audit(None, ctx.user_id, "platform.export", report, {"period": period}, resource_type="report", decision="allowed", result="ok")
    stamp = dt.datetime.now(dt.UTC).strftime("%Y%m%d")
    return PlainTextResponse(text, media_type="text/csv; charset=utf-8",
                             headers={"content-disposition": f'attachment; filename="{report}-{stamp}.csv"'})


# --- notifications -----------------------------------------------------------------------------------------------------

@router.get("/api/owner/announcements")
async def list_announcements(ctx: Ctx) -> dict[str, Any]:
    need(ctx, "notifications.read")
    return {"items": notifications.list_announcements(ctx.engine), "categories": notifications.CATEGORIES, "channels": notifications.CHANNELS,
            "plans": [p["code"] for p in plans.list_plans(ctx.engine)]}


class AnnouncementIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1, max_length=10_000)
    category: str = "general"
    channels: list[str] = Field(default_factory=lambda: ["in_app"])
    target: dict[str, Any] = Field(default_factory=lambda: {"all": True})
    scheduled_at: float | None = None
    send_now: bool = False


@router.post("/api/owner/announcements", body=AnnouncementIn, fresh=True)
async def create_announcement(ctx: Ctx) -> dict[str, Any]:
    need(ctx, "notifications.manage")
    a = notifications.create_announcement(ctx.engine, ctx.body.model_dump(exclude={"send_now"}), by=ctx.user_id)
    if ctx.body.send_now:
        a = notifications.send_announcement(ctx.engine, a["id"], by=ctx.user_id)
    return a


@router.post("/api/owner/announcements/preview", body=AnnouncementIn)
async def preview_announcement(ctx: Ctx) -> dict[str, Any]:
    need(ctx, "notifications.read")
    target = notifications._validate_target(ctx.engine, ctx.body.target)
    people = notifications.recipients(ctx.engine, target)
    return {"recipients": len(people), "sample": [p["email"] for p in people[:10]]}


@router.post("/api/owner/announcements/{aid}/send", fresh=True)
async def send_announcement(ctx: Ctx) -> dict[str, Any]:
    need(ctx, "notifications.manage")
    return notifications.send_announcement(ctx.engine, ctx.request.path_params["aid"], by=ctx.user_id)


@router.post("/api/owner/announcements/{aid}/cancel", fresh=True)
async def cancel_announcement(ctx: Ctx) -> None:
    need(ctx, "notifications.manage")
    notifications.cancel_announcement(ctx.engine, ctx.request.path_params["aid"], by=ctx.user_id)


@router.get("/api/owner/announcements/{aid}/deliveries")
async def announcement_deliveries(ctx: Ctx) -> dict[str, Any]:
    need(ctx, "notifications.read")
    return {"items": notifications.delivery_history(ctx.engine, ctx.request.path_params["aid"])}


@router.get("/api/owner/alerts")
async def alert_rules(ctx: Ctx) -> dict[str, Any]:
    need(ctx, "notifications.read")
    return {"rules": notifications.alert_rules(ctx.engine), "webhook_url": get_setting(ctx.engine, "notifications.webhook_url"),
            "webhook_secret": secret_status(ctx.engine, "notifications.webhook_secret")}


class AlertRulesIn(BaseModel):
    rules: dict[str, dict[str, Any]] | None = None
    webhook_url: str | None = None
    webhook_secret: str | None = None


@router.put("/api/owner/alerts", body=AlertRulesIn, fresh=True)
async def set_alert_rules(ctx: Ctx) -> dict[str, Any]:
    need(ctx, "notifications.manage")
    if ctx.body.rules is not None:
        unknown = set(ctx.body.rules) - set(notifications.ALERT_EVENTS)
        if unknown:
            raise Invalid(f"Unknown alert rules: {', '.join(sorted(unknown))}.")
        set_setting(ctx.engine, "alerts.rules", ctx.body.rules, by=ctx.user_id)
    if ctx.body.webhook_url is not None:
        set_setting(ctx.engine, "notifications.webhook_url", ctx.body.webhook_url, by=ctx.user_id)
    if ctx.body.webhook_secret:
        set_setting(ctx.engine, "notifications.webhook_secret", ctx.body.webhook_secret, by=ctx.user_id)
    return await alert_rules(ctx)


# --- e-mail ------------------------------------------------------------------------------------------------------------

@router.get("/api/owner/email")
async def email_status(ctx: Ctx) -> dict[str, Any]:
    need(ctx, "email.read")
    return {**email_svc.provider_status(ctx.engine), "templates": email_svc.list_templates(ctx.engine),
            "jobs": email_svc.list_jobs(ctx.engine, limit=50), "daily": email_svc.daily_stats(ctx.engine),
            "suppressions": ctx.engine.db.all("SELECT * FROM email_suppressions ORDER BY created_at DESC LIMIT 100")}


class EmailSettingsIn(BaseModel):
    smtp_host: str | None = None
    smtp_port: int | None = None
    smtp_user: str | None = None
    smtp_password: str | None = None
    smtp_security: Literal["starttls", "ssl", "none"] | None = None
    from_address: str | None = None
    from_name: str | None = None
    reply_to: str | None = None
    max_attempts: int | None = None


@router.put("/api/owner/email", body=EmailSettingsIn, fresh=True)
async def email_configure(ctx: Ctx) -> dict[str, Any]:
    need(ctx, "email.configure")
    b = ctx.body.model_dump(exclude_none=True)
    if b.get("smtp_security") == "none" and (b.get("smtp_user") or get_setting(ctx.engine, "email.smtp_user")):
        raise Invalid("Security 'none' sends in clear and cannot be used with a username and password.")
    for k, v in b.items():
        if k == "smtp_password":
            if v == "":
                clear_secret(ctx.engine, "email.smtp_password", by=ctx.user_id)
            else:
                set_setting(ctx.engine, "email.smtp_password", v, by=ctx.user_id)
        else:
            set_setting(ctx.engine, f"email.{k}", v, by=ctx.user_id)
    return email_svc.provider_status(ctx.engine)


class TestEmailIn(BaseModel):
    to: str = Field(min_length=3, max_length=254)


@router.post("/api/owner/email/test", body=TestEmailIn, fresh=True)
async def email_test(ctx: Ctx) -> dict[str, Any]:
    need(ctx, "email.configure")
    return await email_svc.send_test(ctx.engine, ctx.body.to, by=ctx.user_id)


class TemplateIn(BaseModel):
    subject: str = Field(min_length=1, max_length=300)
    body: str = Field(min_length=1, max_length=20_000)


@router.put("/api/owner/email/templates/{key}/{lang}", body=TemplateIn, fresh=True)
async def save_template(ctx: Ctx) -> dict[str, Any]:
    need(ctx, "email.templates")
    return email_svc.save_template(ctx.engine, ctx.request.path_params["key"], ctx.request.path_params["lang"], subject=ctx.body.subject,
                                   body=ctx.body.body, by=ctx.user_id)


@router.delete("/api/owner/email/templates/{key}/{lang}", fresh=True)
async def reset_template(ctx: Ctx) -> dict[str, Any]:
    need(ctx, "email.templates")
    return email_svc.reset_template(ctx.engine, ctx.request.path_params["key"], ctx.request.path_params["lang"], by=ctx.user_id)


@router.get("/api/owner/email/templates/{key}/{lang}/preview")
async def preview_template(ctx: Ctx) -> dict[str, Any]:
    need(ctx, "email.read")
    return email_svc.preview(ctx.engine, ctx.request.path_params["key"], ctx.request.path_params["lang"])


@router.get("/api/owner/email/jobs")
async def email_jobs(ctx: Ctx) -> dict[str, Any]:
    need(ctx, "email.read")
    limit, _ = _page(ctx, 100)
    return {"items": email_svc.list_jobs(ctx.engine, status=ctx.request.query_params.get("status"), limit=limit)}


@router.post("/api/owner/email/jobs/{jid}/retry", fresh=True)
async def retry_email(ctx: Ctx) -> None:
    need(ctx, "email.configure")
    email_svc.retry(ctx.engine, ctx.request.path_params["jid"], by=ctx.user_id)


class SuppressIn(BaseModel):
    address: str = Field(min_length=3, max_length=254)
    reason: str = Field(default="manual", max_length=100)


@router.post("/api/owner/email/suppressions", body=SuppressIn, fresh=True)
async def suppress(ctx: Ctx) -> None:
    need(ctx, "email.configure")
    email_svc.suppress(ctx.engine, ctx.body.address, ctx.body.reason, by=ctx.user_id)


@router.delete("/api/owner/email/suppressions/{address}", fresh=True)
async def unsuppress(ctx: Ctx) -> None:
    need(ctx, "email.configure")
    email_svc.unsuppress(ctx.engine, ctx.request.path_params["address"], by=ctx.user_id)


# --- security ----------------------------------------------------------------------------------------------------------

@router.get("/api/owner/security")
async def security_overview(ctx: Ctx) -> dict[str, Any]:
    role = need(ctx, "security.read")
    e = ctx.engine
    day, week = now() - 86400, now() - 7 * 86400

    def n(sql: str, *p: Any) -> int:
        return int(e.db.scalar(sql, p) or 0)

    return {"role": role,
            "failed_logins_24h": n("SELECT COUNT(*) FROM audit_log WHERE action = 'user.login.failed' AND created_at > ?", day),
            "failed_logins_7d": n("SELECT COUNT(*) FROM audit_log WHERE action = 'user.login.failed' AND created_at > ?", week),
            "denied_7d": n("SELECT COUNT(*) FROM audit_log WHERE decision = 'denied' AND created_at > ?", week),
            "active_sessions": n("SELECT COUNT(*) FROM sessions"),
            "revoked_7d": n("SELECT COUNT(*) FROM audit_log WHERE action = 'session.revoked' AND created_at > ?", week),
            "privileged_actions_7d": n("SELECT COUNT(*) FROM audit_log WHERE (action LIKE 'platform.%' OR action LIKE 'plan.%' OR action LIKE 'user.suspend' "
                                       "OR action LIKE 'workspace.state' OR action LIKE 'support.%') AND created_at > ?", week),
            "permission_changes_7d": n("SELECT COUNT(*) FROM audit_log WHERE action IN ('member.role','member.remove','member.add','platform.admin.grant',"
                                       "'platform.admin.revoke') AND created_at > ?", week),
            "api_events_7d": n("SELECT COUNT(*) FROM audit_log WHERE (action LIKE 'mcp%' OR action LIKE 'webhook.%') AND created_at > ?", week),
            "config_changes_7d": n("SELECT COUNT(*) FROM audit_log WHERE action LIKE 'platform.setting%' OR action LIKE 'platform.secret%' AND created_at > ?", week),
            "mfa_users": n("SELECT COUNT(*) FROM users WHERE mfa_enabled_at IS NOT NULL"),
            "users_total": n("SELECT COUNT(*) FROM users WHERE is_local = 0"),
            "recent_alerts": e.db.all("SELECT action, user_id, target, decision, result, detail, created_at FROM audit_log WHERE decision = 'denied' ORDER BY id DESC LIMIT 30"),
            "recent_privileged": e.db.all("SELECT action, user_id, target, detail, created_at FROM audit_log WHERE action LIKE 'platform.%' OR action LIKE 'plan.%' "
                                          "OR action IN ('user.suspend','user.reactivate','workspace.state','support.access.granted') ORDER BY id DESC LIMIT 30"),
            "policy": group_view(e, "security"), "session": {"idle_minutes": e.settings.session_idle_minutes, "session_hours": e.settings.session_hours,
                                                             "admin_session_hours": e.settings.admin_session_hours, "reauth_minutes": e.settings.reauth_minutes},
            "admins": owner.list_admins(e), "pending_changes": owner.pending_changes(e), "support_grants": support.list_support(e),
            "owner_list_configured": bool(e.settings.platform_owners)}


class SecurityPolicyIn(BaseModel):
    model_config = {"extra": "forbid"}
    mfa_policy: Literal["none", "admins", "all"] | None = None
    dual_approval: bool | None = None
    lockout_threshold: int | None = None
    lockout_minutes: int | None = None
    alert_failed_logins: int | None = None
    session_idle_minutes: int | None = None
    session_hours: int | None = None
    admin_session_hours: int | None = None
    audit_retention_days: int | None = None
    api_key_max_age_days: int | None = None
    oauth_providers: list[str] | None = None
    registration_enabled: bool | None = None
    registration_allowed_domains: list[str] | None = None
    registration_require_verified_email: bool | None = None


@router.put("/api/owner/security", body=SecurityPolicyIn, fresh=True)
async def security_configure(ctx: Ctx) -> dict[str, Any]:
    need(ctx, "security.configure")
    b = ctx.body.model_dump(exclude_none=True)
    critical = {k: v for k, v in b.items() if k in ("mfa_policy", "session_idle_minutes", "session_hours", "admin_session_hours")}
    if critical:
        parked = _critical(ctx, "security.configure", critical)
        if parked:
            for k in critical:
                b.pop(k)
            _apply_security(ctx.engine, b, by=ctx.user_id)
            return {**parked, "applied": sorted(b)}
    _apply_security(ctx.engine, b, by=ctx.user_id)
    return {"policy": group_view(ctx.engine, "security"), "registration": {k: get_setting(ctx.engine, k) for k in REGISTRY if k.startswith("registration.")}}


def _apply_security(engine: Any, b: dict[str, Any], *, by: str) -> None:
    for k, v in b.items():
        key = f"registration.{k[len('registration_'):]}" if k.startswith("registration_") else f"security.{k}"
        set_setting(engine, key, v, by=by)


@router.get("/api/owner/sessions")
async def sessions(ctx: Ctx) -> dict[str, Any]:
    need(ctx, "security.read")
    limit, offset = _page(ctx)
    rows = ctx.engine.db.all("SELECT substr(s.id, 1, 12) AS id, s.user_id, u.email, s.created_at, s.last_seen, s.user_agent, s.mfa_at, s.reauth_at FROM sessions s "
                             "JOIN users u ON u.id = s.user_id ORDER BY s.last_seen DESC LIMIT ? OFFSET ?", (limit, offset))
    return {"total": ctx.engine.db.scalar("SELECT COUNT(*) FROM sessions") or 0, "items": rows}


class AdminIn(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    role: Literal["owner", "admin"] = "admin"


@router.post("/api/owner/admins", body=AdminIn, fresh=True)
async def grant_admin(ctx: Ctx) -> dict[str, Any]:
    need(ctx, "admins.manage")
    u = ctx.engine.db.one("SELECT id FROM users WHERE email = ? AND is_local = 0", (ctx.body.email.lower(),))
    if u is None:
        raise NotFound("No account with that e-mail. The person signs up first; platform roles are never created from the public form.")
    parked = _critical(ctx, "admin.grant", {"user_id": u["id"], "role": ctx.body.role, "email": ctx.body.email.lower()})
    if parked:
        return parked
    owner.grant(ctx.engine, user_id=u["id"], role=ctx.body.role, granted_by=ctx.user_id)
    return {"admins": owner.list_admins(ctx.engine)}


@router.delete("/api/owner/admins/{uid}", fresh=True)
async def revoke_admin(ctx: Ctx) -> dict[str, Any]:
    need(ctx, "admins.manage")
    uid = ctx.request.path_params["uid"]
    if uid == ctx.user_id:
        raise Invalid("You cannot remove your own platform role.")
    owner.revoke(ctx.engine, user_id=uid, revoked_by=ctx.user_id)
    return {"admins": owner.list_admins(ctx.engine)}


@router.get("/api/owner/changes")
async def changes(ctx: Ctx) -> dict[str, Any]:
    need(ctx, "security.read")
    return {"items": owner.pending_changes(ctx.engine), "dual_approval": owner.dual_approval_on(ctx.engine)}


class DecideIn(BaseModel):
    approve: bool


@router.post("/api/owner/changes/{cid}/decide", body=DecideIn, fresh=True)
async def decide_change(ctx: Ctx) -> dict[str, Any]:
    need(ctx, "admins.manage")   # only owners decide
    row = owner.decide_change(ctx.engine, change_id=ctx.request.path_params["cid"], decided_by=ctx.user_id, approve=ctx.body.approve)
    if row["status"] == "approved":
        _apply_change(ctx.engine, row, by=ctx.user_id)
    return row


def _apply_change(engine: Any, row: dict[str, Any], *, by: str) -> None:
    kind, payload = row["kind"], row["payload"]
    if kind == "admin.grant":
        owner.grant(engine, user_id=payload["user_id"], role=payload["role"], granted_by=by)
    elif kind == "plan.update":
        plans.update_plan(engine, payload["plan_id"], payload["values"], by=by)
    elif kind == "security.configure":
        _apply_security(engine, payload, by=by)
    elif kind == "billing.configure":
        values = {k: v for k, v in payload.items() if k not in ("secret_key", "webhook_secret")}
        try:
            sealed = engine.secrets.get(row["id"], workspace_id=None)
            values.update(sealed)
            engine.db.execute("DELETE FROM secrets WHERE id = ?", (row["id"],))
        except Exception:  # noqa: BLE001 - no sealed secrets on this request
            pass
        _apply_billing(engine, values, by=by)


# --- integrations ------------------------------------------------------------------------------------------------------

@router.get("/api/owner/integrations")
async def integrations(ctx: Ctx) -> dict[str, Any]:
    need(ctx, "integrations.read")
    from ..connectors.oauth import app_status
    from .auth import google_login_available

    google = next((a for a in app_status(ctx.engine) if a.get("provider") == "google" or a.get("key") == "google"), None)
    return {"google": {**group_view(ctx.engine, "google"), "credentials": google,
                       "redirect_uri": ctx.engine.settings.oauth_redirect_uri, "available": google_login_available(ctx.engine)},
            "stripe": billing.status(ctx.engine), "email": email_svc.provider_status(ctx.engine),
            "analytics": group_view(ctx.engine, "analytics"),
            "storage": {"kind": ctx.engine.settings.storage.get("kind"), "encrypted": bool(ctx.engine.settings.storage.get("encrypt", True)),
                        "bucket": ctx.engine.settings.storage.get("bucket"), "endpoint": ctx.engine.settings.storage.get("endpoint"),
                        **group_view(ctx.engine, "storage")}}


class GoogleIn(BaseModel):
    enabled: bool | None = None
    client_id: str | None = Field(default=None, max_length=300)
    client_secret: str | None = Field(default=None, max_length=300)
    registration: Literal["follow", "open", "invite_only", "disabled"] | None = None
    allowed_domains: list[str] | None = None
    link_existing: Literal["verified_email", "never"] | None = None


@router.put("/api/owner/integrations/google", body=GoogleIn, fresh=True)
async def google_configure(ctx: Ctx) -> dict[str, Any]:
    need(ctx, "integrations.configure")
    from ..connectors.oauth import save_app_credentials

    b = ctx.body
    if b.client_id or b.client_secret:
        if not b.client_id:
            raise Invalid("A client secret needs its client ID.")
        save_app_credentials(ctx.engine, "google", b.client_id, b.client_secret, {})
        ctx.engine.audit(None, ctx.user_id, "oauth_app.set", "google", {"by": "owner_center"}, resource_type="oauth_app", decision="allowed", result="ok")
    for k in ("enabled", "registration", "allowed_domains", "link_existing"):
        v = getattr(b, k)
        if v is not None:
            set_setting(ctx.engine, f"google.{k}", v, by=ctx.user_id)
    return (await integrations(ctx))["google"]


class AnalyticsIn(BaseModel):
    enabled: bool | None = None
    require_consent: bool | None = None
    collect_country: bool | None = None
    retention_days: int | None = None


@router.put("/api/owner/integrations/analytics", body=AnalyticsIn, fresh=True)
async def analytics_configure(ctx: Ctx) -> dict[str, Any]:
    need(ctx, "integrations.configure")
    for k, v in ctx.body.model_dump(exclude_none=True).items():
        set_setting(ctx.engine, f"analytics.{k}", v, by=ctx.user_id)
    return group_view(ctx.engine, "analytics")


# --- storage -----------------------------------------------------------------------------------------------------------

@router.get("/api/owner/storage")
async def storage_overview(ctx: Ctx) -> dict[str, Any]:
    need(ctx, "storage.read")
    return {"totals": storage.platform_totals(ctx.engine), "workspaces": storage.per_workspace(ctx.engine),
            "settings": group_view(ctx.engine, "storage"), "provider": {"kind": ctx.engine.settings.storage.get("kind"),
                                                                        "encrypted": bool(ctx.engine.settings.storage.get("encrypt", True)),
                                                                        "max_upload_mb": ctx.engine.settings.max_upload_mb}}


class StorageIn(BaseModel):
    over_quota_policy: Literal["read_only", "grace"] | None = None
    grace_days: int | None = None
    retention_days_after_delete: int | None = None
    max_file_mb: int | None = None


@router.put("/api/owner/storage", body=StorageIn, fresh=True)
async def storage_configure(ctx: Ctx) -> dict[str, Any]:
    need(ctx, "storage.configure")
    for k, v in ctx.body.model_dump(exclude_none=True).items():
        set_setting(ctx.engine, f"storage.{k}", v, by=ctx.user_id)
    return group_view(ctx.engine, "storage")


# --- audit log ----------------------------------------------------------------------------------------------------------

@router.get("/api/owner/audit")
async def audit(ctx: Ctx) -> dict[str, Any]:
    need(ctx, "audit.read")
    q = ctx.request.query_params
    where, params = ["1=1"], []
    for col in ("action", "user_id", "workspace_id", "decision", "result", "request_id"):
        if q.get(col):
            where.append(f"a.{col} LIKE ?" if col == "action" else f"a.{col} = ?")
            params.append(f"{q[col]}%" if col == "action" else q[col])
    if q.get("from"):
        where.append("a.created_at >= ?")
        params.append(float(q["from"]))
    if q.get("to"):
        where.append("a.created_at < ?")
        params.append(float(q["to"]))
    limit, offset = _page(ctx, 100, 500)
    sql = f"FROM audit_log a LEFT JOIN users u ON u.id = a.user_id WHERE {' AND '.join(where)}"
    total = ctx.engine.db.scalar(f"SELECT COUNT(*) {sql}", params) or 0
    rows = ctx.engine.db.all(f"SELECT a.id, a.event_id, a.request_id, a.workspace_id, a.user_id, u.email, a.action, a.resource_type, a.target, a.decision, "
                             f"a.approval_id, a.result, a.detail, a.created_at {sql} ORDER BY a.id DESC LIMIT ? OFFSET ?", [*params, limit, offset])
    for r in rows:
        r["detail"] = loads(r["detail"], None)
    return {"total": total, "items": rows, "actions": [r["action"] for r in ctx.engine.db.all("SELECT DISTINCT action FROM audit_log ORDER BY action")]}


# --- platform settings ---------------------------------------------------------------------------------------------------

@router.get("/api/owner/settings")
async def settings_view(ctx: Ctx) -> dict[str, Any]:
    need(ctx, "settings.read")
    return {"general": group_view(ctx.engine, "general"), "features": group_view(ctx.engine, "features"),
            "feature_keys": list(FEATURE_FLAG_KEYS),
            "registry": {k: {"kind": s.kind, "group": s.group, "choices": list(s.choices), "lo": s.lo, "hi": s.hi, "critical": s.critical}
                         for k, s in REGISTRY.items()},
            "environment": {"mode": ctx.engine.settings.mode, "hosted": ctx.engine.settings.hosted, "public_url": ctx.engine.settings.public_url,
                            "site_url": ctx.engine.settings.site_url, "open_signup_env": ctx.engine.settings.open_signup,
                            "sealed": ctx.engine.settings.sealed, "storage": ctx.engine.settings.storage.get("kind")}}


class SettingsIn(BaseModel):
    values: dict[str, Any]


@router.put("/api/owner/settings", body=SettingsIn, fresh=True)
async def settings_update(ctx: Ctx) -> dict[str, Any]:
    need(ctx, "settings.manage")
    out = {}
    for key, value in ctx.body.values.items():
        spec = REGISTRY.get(key)
        if spec is None:
            raise Invalid(f"Unknown setting {key}.", code="unknown_setting")
        if spec.group not in ("general", "features"):
            raise Invalid(f"{key} is managed in its own section (billing, e-mail, security, integrations, storage).")
        out[key] = set_setting(ctx.engine, key, value, by=ctx.user_id)
    return out
