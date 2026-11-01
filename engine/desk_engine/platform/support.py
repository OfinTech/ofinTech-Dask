"""Workspace state (active, read-only, suspended, archived) and time-limited support access.

State is enforced in ``web.load_workspace`` for every ``/api/w/{ws}/…`` route, so no path around it exists:

* ``read_only`` — reads work, every non-GET is refused (``workspace_read_only``). Used by the over-quota and
  over-plan-limit policies and by the owner for a dispute.
* ``suspended`` — every request is refused (``workspace_suspended``); members see why.
* ``archived`` — like suspended, reversible; the data stays until the owner deletes it.

Support access is the one exception to "platform administration never reads customer data": a platform administrator
may be granted the **viewer** role on one workspace for a bounded time, with a reason, and every such grant and
every request made under it is audited against that workspace, where its members can see it. It never grants a
write.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..db import new_id, now
from ..errors import Forbidden, Invalid, NotFound

if TYPE_CHECKING:
    from ..app import Engine

STATES = ("active", "read_only", "suspended", "archived")
MAX_SUPPORT_HOURS = 72


def get_state(engine: Engine, ws_id: str) -> dict[str, Any]:
    row = engine.db.one("SELECT * FROM workspace_state WHERE workspace_id = ?", (ws_id,))
    return row or {"workspace_id": ws_id, "status": "active", "reason": None, "changed_by": None, "changed_at": None}


def set_state(engine: Engine, ws_id: str, status: str, *, reason: str | None, by: str | None) -> dict[str, Any]:
    if status not in STATES:
        raise Invalid(f"State must be one of {', '.join(STATES)}.")
    if engine.db.one("SELECT 1 FROM workspaces WHERE id = ?", (ws_id,)) is None:
        raise NotFound("Workspace not found.")
    with engine.db.tx() as c:
        c.execute("INSERT INTO workspace_state (workspace_id, status, reason, changed_by, changed_at) VALUES (?,?,?,?,?) "
                  "ON CONFLICT(workspace_id) DO UPDATE SET status = excluded.status, reason = excluded.reason, "
                  "changed_by = excluded.changed_by, changed_at = excluded.changed_at", (ws_id, status, reason, by, now()))
    engine.audit(ws_id, by, "workspace.state", ws_id, {"status": status, "reason": reason}, resource_type="workspace",
                 decision="allowed", result="ok")
    return get_state(engine, ws_id)


def enforce(engine: Engine, ws_id: str, method: str) -> None:
    """Called for every workspace route after membership is established."""
    row = engine.db.one("SELECT status, reason FROM workspace_state WHERE workspace_id = ?", (ws_id,))
    if row is None or row["status"] == "active":
        return
    if row["status"] == "read_only":
        if method not in {"GET", "HEAD", "OPTIONS"}:
            why = {"over_plan_limit": "it is beyond the number of workspaces the owner's plan includes",
                   "over_quota": "its storage quota is used up"}.get(row["reason"] or "", "the platform owner set it so")
            raise Forbidden(f"This workspace is read-only because {why}.", code="workspace_read_only",
                            details={"reason": row["reason"]})
        return
    raise Forbidden("This workspace is suspended." if row["status"] == "suspended" else "This workspace is archived.",
                    code=f"workspace_{row['status']}", details={"reason": row["reason"]})


# --- support access -----------------------------------------------------------------------------------------------

def grant_support(engine: Engine, *, admin_id: str, ws_id: str, reason: str, hours: int) -> dict[str, Any]:
    if not reason or len(reason.strip()) < 5:
        raise Invalid("Give the reason for support access (at least a few words).")
    if hours < 1 or hours > MAX_SUPPORT_HOURS:
        raise Invalid(f"Support access lasts between 1 and {MAX_SUPPORT_HOURS} hours.")
    if engine.db.one("SELECT 1 FROM workspaces WHERE id = ?", (ws_id,)) is None:
        raise NotFound("Workspace not found.")
    ts = now()
    row = {"id": new_id("sup_"), "admin_id": admin_id, "workspace_id": ws_id, "reason": reason.strip()[:500],
           "granted_at": ts, "expires_at": ts + hours * 3600}
    engine.db.insert("support_access", row)
    engine.audit(ws_id, admin_id, "support.access.granted", row["id"], {"reason": row["reason"], "hours": hours},
                 resource_type="support_access", decision="allowed", result="ok")
    engine.notify_role(ws_id, "admin", "security", f"A platform administrator has read-only support access to this "
                       f"workspace for {hours} h: {row['reason'][:120]}", "#/oversight")
    return row


def revoke_support(engine: Engine, *, grant_id: str, by: str) -> None:
    row = engine.db.one("SELECT * FROM support_access WHERE id = ? AND revoked_at IS NULL", (grant_id,))
    if row is None:
        raise NotFound("Support grant not found.")
    engine.db.update("support_access", grant_id, {"revoked_at": now()})
    engine.audit(row["workspace_id"], by, "support.access.revoked", grant_id, None, resource_type="support_access",
                 decision="allowed", result="ok")


def active_support(engine: Engine, admin_id: str, ws_id: str) -> dict[str, Any] | None:
    return engine.db.one("SELECT * FROM support_access WHERE admin_id = ? AND workspace_id = ? AND revoked_at IS NULL AND "
                         "expires_at > ? ORDER BY expires_at DESC LIMIT 1", (admin_id, ws_id, now()))


def list_support(engine: Engine, *, active_only: bool = True) -> list[dict[str, Any]]:
    sql = ("SELECT s.*, u.email AS admin_email, w.name AS workspace_name FROM support_access s JOIN users u ON u.id = s.admin_id "
           "JOIN workspaces w ON w.id = s.workspace_id")
    params: tuple[Any, ...] = ()
    if active_only:
        sql += " WHERE s.revoked_at IS NULL AND s.expires_at > ?"
        params = (now(),)
    return engine.db.all(sql + " ORDER BY s.granted_at DESC LIMIT 200", params)
