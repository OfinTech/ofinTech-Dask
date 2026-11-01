"""Who may use the Owner Control Center, and what each of them may do.

Two platform roles exist, kept in ``platform_admins``:

* ``owner`` — the product owner. Everything, including billing credentials, plans, security policy and the right to
  delegate.
* ``admin`` — a delegated platform administrator. Reads everything the dashboard shows, administers accounts,
  workspaces, sales, notifications and e-mail templates; cannot touch billing credentials, integration secrets,
  security policy, plans and prices, or other administrators.

Provisioning is never done from the public interface. An owner is created by one of:

1. ``DESK_PLATFORM_OWNERS=olivia@example.com,…`` — applied at start-up to existing accounts and at sign-up to new ones;
2. ``python -m desk_engine owner grant olivia@example.com`` on the server;
3. an existing owner delegating from the dashboard (``POST /api/owner/admins``, step-up required, dual approval when
   the policy is on).

For compatibility with existing installations the *instance administrator* (the first account created on a fresh
Team Server) also counts as a platform owner **unless** ``DESK_PLATFORM_OWNERS`` is set, in which case only the
listed owners (and those they delegate) are owners. The desktop app has no platform administration at all.

Authorization is deny by default: a route names the permission it needs and anything not listed for the caller's
role is refused with 403 (and audited by the router like every other refusal).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from ..db import dumps, loads, new_id, now
from ..errors import Forbidden, Invalid, NotFound

if TYPE_CHECKING:
    from ..app import Engine
    from ..web import Ctx

log = logging.getLogger("desk.platform")

PLATFORM_ROLES = ("owner", "admin")

# permission → the platform roles that hold it
PERMISSIONS: dict[str, frozenset[str]] = {
    "dashboard.read": frozenset({"owner", "admin"}),
    "users.read": frozenset({"owner", "admin"}),
    "users.manage": frozenset({"owner", "admin"}),
    "workspaces.read": frozenset({"owner", "admin"}),
    "workspaces.manage": frozenset({"owner", "admin"}),
    "workspaces.delete": frozenset({"owner"}),
    "support.access": frozenset({"owner", "admin"}),
    "subscriptions.read": frozenset({"owner", "admin"}),
    "subscriptions.manage": frozenset({"owner"}),
    "plans.read": frozenset({"owner", "admin"}),
    "plans.manage": frozenset({"owner"}),
    "billing.read": frozenset({"owner", "admin"}),
    "billing.configure": frozenset({"owner"}),
    "coupons.read": frozenset({"owner", "admin"}),
    "coupons.manage": frozenset({"owner"}),
    "sales.read": frozenset({"owner", "admin"}),
    "sales.manage": frozenset({"owner", "admin"}),
    "analytics.read": frozenset({"owner", "admin"}),
    "analytics.export": frozenset({"owner", "admin"}),
    "notifications.read": frozenset({"owner", "admin"}),
    "notifications.manage": frozenset({"owner", "admin"}),
    "email.read": frozenset({"owner", "admin"}),
    "email.templates": frozenset({"owner", "admin"}),
    "email.configure": frozenset({"owner"}),
    "security.read": frozenset({"owner", "admin"}),
    "security.configure": frozenset({"owner"}),
    "integrations.read": frozenset({"owner", "admin"}),
    "integrations.configure": frozenset({"owner"}),
    "storage.read": frozenset({"owner", "admin"}),
    "storage.configure": frozenset({"owner"}),
    "audit.read": frozenset({"owner", "admin"}),
    "settings.read": frozenset({"owner", "admin"}),
    "settings.manage": frozenset({"owner"}),
    "admins.manage": frozenset({"owner"}),
}


def platform_role(engine: Engine, user_id: str | None) -> str | None:
    """``owner``, ``admin`` or None for this account."""
    if not user_id or engine.settings.is_desktop:
        return None
    row = engine.db.one("SELECT role FROM platform_admins WHERE user_id = ?", (user_id,))
    if row:
        return row["role"]
    if not engine.settings.platform_owners:
        # compatibility: the instance administrator of an installation without an explicit owner list
        admin = engine.db.scalar("SELECT value FROM settings WHERE key = 'instance_admin'")
        if admin and loads(admin) == user_id:
            return "owner"
    return None


def permissions_for(role: str | None) -> list[str]:
    return sorted(p for p, roles in PERMISSIONS.items() if role in roles)


def has_permission(engine: Engine, user_id: str | None, permission: str) -> bool:
    if permission not in PERMISSIONS:
        raise ValueError(f"unknown platform permission {permission!r}")
    role = platform_role(engine, user_id)
    return role is not None and role in PERMISSIONS[permission]


def require(ctx: Ctx, permission: str) -> str:
    """The deny-by-default gate used by every owner route. Returns the caller's platform role."""
    if ctx.engine.settings.is_desktop:
        raise NotFound("Not found.")   # the desktop app has no platform administration; the area does not exist
    role = platform_role(ctx.engine, ctx.user["id"] if ctx.user else None)
    if role is None or role not in PERMISSIONS.get(permission, frozenset()):
        raise Forbidden("This area is for platform administrators.", code="owner_required")
    return role


def list_admins(engine: Engine) -> list[dict[str, Any]]:
    rows = engine.db.all("SELECT a.user_id, a.role, a.granted_by, a.created_at, u.email, u.name FROM platform_admins a "
                         "JOIN users u ON u.id = a.user_id ORDER BY a.role, u.email")
    if not engine.settings.platform_owners:
        admin = engine.db.scalar("SELECT value FROM settings WHERE key = 'instance_admin'")
        uid = loads(admin) if admin else None
        if uid and not any(r["user_id"] == uid for r in rows):
            u = engine.db.one("SELECT email, name FROM users WHERE id = ?", (uid,))
            if u:
                rows.insert(0, {"user_id": uid, "role": "owner", "granted_by": "instance_admin", "created_at": None, **u})
    return rows


def grant(engine: Engine, *, user_id: str, role: str, granted_by: str | None) -> None:
    if role not in PLATFORM_ROLES:
        raise Invalid("Role must be 'owner' or 'admin'.")
    if engine.db.one("SELECT 1 FROM users WHERE id = ? AND is_local = 0", (user_id,)) is None:
        raise NotFound("User not found.")
    with engine.db.tx() as c:
        c.execute("INSERT INTO platform_admins (user_id, role, granted_by, created_at) VALUES (?,?,?,?) "
                  "ON CONFLICT(user_id) DO UPDATE SET role = excluded.role, granted_by = excluded.granted_by",
                  (user_id, role, granted_by, now()))
    engine.audit(None, granted_by, "platform.admin.grant", user_id, {"role": role}, resource_type="platform_admin",
                 decision="allowed", result="ok")


def revoke(engine: Engine, *, user_id: str, revoked_by: str | None) -> None:
    owners = engine.db.scalar("SELECT COUNT(*) FROM platform_admins WHERE role = 'owner'")
    row = engine.db.one("SELECT role FROM platform_admins WHERE user_id = ?", (user_id,))
    if row is None:
        raise NotFound("Not a platform administrator.")
    if row["role"] == "owner" and owners <= 1 and engine.settings.platform_owners:
        raise Invalid("The last platform owner cannot be removed.", code="last_owner")
    engine.db.execute("DELETE FROM platform_admins WHERE user_id = ?", (user_id,))
    engine.audit(None, revoked_by, "platform.admin.revoke", user_id, None, resource_type="platform_admin",
                 decision="allowed", result="ok")


def apply_bootstrap(engine: Engine) -> None:
    """At start-up: every address in ``DESK_PLATFORM_OWNERS`` that already has an account becomes an owner."""
    for email in engine.settings.platform_owners:
        user = engine.db.one("SELECT id FROM users WHERE email = ? AND is_local = 0", (email,))
        if user and not engine.db.one("SELECT 1 FROM platform_admins WHERE user_id = ?", (user["id"],)):
            with engine.db.tx() as c:
                c.execute("INSERT INTO platform_admins (user_id, role, granted_by, created_at) VALUES (?,?,?,?)",
                          (user["id"], "owner", "bootstrap", now()))
            engine.audit(None, None, "platform.admin.grant", user["id"], {"role": "owner", "via": "DESK_PLATFORM_OWNERS"},
                         resource_type="platform_admin", decision="allowed", result="ok")
            log.info("platform owner provisioned from DESK_PLATFORM_OWNERS: %s", email)


def on_signup(engine: Engine, user_id: str, email: str) -> None:
    """A new account whose address is in the owner list is an owner from its first sign-in."""
    if email.lower() in engine.settings.platform_owners:
        with engine.db.tx() as c:
            c.execute("INSERT OR IGNORE INTO platform_admins (user_id, role, granted_by, created_at) VALUES (?,?,?,?)",
                      (user_id, "owner", "bootstrap", now()))
        engine.audit(None, None, "platform.admin.grant", user_id, {"role": "owner", "via": "DESK_PLATFORM_OWNERS"},
                     resource_type="platform_admin", decision="allowed", result="ok")


# --- dual approval for critical changes ------------------------------------------------------------------------------

CRITICAL_KINDS = {"admin.grant", "billing.configure", "plan.update", "security.configure"}


def dual_approval_on(engine: Engine) -> bool:
    from .settings import get_setting

    return bool(get_setting(engine, "security.dual_approval"))


def request_change(engine: Engine, *, kind: str, payload: dict[str, Any], requested_by: str) -> dict[str, Any]:
    """Records a critical change for a second owner to approve. Returns the request row."""
    if kind not in CRITICAL_KINDS:
        raise ValueError(kind)
    row = {"id": new_id("chg_"), "kind": kind, "payload": dumps(payload), "requested_by": requested_by,
           "status": "pending", "created_at": now()}
    engine.db.insert("owner_change_requests", row)
    engine.audit(None, requested_by, "platform.change.requested", row["id"], {"kind": kind},
                 resource_type="change_request", decision="allowed", result="pending")
    return row


def pending_changes(engine: Engine) -> list[dict[str, Any]]:
    rows = engine.db.all("SELECT c.*, u.email AS requested_by_email FROM owner_change_requests c LEFT JOIN users u ON "
                         "u.id = c.requested_by WHERE c.status = 'pending' ORDER BY c.created_at")
    for r in rows:
        r["payload"] = loads(r["payload"], {})
    return rows


def decide_change(engine: Engine, *, change_id: str, decided_by: str, approve: bool) -> dict[str, Any]:
    row = engine.db.one("SELECT * FROM owner_change_requests WHERE id = ? AND status = 'pending'", (change_id,))
    if row is None:
        raise NotFound("Change request not found or already decided.")
    if row["requested_by"] == decided_by and approve:
        raise Forbidden("A critical change needs a second platform owner's approval.", code="second_owner")
    status = "approved" if approve else "rejected"
    engine.db.update("owner_change_requests", change_id, {"status": status, "decided_by": decided_by, "decided_at": now()})
    engine.audit(None, decided_by, f"platform.change.{status}", change_id, {"kind": row["kind"]},
                 resource_type="change_request", decision="allowed" if approve else "denied", result=status)
    row["status"] = status
    row["payload"] = loads(row["payload"], {})
    return row
