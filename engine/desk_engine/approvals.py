"""Approvals: the one path by which an AI agent or a workflow gets a person's permission for a sensitive action.

An approval is a row bound to the workspace, the user who asked (the run's user, never the model), the tool, the
exact arguments (a canonical SHA-256 digest is stored beside the JSON), the time it was created and the time it
stops being valid. Its id is unpredictable (`ap_` + 160 random bits). Its life:

    pending ──decide──▶ approved ──claim──▶ executing ──finish──▶ executed
       │                   │
       ├── rejected        └── (expired / refused at claim: expired, replayed, lost permission, changed)
       └── expired

* ``request`` writes the row and audits it.
* ``decide`` is what an editor or above does from the inbox. It refuses an approval that has expired, that was
  already decided, or whose requester is no longer an active member with the role the tool needs.
* ``claim`` is done by the code that is about to execute. It is one atomic UPDATE: pending→executing is impossible,
  approved→executing succeeds for exactly one caller, and only if the workspace, tool, requester and argument
  digest still match the row and the expiry has not passed. Two concurrent executions of one approval cannot both
  win. The requester's membership and role are checked again at this moment, so a permission lost after the
  decision still blocks the action.
* ``finish`` records the outcome; the row never returns to a claimable state.

Validity depends on what the action does: anything that leaves the workspace (e-mail, chat, MCP servers,
workflows, imports from outside) is valid for ``OUTBOUND_TTL``; a write that stays inside (editing the person's
own file) for ``INTERNAL_TTL``. Approvals are bound to the user, not to a browser session; they are expired when
the requester loses the membership (``expire_for_member``), and when the conversation is stopped.
"""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING, Any

from .db import dumps, loads, new_id, now
from .errors import Conflict, Forbidden, Invalid, NotFound

if TYPE_CHECKING:
    from .app import Engine

OUTBOUND_TTL = 4 * 3600       # seconds: sends, messages, workflow runs, MCP tools, imports from outside
INTERNAL_TTL = 24 * 3600      # seconds: edits of the person's own files, workflow approval steps
INTERNAL_TOOLS = frozenset({"edit_file", "workflow.approval"})

CLAIM_FAILURES = {"expired", "replayed", "changed", "permission", "missing"}


def canonical_digest(workspace_id: str, requested_by: str | None, tool: str, arguments: Any) -> str:
    """SHA-256 over the canonical JSON of what is being approved; any change to any part changes it."""
    payload = json.dumps({"ws": workspace_id, "by": requested_by, "tool": tool, "args": arguments},
                         sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
    return hashlib.sha256(payload.encode()).hexdigest()


def ttl_for(tool: str) -> int:
    return INTERNAL_TTL if tool in INTERNAL_TOOLS else OUTBOUND_TTL


class Approvals:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    # -- lifecycle --------------------------------------------------------------------------------------------
    def request(self, *, workspace_id: str, source: str, source_id: str, tool: str, arguments: Any, summary: str,
                requested_by: str | None, request_id: str | None = None) -> str:
        approval_id = new_id("ap_")
        ts = now()
        self.engine.db.insert("approvals", {
            "id": approval_id, "workspace_id": workspace_id, "source": source, "source_id": source_id, "tool": tool,
            "arguments": dumps(arguments), "summary": summary[:4000], "status": "pending", "requested_by": requested_by,
            "created_at": ts, "expires_at": ts + ttl_for(tool),
            "args_digest": canonical_digest(workspace_id, requested_by, tool, arguments)})
        self.engine.audit(workspace_id, requested_by, "approval.requested", tool, {"source": source, "source_id": source_id},
                          resource_type="approval", decision="needs_approval", approval_id=approval_id,
                          result="pending", request_id=request_id)
        return approval_id

    def expire_stale(self, workspace_id: str | None = None) -> int:
        """Marks pending approvals past their expiry; returns how many."""
        params: list[Any] = [now()]
        sql = "UPDATE approvals SET status = 'expired' WHERE status = 'pending' AND expires_at IS NOT NULL AND expires_at < ?"
        if workspace_id:
            sql += " AND workspace_id = ?"
            params.append(workspace_id)
        with self.engine.db.tx() as c:
            return c.execute(sql, params).rowcount

    def expire_for_member(self, workspace_id: str, user_id: str, reason: str) -> int:
        """A member who leaves, is disabled or demoted below editor takes their pending requests with them."""
        with self.engine.db.tx() as c:
            n = c.execute("UPDATE approvals SET status = 'expired' WHERE workspace_id = ? AND requested_by = ? AND "
                          "status IN ('pending', 'approved')", (workspace_id, user_id)).rowcount
        if n:
            self.engine.audit(workspace_id, user_id, "approval.expired", None, {"count": n, "reason": reason},
                              resource_type="approval", decision="expired", result="expired")
        return n

    def decide(self, workspace: dict[str, Any], approval_id: str, user_id: str, role: str, approve: bool,
               note: str | None = None) -> dict[str, Any]:
        """The person's decision. Refuses when the approval has expired, was already decided, or when the person
        who asked for it can no longer act in the workspace."""
        from .web import role_at_least

        appr = self.engine.db.one("SELECT * FROM approvals WHERE id = ? AND workspace_id = ?",
                                  (approval_id, workspace["id"]))
        if appr is None:
            raise NotFound("Approval not found.")
        if not role_at_least(role, "editor"):
            self.engine.audit(workspace["id"], user_id, "approval.denied", appr["tool"], {"reason": "needs_editor", "role": role},
                              resource_type="approval", decision="denied", approval_id=approval_id, result="refused")
            raise Forbidden("Approving actions needs the editor role or higher.")
        if appr["status"] != "pending":
            self.engine.audit(workspace["id"], user_id, "approval.replayed", appr["tool"], {"status": appr["status"]},
                              resource_type="approval", decision="replayed", approval_id=approval_id, result="refused")
            raise Conflict(f"This approval was already {appr['status']}.", code="approval_decided")
        if appr["expires_at"] is not None and appr["expires_at"] < now():
            self.engine.db.execute("UPDATE approvals SET status = 'expired' WHERE id = ? AND status = 'pending'", (approval_id,))
            self.engine.audit(workspace["id"], user_id, "approval.expired", appr["tool"], {"expires_at": appr["expires_at"]},
                              resource_type="approval", decision="expired", approval_id=approval_id, result="refused")
            raise Conflict("This approval has expired. Ask again.", code="approval_expired")
        if approve and not self._requester_may(workspace["id"], appr):
            self.engine.db.execute("UPDATE approvals SET status = 'expired' WHERE id = ? AND status = 'pending'", (approval_id,))
            self.engine.audit(workspace["id"], user_id, "approval.expired", appr["tool"], {"reason": "requester_lost_permission"},
                              resource_type="approval", decision="expired", approval_id=approval_id, result="refused")
            raise Conflict("The person who asked for this can no longer act in this workspace.", code="approval_expired")
        status = "approved" if approve else "rejected"
        with self.engine.db.tx() as c:
            changed = c.execute("UPDATE approvals SET status = ?, decided_by = ?, decided_at = ?, result = ? "
                                "WHERE id = ? AND status = 'pending'",
                                (status, user_id, now(), dumps({"note": note}) if note else None, approval_id)).rowcount
        if changed != 1:
            self.engine.audit(workspace["id"], user_id, "approval.replayed", appr["tool"], None, resource_type="approval",
                              decision="replayed", approval_id=approval_id, result="refused")
            raise Conflict("This approval was decided by someone else a moment ago.", code="approval_decided")
        self.engine.audit(workspace["id"], user_id, f"approval.{status}", appr["tool"],
                          {"source": appr["source"], "requested_by": appr["requested_by"]},
                          resource_type="approval", decision=status, approval_id=approval_id, result=status)
        return self.engine.db.one("SELECT * FROM approvals WHERE id = ?", (approval_id,))

    def claim(self, *, approval_id: str, workspace_id: str, tool: str, requested_by: str | None, arguments: Any,
              executor: str | None) -> str | None:
        """Takes the approval for execution. Returns None on success or the reason it was refused (one of
        CLAIM_FAILURES), having recorded the refusal."""
        appr = self.engine.db.one("SELECT * FROM approvals WHERE id = ?", (approval_id,))
        if appr is None or appr["workspace_id"] != workspace_id:
            self._refused(workspace_id, executor, approval_id, tool, "missing")
            return "missing"
        digest = canonical_digest(workspace_id, requested_by, tool, arguments)
        if appr["tool"] != tool or appr["requested_by"] != requested_by or (appr["args_digest"] and appr["args_digest"] != digest):
            self._refused(workspace_id, executor, approval_id, tool, "changed")
            return "changed"
        if appr["expires_at"] is not None and appr["expires_at"] < now():
            self.engine.db.execute("UPDATE approvals SET status = 'expired' WHERE id = ? AND status IN ('pending','approved')",
                                   (approval_id,))
            self._refused(workspace_id, executor, approval_id, tool, "expired")
            return "expired"
        if not self._requester_may(workspace_id, appr, tool=tool):
            self.engine.db.execute("UPDATE approvals SET status = 'expired' WHERE id = ? AND status IN ('pending','approved')",
                                   (approval_id,))
            self._refused(workspace_id, executor, approval_id, tool, "permission")
            return "permission"
        # exactly one caller gets from approved to executing; the digest and expiry are part of the condition so a
        # row that changed between the read above and this write still cannot be taken
        with self.engine.db.tx() as c:
            won = c.execute("UPDATE approvals SET status = 'executing', executed_by = ?, executed_at = ? WHERE id = ? AND "
                            "status = 'approved' AND workspace_id = ? AND tool = ? AND (args_digest IS NULL OR args_digest = ?) "
                            "AND (expires_at IS NULL OR expires_at >= ?)",
                            (executor, now(), approval_id, workspace_id, tool, digest, now())).rowcount
        if won != 1:
            self._refused(workspace_id, executor, approval_id, tool, "replayed")
            return "replayed"
        return None

    def finish(self, approval_id: str, *, ok: bool, text: str | None) -> None:
        self.engine.db.execute("UPDATE approvals SET status = 'executed', result = ? WHERE id = ? AND status = 'executing'",
                               (dumps({"text": (text or "")[:2000], "ok": ok}), approval_id))

    # -- helpers ----------------------------------------------------------------------------------------------
    def _requester_may(self, workspace_id: str, appr: dict[str, Any], tool: str | None = None) -> bool:
        """The requester is still an active member, and still has the role the tool needs (if it is a static tool
        with one). The model never supplies any of this."""
        from .web import role_at_least

        if not appr["requested_by"]:
            return False
        row = self.engine.db.one("SELECT m.role FROM members m JOIN users u ON u.id = m.user_id WHERE m.workspace_id = ? "
                                 "AND m.user_id = ? AND u.disabled = 0", (workspace_id, appr["requested_by"]))
        if row is None:
            return False
        name = tool or appr["tool"]
        try:
            static = self.engine.tools.get_static(name)
        except Invalid:
            return True   # MCP and workflow approvals: membership is the bar; the admin's policy set the rest
        return role_at_least(row["role"], static.min_role)

    def _refused(self, workspace_id: str, executor: str | None, approval_id: str, tool: str, reason: str) -> None:
        self.engine.audit(workspace_id, executor, f"approval.{reason}", tool, {"reason": reason},
                          resource_type="approval", decision="replayed" if reason == "replayed" else "denied",
                          approval_id=approval_id, result="refused")

    def out(self, approval_id: str) -> dict[str, Any]:
        a = self.engine.db.one("SELECT * FROM approvals WHERE id = ?", (approval_id,))
        if a is None:
            raise NotFound("Approval not found.")
        a["arguments"] = loads(a["arguments"], {})
        a["result"] = loads(a["result"], None)
        a["expired"] = a["status"] == "expired" or (a["status"] == "pending" and a["expires_at"] is not None
                                                     and a["expires_at"] < now())
        return a
