"""Subscription plans, entitlements and their enforcement.

Three editions share this code:

* **Self-hosted** (the open-source edition, ``DESK_HOSTED`` unset): every entitlement is unlimited. Plans may be
  edited in the dashboard for a later hosted deployment, but nothing here limits a self-hosted installation —
  the project's Apache-2.0 licence grants those rights and this code does not take them back.
* **Hosted Free / Pro** (``DESK_HOSTED=true``): the account's active subscription decides; without one, the default
  plan (Free). Limits are enforced on the server, inside the same transaction that would exceed them.
* **Enterprise**: a plan with ``contact_sales`` set; its entitlements are assigned by the owner after a contract.

The *account* that owns a workspace is the member with the ``owner`` role (the person who created it). Workspace
limits, storage quotas and features are judged against that account's plan, so a viewer invited to a Pro workspace
gets the Pro features inside it without paying, and a Free account cannot own more than its plan allows.

A downgrade never deletes anything. Workspaces beyond the new limit become read-only (or get a grace period, by
policy); an upgrade restores them. Files over quota stop new uploads only.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..db import dumps, loads, new_id, now
from ..errors import Conflict, Forbidden, Invalid, NotFound

if TYPE_CHECKING:
    from ..app import Engine

FEATURES: dict[str, str] = {
    "ask_finance": "Ask Your Finance (the assistant over the workspace's figures)",
    "autonomous_reconciliation": "Autonomous reconciliation and month-end close preparation",
    "financial_intelligence": "Financial Intelligence (KPIs, forecasts, analysis)",
    "bank_connections": "Bank connections",
    "erp_connections": "ERP and accounting connections",
    "file_storage": "Hosted file storage",
    "advanced_analytics": "Advanced analytics",
    "api_access": "API and MCP access",
    "data_export": "Data export",
    "advanced_audit_logs": "Advanced audit logs",
    "workflows": "Automated workflows",
    "priority_support": "Priority support",
}

ACTIVE_STATUSES = ("trialing", "active", "past_due")
UNLIMITED = 0

DEFAULT_PLANS: list[dict[str, Any]] = [
    {"code": "free", "name": "Free", "description": "One hosted workspace with the core of OfinTech Desk. Community support.",
     "monthly_price": 0, "annual_price": 0, "max_workspaces": 1, "storage_quota_mb": 250, "max_members": 5,
     "features": {"ask_finance": True, "autonomous_reconciliation": True, "financial_intelligence": True,
                  "bank_connections": False, "erp_connections": True, "file_storage": True, "advanced_analytics": False,
                  "api_access": False, "data_export": True, "advanced_audit_logs": False, "workflows": True,
                  "priority_support": False, "ai_monthly_tokens": 2_000_000},
     "is_default": 1, "position": 0},
    {"code": "pro", "name": "Pro", "description": "Two hosted workspaces, hosted file storage, premium features and "
     "e-mail support.", "monthly_price": 2900, "annual_price": 29000, "max_workspaces": 2, "storage_quota_mb": 10240,
     "max_members": 25, "trial_days": 14,
     "features": {"ask_finance": True, "autonomous_reconciliation": True, "financial_intelligence": True,
                  "bank_connections": True, "erp_connections": True, "file_storage": True, "advanced_analytics": True,
                  "api_access": True, "data_export": True, "advanced_audit_logs": True, "workflows": True,
                  "priority_support": False, "ai_monthly_tokens": 20_000_000},
     "position": 1},
    {"code": "enterprise", "name": "Enterprise", "description": "Custom limits, private deployment, SSO, enterprise "
     "audit retention and dedicated support. Contract-based billing.", "monthly_price": 0, "annual_price": 0,
     "max_workspaces": UNLIMITED, "storage_quota_mb": UNLIMITED, "max_members": UNLIMITED, "contact_sales": 1,
     "features": {k: True for k in FEATURES} | {"ai_monthly_tokens": UNLIMITED}, "position": 2},
]

ENTERPRISE_CAPABILITIES = [  # what the Contact Sales page may say, each marked by its real state
    {"key": "custom_limits", "label": "Custom workspace limits and storage allocation", "status": "available"},
    {"key": "private_deployment", "label": "Private deployment (self-hosted Team Server, Docker)", "status": "available"},
    {"key": "advanced_security", "label": "Advanced security policies (sealed egress, step-up, MFA policy)", "status": "available"},
    {"key": "audit_retention", "label": "Enterprise audit retention", "status": "available"},
    {"key": "dedicated_support", "label": "Dedicated support", "status": "contract"},
    {"key": "custom_integrations", "label": "Custom integrations (custom accounting connector, MCP, webhooks)", "status": "available"},
    {"key": "financial_ai", "label": "Advanced financial AI features", "status": "available"},
    {"key": "contract_billing", "label": "Contract-based billing (owner-assigned subscription)", "status": "available"},
    {"key": "sso", "label": "SSO (SAML / OIDC)", "status": "planned"},
]


def seed(engine: Engine) -> None:
    """The three default plans, created once; an owner may edit or archive them afterwards."""
    if engine.db.scalar("SELECT COUNT(*) FROM plans"):
        return
    ts = now()
    for p in DEFAULT_PLANS:
        row = {"id": new_id("plan_"), "currency": "USD", "trial_days": 0, "billing_interval": "both", "visibility": "public",
               "contact_sales": 0, "status": "active", "is_default": 0, "created_at": ts, "updated_at": ts} | p
        row["features"] = dumps(row["features"])
        engine.db.insert("plans", row)


# --- plans -------------------------------------------------------------------------------------------------------

def _row(p: dict[str, Any]) -> dict[str, Any]:
    p = dict(p)
    p["features"] = loads(p.get("features"), {})
    for k in ("contact_sales", "is_default"):
        p[k] = bool(p.get(k))
    return p


def list_plans(engine: Engine, *, public_only: bool = False) -> list[dict[str, Any]]:
    sql = "SELECT * FROM plans"
    if public_only:
        sql += " WHERE status = 'active' AND visibility = 'public'"
    return [_row(p) for p in engine.db.all(sql + " ORDER BY position, monthly_price")]


def get_plan(engine: Engine, plan_id: str) -> dict[str, Any]:
    p = engine.db.one("SELECT * FROM plans WHERE id = ? OR code = ?", (plan_id, plan_id))
    if p is None:
        raise NotFound("Plan not found.")
    return _row(p)


def default_plan(engine: Engine) -> dict[str, Any] | None:
    p = engine.db.one("SELECT * FROM plans WHERE is_default = 1 AND status != 'archived' ORDER BY position LIMIT 1")
    return _row(p) if p else None


PLAN_FIELDS = {"code", "name", "description", "monthly_price", "annual_price", "currency", "max_workspaces",
               "storage_quota_mb", "max_members", "features", "trial_days", "billing_interval", "visibility",
               "contact_sales", "status", "is_default", "position", "stripe_product_id", "stripe_price_monthly",
               "stripe_price_annual"}


def _validate_plan(values: dict[str, Any]) -> dict[str, Any]:
    import re

    out: dict[str, Any] = {}
    for k, v in values.items():
        if k not in PLAN_FIELDS:
            raise Invalid(f"Unknown plan field {k}.")
        if k == "code":
            if not isinstance(v, str) or not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,39}", v):
                raise Invalid("The plan code is lowercase letters, digits, '-' and '_' (max 40).")
        elif k in ("name", "description", "stripe_product_id", "stripe_price_monthly", "stripe_price_annual"):
            if v is not None and (not isinstance(v, str) or len(v) > (1000 if k == "description" else 200)):
                raise Invalid(f"{k} is too long.")
            if k == "name" and not (v or "").strip():
                raise Invalid("The plan needs a name.")
        elif k in ("monthly_price", "annual_price", "max_workspaces", "storage_quota_mb", "max_members", "trial_days", "position"):
            if isinstance(v, bool) or not isinstance(v, int) or v < 0 or v > 10_000_000_000:
                raise Invalid(f"{k} must be a whole number of zero or more.")
        elif k == "currency":
            if not isinstance(v, str) or not re.fullmatch(r"[A-Z]{3}", v):
                raise Invalid("Currency is a three-letter ISO code.")
        elif k == "features":
            if not isinstance(v, dict):
                raise Invalid("features must be an object.")
            for fk, fv in v.items():
                if fk == "ai_monthly_tokens":
                    if isinstance(fv, bool) or not isinstance(fv, int) or fv < 0:
                        raise Invalid("ai_monthly_tokens is a whole number (0 = unlimited).")
                elif fk not in FEATURES or not isinstance(fv, bool):
                    raise Invalid(f"Unknown or non-boolean feature {fk}.")
        elif k == "billing_interval":
            if v not in ("month", "year", "both"):
                raise Invalid("billing_interval is month, year or both.")
        elif k == "visibility":
            if v not in ("public", "hidden"):
                raise Invalid("visibility is public or hidden.")
        elif k == "status":
            if v not in ("draft", "active", "archived"):
                raise Invalid("status is draft, active or archived.")
        elif k in ("contact_sales", "is_default"):
            if not isinstance(v, bool):
                raise Invalid(f"{k} is true or false.")
            v = int(v)
        out[k] = v
    return out


def create_plan(engine: Engine, values: dict[str, Any], *, by: str) -> dict[str, Any]:
    clean = _validate_plan(values)
    if "code" not in clean or "name" not in clean:
        raise Invalid("A plan needs a code and a name.")
    if engine.db.one("SELECT 1 FROM plans WHERE code = ?", (clean["code"],)):
        raise Conflict("A plan with this code exists.")
    ts = now()
    row = {"id": new_id("plan_"), "description": "", "monthly_price": 0, "annual_price": 0, "currency": "USD",
           "max_workspaces": 1, "storage_quota_mb": 0, "max_members": 0, "features": {}, "trial_days": 0,
           "billing_interval": "both", "visibility": "public", "contact_sales": 0, "status": "draft", "is_default": 0,
           "position": 99, "created_at": ts, "updated_at": ts} | clean
    row["features"] = dumps(row["features"])
    with engine.db.tx() as c:
        if row["is_default"]:
            c.execute("UPDATE plans SET is_default = 0")
        c.execute(f"INSERT INTO plans ({', '.join(row)}) VALUES ({', '.join('?' for _ in row)})", list(row.values()))
    engine.audit(None, by, "plan.create", row["id"], {"code": row["code"]}, resource_type="plan", decision="allowed", result="ok")
    return get_plan(engine, row["id"])


def update_plan(engine: Engine, plan_id: str, values: dict[str, Any], *, by: str) -> dict[str, Any]:
    plan = get_plan(engine, plan_id)
    clean = _validate_plan(values)
    if "code" in clean and clean["code"] != plan["code"] and engine.db.one("SELECT 1 FROM plans WHERE code = ?", (clean["code"],)):
        raise Conflict("A plan with this code exists.")
    if "features" in clean:
        clean["features"] = dumps(clean["features"])
    clean["updated_at"] = now()
    with engine.db.tx() as c:
        if clean.get("is_default"):
            c.execute("UPDATE plans SET is_default = 0")
        sets = ", ".join(f"{k} = ?" for k in clean)
        c.execute(f"UPDATE plans SET {sets} WHERE id = ?", [*clean.values(), plan["id"]])
    engine.audit(None, by, "plan.update", plan["id"], {k: v for k, v in values.items() if k != "description"},
                 resource_type="plan", decision="allowed", result="ok")
    updated = get_plan(engine, plan["id"])
    # entitlements changed for everyone on the plan: re-judge their workspaces
    for s in engine.db.all("SELECT DISTINCT user_id FROM subscriptions WHERE plan_id = ? AND status IN "
                           "('trialing','active','past_due')", (plan["id"],)):
        enforce_workspace_limit(engine, s["user_id"])
    return updated


# --- subscriptions ----------------------------------------------------------------------------------------------

def current_subscription(engine: Engine, user_id: str) -> dict[str, Any] | None:
    """The subscription that decides this account's entitlements right now (one at most)."""
    row = engine.db.one("SELECT * FROM subscriptions WHERE user_id = ? AND status IN ('trialing','active','past_due') "
                        "ORDER BY CASE status WHEN 'active' THEN 0 WHEN 'trialing' THEN 1 ELSE 2 END, updated_at DESC LIMIT 1",
                        (user_id,))
    if row and row["status"] == "past_due" and row.get("grace_until") and row["grace_until"] < now():
        return None   # the grace period after a failed payment is over: back to the default plan
    return row


def subscription_history(engine: Engine, user_id: str) -> list[dict[str, Any]]:
    return engine.db.all("SELECT s.*, p.name AS plan_name, p.code AS plan_code FROM subscriptions s JOIN plans p ON p.id = "
                         "s.plan_id WHERE s.user_id = ? ORDER BY s.created_at DESC", (user_id,))


def plan_for(engine: Engine, user_id: str) -> dict[str, Any] | None:
    sub = current_subscription(engine, user_id)
    if sub:
        try:
            return get_plan(engine, sub["plan_id"])
        except NotFound:
            pass
    return default_plan(engine)


def assign(engine: Engine, *, user_id: str, plan_id: str, by: str, interval: str = "month", note: str | None = None,
           months: int | None = None, source: str = "owner") -> dict[str, Any]:
    """The owner puts an account on a plan without Stripe (an enterprise contract, a courtesy upgrade). Any existing
    non-Stripe subscription is ended; a Stripe one is left to Stripe and refused here."""
    plan = get_plan(engine, plan_id)
    if engine.db.one("SELECT 1 FROM users WHERE id = ? AND is_local = 0", (user_id,)) is None:
        raise NotFound("User not found.")
    live = current_subscription(engine, user_id)
    if live and live["source"] == "stripe":
        raise Conflict("This account has a Stripe subscription. Change it through billing, not by assignment.",
                       code="stripe_managed")
    ts = now()
    end = ts + (months or 0) * 30 * 86400 if months else None
    with engine.db.tx() as c:
        c.execute("UPDATE subscriptions SET status = 'canceled', canceled_at = ?, updated_at = ? WHERE user_id = ? AND "
                  "source != 'stripe' AND status IN ('trialing','active','past_due')", (ts, ts, user_id))
        sid = new_id("sub_")
        c.execute("INSERT INTO subscriptions (id, user_id, plan_id, status, interval, source, current_period_start, "
                  "current_period_end, note, created_by, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                  (sid, user_id, plan["id"], "active", interval, source, ts, end, note, by, ts, ts))
    engine.audit(None, by, "subscription.assign", sid, {"user": user_id, "plan": plan["code"], "months": months},
                 resource_type="subscription", decision="allowed", result="ok")
    enforce_workspace_limit(engine, user_id)
    return engine.db.one("SELECT * FROM subscriptions WHERE id = ?", (sid,))


def cancel(engine: Engine, subscription_id: str, *, by: str, at_period_end: bool = True) -> dict[str, Any]:
    sub = engine.db.one("SELECT * FROM subscriptions WHERE id = ?", (subscription_id,))
    if sub is None:
        raise NotFound("Subscription not found.")
    ts = now()
    if at_period_end and sub.get("current_period_end") and sub["current_period_end"] > ts:
        engine.db.update("subscriptions", sub["id"], {"cancel_at_period_end": 1, "updated_at": ts})
    else:
        engine.db.update("subscriptions", sub["id"], {"status": "canceled", "canceled_at": ts, "updated_at": ts})
        enforce_workspace_limit(engine, sub["user_id"])
    engine.audit(None, by, "subscription.cancel", sub["id"], {"at_period_end": at_period_end}, resource_type="subscription",
                 decision="allowed", result="ok")
    return engine.db.one("SELECT * FROM subscriptions WHERE id = ?", (sub["id"],))


def schedule_change(engine: Engine, subscription_id: str, new_plan_id: str, *, by: str) -> dict[str, Any]:
    """A downgrade takes effect at the end of the paid period; an upgrade is applied at once."""
    sub = engine.db.one("SELECT * FROM subscriptions WHERE id = ?", (subscription_id,))
    if sub is None:
        raise NotFound("Subscription not found.")
    new = get_plan(engine, new_plan_id)
    old = get_plan(engine, sub["plan_id"])
    upgrade = (new["monthly_price"], new["max_workspaces"] or 10**9) > (old["monthly_price"], old["max_workspaces"] or 10**9)
    ts = now()
    if upgrade or not sub.get("current_period_end") or sub["current_period_end"] <= ts:
        engine.db.update("subscriptions", sub["id"], {"plan_id": new["id"], "scheduled_plan_id": None, "updated_at": ts})
        enforce_workspace_limit(engine, sub["user_id"])
        kind = "upgrade" if upgrade else "change"
    else:
        engine.db.update("subscriptions", sub["id"], {"scheduled_plan_id": new["id"], "updated_at": ts})
        kind = "downgrade_scheduled"
    engine.audit(None, by, f"subscription.{kind}", sub["id"], {"from": old["code"], "to": new["code"]},
                 resource_type="subscription", decision="allowed", result="ok")
    return engine.db.one("SELECT * FROM subscriptions WHERE id = ?", (sub["id"],))


def tick(engine: Engine) -> None:
    """Periodic lifecycle: trials end, scheduled changes apply, cancellations at period end take effect, grace
    periods run out. Idempotent; the scheduler calls it every few minutes."""
    ts = now()
    changed_users: set[str] = set()
    for s in engine.db.all("SELECT * FROM subscriptions WHERE status IN ('trialing','active','past_due')"):
        end = s.get("current_period_end")
        if s["status"] == "trialing" and s.get("trial_end") and s["trial_end"] < ts and s["source"] != "stripe":
            engine.db.update("subscriptions", s["id"], {"status": "canceled", "canceled_at": ts, "updated_at": ts})
            changed_users.add(s["user_id"])
        elif end and end < ts and s.get("scheduled_plan_id"):
            engine.db.update("subscriptions", s["id"], {"plan_id": s["scheduled_plan_id"], "scheduled_plan_id": None,
                                                        "current_period_start": ts, "current_period_end": None if s["source"] != "stripe" else end,
                                                        "updated_at": ts})
            changed_users.add(s["user_id"])
        elif end and end < ts and s["cancel_at_period_end"] and s["source"] != "stripe":
            engine.db.update("subscriptions", s["id"], {"status": "canceled", "canceled_at": ts, "updated_at": ts})
            changed_users.add(s["user_id"])
        elif end and end < ts and s["source"] == "owner" and s["status"] == "active":
            engine.db.update("subscriptions", s["id"], {"status": "canceled", "canceled_at": ts, "updated_at": ts})
            changed_users.add(s["user_id"])
        elif s["status"] == "past_due" and s.get("grace_until") and s["grace_until"] < ts:
            changed_users.add(s["user_id"])
    for uid in changed_users:
        enforce_workspace_limit(engine, uid)


# --- entitlements ------------------------------------------------------------------------------------------------

UNLIMITED_ENTITLEMENTS: dict[str, Any] = {"plan": None, "plan_code": "self_hosted", "max_workspaces": UNLIMITED,
                                          "storage_quota_mb": UNLIMITED, "max_members": UNLIMITED,
                                          "features": {k: True for k in FEATURES} | {"ai_monthly_tokens": UNLIMITED},
                                          "hosted": False}


def entitlements_for(engine: Engine, user_id: str) -> dict[str, Any]:
    """The limits that apply to the workspaces this account owns."""
    if not engine.settings.hosted:
        return dict(UNLIMITED_ENTITLEMENTS)
    from .owner import platform_role

    if platform_role(engine, user_id):   # the people who run the platform are not its customers
        return dict(UNLIMITED_ENTITLEMENTS) | {"plan_code": "platform_admin", "hosted": True}
    plan = plan_for(engine, user_id)
    if plan is None:
        ent = {"plan": None, "plan_code": None, "max_workspaces": 1, "storage_quota_mb": 100, "max_members": 3,
               "features": {k: False for k in FEATURES} | {"ask_finance": True, "ai_monthly_tokens": 500_000}}
    else:
        ent = {"plan": plan["id"], "plan_code": plan["code"], "max_workspaces": plan["max_workspaces"],
               "storage_quota_mb": plan["storage_quota_mb"], "max_members": plan["max_members"],
               "features": {k: bool(plan["features"].get(k, False)) for k in FEATURES}
               | {"ai_monthly_tokens": int(plan["features"].get("ai_monthly_tokens", 0))}}
    ov = engine.db.one("SELECT * FROM entitlement_overrides WHERE user_id = ? AND (expires_at IS NULL OR expires_at > ?)",
                       (user_id, now()))
    if ov:
        extra = loads(ov["overrides"], {})
        for k in ("max_workspaces", "storage_quota_mb", "max_members"):
            if k in extra:
                ent[k] = int(extra[k])
        for k, v in (extra.get("features") or {}).items():
            ent["features"][k] = v
        ent["overrides"] = True
    ent["hosted"] = True
    return ent


def set_overrides(engine: Engine, *, user_id: str, overrides: dict[str, Any], note: str | None, by: str,
                  expires_at: float | None) -> None:
    allowed = {"max_workspaces", "storage_quota_mb", "max_members", "features"}
    if set(overrides) - allowed:
        raise Invalid(f"Overrides may set {', '.join(sorted(allowed))}.")
    for k in ("max_workspaces", "storage_quota_mb", "max_members"):
        if k in overrides and (isinstance(overrides[k], bool) or not isinstance(overrides[k], int) or overrides[k] < 0):
            raise Invalid(f"{k} must be a whole number of zero or more.")
    for fk, fv in (overrides.get("features") or {}).items():
        if fk == "ai_monthly_tokens":
            if isinstance(fv, bool) or not isinstance(fv, int) or fv < 0:
                raise Invalid("ai_monthly_tokens is a whole number.")
        elif fk not in FEATURES or not isinstance(fv, bool):
            raise Invalid(f"Unknown feature {fk}.")
    if engine.db.one("SELECT 1 FROM users WHERE id = ?", (user_id,)) is None:
        raise NotFound("User not found.")
    with engine.db.tx() as c:
        if not overrides:
            c.execute("DELETE FROM entitlement_overrides WHERE user_id = ?", (user_id,))
        else:
            c.execute("INSERT INTO entitlement_overrides (user_id, overrides, note, granted_by, created_at, expires_at) VALUES "
                      "(?,?,?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET overrides = excluded.overrides, note = excluded.note, "
                      "granted_by = excluded.granted_by, created_at = excluded.created_at, expires_at = excluded.expires_at",
                      (user_id, dumps(overrides), note, by, now(), expires_at))
    engine.audit(None, by, "entitlements.override", user_id, {"overrides": overrides, "expires_at": expires_at},
                 resource_type="entitlements", decision="allowed", result="ok")
    enforce_workspace_limit(engine, user_id)


def workspace_owner(engine: Engine, ws_id: str) -> str | None:
    row = engine.db.one("SELECT user_id FROM members WHERE workspace_id = ? AND role = 'owner' ORDER BY created_at LIMIT 1",
                        (ws_id,))
    return row["user_id"] if row else None


def workspace_entitlements(engine: Engine, ws_id: str) -> dict[str, Any]:
    owner = workspace_owner(engine, ws_id)
    return entitlements_for(engine, owner) if owner else dict(UNLIMITED_ENTITLEMENTS)


def feature_enabled(engine: Engine, ws_id: str, feature: str) -> bool:
    from .settings import feature_flag

    if not feature_flag(engine, feature):
        return False
    if not engine.settings.hosted:
        return True
    return bool(workspace_entitlements(engine, ws_id)["features"].get(feature, False))


def require_feature(engine: Engine, ws_id: str, feature: str) -> None:
    """Refused with 403 ``feature_not_in_plan`` (hosted) or ``feature_disabled`` (switched off by the owner)."""
    from .settings import feature_flag

    if not feature_flag(engine, feature):
        raise Forbidden(f"{FEATURES.get(feature, feature)} is switched off on this installation.", code="feature_disabled")
    if engine.settings.hosted and not workspace_entitlements(engine, ws_id)["features"].get(feature, False):
        raise Forbidden(f"{FEATURES.get(feature, feature)} is not included in this workspace's plan.",
                        code="feature_not_in_plan")


def check_workspace_limit(c: Any, engine: Engine, user_id: str) -> None:
    """Inside the creating transaction (``BEGIN IMMEDIATE`` serialises writers, so two concurrent requests cannot
    both pass): the account may own at most ``max_workspaces`` workspaces."""
    if not engine.settings.hosted:
        return
    ent = entitlements_for(engine, user_id)
    limit = ent["max_workspaces"]
    if limit == UNLIMITED:
        return
    owned = c.execute("SELECT COUNT(*) FROM members WHERE user_id = ? AND role = 'owner'", (user_id,)).fetchone()[0]
    if owned >= limit:
        raise Forbidden(f"Your plan includes {limit} workspace{'s' if limit != 1 else ''}. Upgrade to add another.",
                        code="workspace_limit", details={"limit": limit, "owned": owned, "plan": ent.get("plan_code")})


def check_member_limit(engine: Engine, ws_id: str) -> None:
    if not engine.settings.hosted:
        return
    ent = workspace_entitlements(engine, ws_id)
    limit = ent["max_members"]
    if limit == UNLIMITED:
        return
    count = engine.db.scalar("SELECT COUNT(*) FROM members WHERE workspace_id = ?", (ws_id,)) + \
        engine.db.scalar("SELECT COUNT(*) FROM invites WHERE workspace_id = ? AND accepted_at IS NULL AND expires_at > ?",
                         (ws_id, now()))
    if count >= limit:
        raise Forbidden(f"This workspace's plan allows {limit} members.", code="member_limit", details={"limit": limit})


def ai_tokens_this_month(engine: Engine, user_id: str) -> int:
    """Tokens used this calendar month across the workspaces this account owns."""
    import datetime as dt

    start = dt.datetime.now(dt.UTC).replace(day=1, hour=0, minute=0, second=0, microsecond=0).timestamp()
    return int(engine.db.scalar(
        "SELECT COALESCE(SUM(u.input_tokens + u.output_tokens), 0) FROM usage u JOIN members m ON m.workspace_id = u.workspace_id "
        "AND m.role = 'owner' WHERE m.user_id = ? AND u.created_at >= ?", (user_id, start)) or 0)


def check_ai_allowance(engine: Engine, ws_id: str) -> None:
    if not engine.settings.hosted:
        return
    owner = workspace_owner(engine, ws_id)
    if not owner:
        return
    ent = entitlements_for(engine, owner)
    allowance = int(ent["features"].get("ai_monthly_tokens", 0) or 0)
    if allowance and ai_tokens_this_month(engine, owner) >= allowance:
        raise Forbidden("This workspace's monthly AI allowance is used up. It resets next month; upgrading raises it.",
                        code="ai_allowance", details={"allowance": allowance})


def enforce_workspace_limit(engine: Engine, user_id: str) -> None:
    """After a plan change: workspaces beyond the new limit become read-only (oldest ones stay writable); workspaces
    that were read-only for that reason are restored when the limit allows. Nothing is deleted."""
    from .support import set_state

    if not engine.settings.hosted:
        return
    ent = entitlements_for(engine, user_id)
    limit = ent["max_workspaces"]
    owned = engine.db.all("SELECT w.id FROM workspaces w JOIN members m ON m.workspace_id = w.id AND m.role = 'owner' "
                          "WHERE m.user_id = ? ORDER BY w.created_at", (user_id,))
    for index, w in enumerate(owned):
        state = engine.db.one("SELECT status, reason FROM workspace_state WHERE workspace_id = ?", (w["id"],))
        over = limit != UNLIMITED and index >= limit
        if over and (state is None or state["status"] == "active"):
            set_state(engine, w["id"], "read_only", reason="over_plan_limit", by=None)
        elif not over and state and state["status"] == "read_only" and state["reason"] == "over_plan_limit":
            set_state(engine, w["id"], "active", reason=None, by=None)
