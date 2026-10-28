"""Webhooks under Connections.

* **Incoming**: another system (an ERP, a bank portal, a CRM, a scheduler) posts JSON to
  ``POST /api/hooks/in/<hook id>``. Each delivery is verified, kept in a log the workspace can read, and can start a
  workflow with the payload as ``{{trigger.data}}``. The sender proves itself with the hook's secret, either as
  ``Authorization: Bearer <secret>`` or as an HMAC-SHA256 of the body in ``X-OfinTech-Signature-256``
  (``X-Hub-Signature-256`` is accepted too, so senders built for GitHub-style signatures work unchanged).
* **Outgoing**: when something happens in the workspace (a workflow run, an approval, a file, a forecast approved,
  a cost-centre change…) OfinTech Desk posts a signed JSON event to a URL the administrator set. Events come from
  the audit log, so every outgoing event is also a line a person can read under Settings → Audit log. Deliveries are
  queued in the database and sent by the scheduler, with three retries, so a slow or failing receiver never blocks
  the person's work.

Secrets are stored encrypted (and, for incoming hooks, as a hash for the bearer form); they are shown once.
"""

from __future__ import annotations

import hmac
import json
import logging
import time
from collections import defaultdict, deque
from typing import TYPE_CHECKING, Any

from .config import VERSION
from .db import dumps, loads, new_id, now
from .errors import AppError, Invalid, NotFound
from .security import check_outbound_url, hmac_sha256_hex, new_token, token_hash

if TYPE_CHECKING:
    from .app import Engine

log = logging.getLogger("desk.webhooks")

MAX_IN_BODY = 256 * 1024
MAX_OUT_PAYLOAD = 64 * 1024
IN_RATE_PER_MINUTE = 60
RETRY_AFTER = [60, 600, 3600]          # seconds between attempts: three retries
KEEP_DELIVERIES = 500                  # per hook

# event groups an outgoing hook can subscribe to, with the audit actions they cover (prefix match)
EVENT_GROUPS: dict[str, str] = {
    "workflow": "workflow runs started, finished or failed; workflows changed",
    "approval": "approvals decided",
    "file": "files created, revised, reviewed, edited, uploaded or deleted",
    "forecast": "forecasts created, submitted, approved, archived or made the plan",
    "cost_centre": "cost centres and their budgets",
    "kpi": "KPI targets and formulas changed",
    "memory": "memory proposed, saved, edited or deleted",
    "dataset": "datasets imported or deleted",
    "connection": "connections and syncs",
    "member": "members added, roles changed, removed",
    "document": "documents added or deleted",
    "test": "test events sent from the Connections page",
}
SKIP_DETAIL_KEYS = {"secret_id", "key", "token", "password", "api_key", "ingest_key", "bot_token", "secret"}


def _clean(detail: Any) -> Any:
    """Audit details without anything that looks like a credential."""
    if isinstance(detail, dict):
        return {k: _clean(v) for k, v in detail.items() if k.lower() not in SKIP_DETAIL_KEYS}
    if isinstance(detail, list):
        return [_clean(v) for v in detail]
    return detail


def matches(events: list[str], action: str) -> bool:
    return any(e == "*" or action == e or action.startswith(e + ".") for e in events)


class Webhooks:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    # -- storage --------------------------------------------------------------------------------------------------
    def _row(self, ws_id: str, hook_id: str) -> dict[str, Any]:
        row = self.engine.db.one("SELECT * FROM webhooks WHERE id = ? AND workspace_id = ?", (hook_id, ws_id))
        if row is None:
            raise NotFound("Webhook not found.")
        return row

    def public(self, row: dict[str, Any]) -> dict[str, Any]:
        out = {k: row[k] for k in ("id", "direction", "name", "enabled", "url", "workflow_id", "created_at", "updated_at",
                                   "last_delivery_at", "last_status", "secret_prefix")}
        out["enabled"] = bool(row["enabled"])
        out["events"] = loads(row["events"], [])
        if row["direction"] == "in":
            out["endpoint"] = f"{self.engine.settings.public_url.rstrip('/')}/api/hooks/in/{row['id']}"
        if row["workflow_id"]:
            out["workflow_name"] = self.engine.db.scalar("SELECT name FROM workflows WHERE id = ?", (row["workflow_id"],))
        counts = self.engine.db.one("SELECT COUNT(*) AS n, SUM(CASE WHEN status IN ('failed', 'rejected') THEN 1 ELSE 0 "
                                    "END) AS bad FROM webhook_deliveries WHERE webhook_id = ?", (row["id"],)) or {}
        out["deliveries"] = int(counts.get("n") or 0)
        out["failed"] = int(counts.get("bad") or 0)
        return out

    def list(self, ws_id: str) -> list[dict[str, Any]]:
        rows = self.engine.db.all("SELECT * FROM webhooks WHERE workspace_id = ? ORDER BY direction, created_at", (ws_id,))
        return [self.public(r) for r in rows]

    def _check_workflow(self, ws_id: str, workflow_id: str | None) -> None:
        if workflow_id and not self.engine.db.one("SELECT 1 FROM workflows WHERE id = ? AND workspace_id = ?",
                                                  (workflow_id, ws_id)):
            raise NotFound("Workflow not found.")

    def _check_url(self, url: str) -> str:
        url = url.strip()
        check_outbound_url(url, allow_private=self.engine.settings.allow_private_network_urls,
                           allow_http=self.engine.settings.is_desktop)
        return url

    def create(self, ws_id: str, user_id: str, *, direction: str, name: str, url: str | None = None,
               events: list[str] | None = None, workflow_id: str | None = None) -> dict[str, Any]:
        if direction not in ("in", "out"):
            raise Invalid("direction is 'in' or 'out'.")
        if direction == "out":
            if not url:
                raise Invalid("An outgoing webhook needs the address to post to.")
            url = self._check_url(url)
            events = [e for e in (events or []) if e == "*" or e in EVENT_GROUPS]
            if not events:
                raise Invalid("Choose at least one event group.", code="no_events")
        else:
            self._check_workflow(ws_id, workflow_id)
            url, events = None, []
        hid, ts = new_id("wh_"), now()
        secret = ("whs_" if direction == "in" else "who_") + new_token(30)
        secret_id = self.engine.secrets.put("webhook", {"secret": secret}, workspace_id=ws_id, secret_id=f"sec_{hid}")
        self.engine.db.insert("webhooks", {
            "id": hid, "workspace_id": ws_id, "direction": direction, "name": name.strip()[:120], "enabled": 1,
            "secret_id": secret_id, "secret_hash": token_hash(secret), "secret_prefix": secret[:8], "url": url,
            "events": dumps(events), "workflow_id": workflow_id, "created_by": user_id, "created_at": ts, "updated_at": ts})
        self.engine.audit(ws_id, user_id, "webhook.create", hid, {"direction": direction, "name": name})
        return self.public(self._row(ws_id, hid)) | {"secret": secret}

    def update(self, ws_id: str, user_id: str, hook_id: str, values: dict[str, Any]) -> dict[str, Any]:
        row = self._row(ws_id, hook_id)
        sets: dict[str, Any] = {}
        if "name" in values and values["name"]:
            sets["name"] = str(values["name"]).strip()[:120]
        if "enabled" in values and values["enabled"] is not None:
            sets["enabled"] = int(bool(values["enabled"]))
        if row["direction"] == "out":
            if values.get("url"):
                sets["url"] = self._check_url(values["url"])
            if values.get("events") is not None:
                events = [e for e in values["events"] if e == "*" or e in EVENT_GROUPS]
                if not events:
                    raise Invalid("Choose at least one event group.", code="no_events")
                sets["events"] = dumps(events)
        elif "workflow_id" in values:
            self._check_workflow(ws_id, values["workflow_id"])
            sets["workflow_id"] = values["workflow_id"]
        if sets:
            sets["updated_at"] = now()
            self.engine.db.update("webhooks", hook_id, sets, workspace_id=ws_id)
            self.engine.audit(ws_id, user_id, "webhook.update", hook_id, {"fields": sorted(k for k in sets if k != "updated_at")})
        return self.public(self._row(ws_id, hook_id))

    def rotate_secret(self, ws_id: str, user_id: str, hook_id: str) -> str:
        row = self._row(ws_id, hook_id)
        secret = ("whs_" if row["direction"] == "in" else "who_") + new_token(30)
        self.engine.secrets.put("webhook", {"secret": secret}, workspace_id=ws_id, secret_id=f"sec_{hook_id}")
        self.engine.db.update("webhooks", hook_id, {"secret_hash": token_hash(secret), "secret_prefix": secret[:8],
                                                    "updated_at": now()}, workspace_id=ws_id)
        self.engine.audit(ws_id, user_id, "webhook.rotate", hook_id)
        return secret

    def delete(self, ws_id: str, user_id: str, hook_id: str) -> None:
        row = self._row(ws_id, hook_id)
        self.engine.secrets.delete(row["secret_id"])
        self.engine.db.execute("DELETE FROM webhooks WHERE id = ?", (hook_id,))
        self.engine.audit(ws_id, user_id, "webhook.delete", hook_id, {"name": row["name"]})

    def deliveries(self, ws_id: str, hook_id: str, limit: int = 50) -> list[dict[str, Any]]:
        self._row(ws_id, hook_id)
        rows = self.engine.db.all("SELECT id, event, status, http_status, attempts, payload, response, run_id, created_at, "
                                  "next_attempt_at, finished_at FROM webhook_deliveries WHERE webhook_id = ? ORDER BY "
                                  "created_at DESC LIMIT ?", (hook_id, min(max(limit, 1), 200)))
        for r in rows:
            r["payload"] = loads(r["payload"], None) if r["payload"] else None
        return rows

    def _trim(self, hook_id: str) -> None:
        self.engine.db.execute("DELETE FROM webhook_deliveries WHERE webhook_id = ? AND id NOT IN (SELECT id FROM "
                               "webhook_deliveries WHERE webhook_id = ? ORDER BY created_at DESC LIMIT ?)",
                               (hook_id, hook_id, KEEP_DELIVERIES))

    # -- incoming -------------------------------------------------------------------------------------------------
    def _secret(self, row: dict[str, Any]) -> str:
        return str(self.engine.secrets.get(row["secret_id"], workspace_id=row["workspace_id"]).get("secret", ""))

    def verify_incoming(self, row: dict[str, Any], headers: dict[str, str], body: bytes) -> bool:
        """Bearer secret (hash compare) or an HMAC-SHA256 signature of the body, both in constant time."""
        auth = headers.get("authorization", "")
        if auth.lower().startswith("bearer "):
            return hmac.compare_digest(token_hash(auth[7:].strip()), row["secret_hash"] or "")
        signature = headers.get("x-ofintech-signature-256") or headers.get("x-hub-signature-256") or ""
        if signature.startswith("sha256="):
            expected = "sha256=" + hmac_sha256_hex(self._secret(row), body)
            return hmac.compare_digest(signature, expected)
        return False

    def rate_limited(self, hook_id: str) -> bool:
        q, t = self._hits[hook_id], time.monotonic()
        while q and t - q[0] > 60:
            q.popleft()
        if len(q) >= IN_RATE_PER_MINUTE:
            return True
        q.append(t)
        return False

    def receive(self, row: dict[str, Any], event: str | None, body: bytes) -> dict[str, Any]:
        """Records a verified delivery and starts the linked workflow, if any."""
        try:
            data: Any = json.loads(body) if body.strip() else {}
        except json.JSONDecodeError:
            data = None
        text = json.dumps(data, ensure_ascii=False, indent=1)[:8000] if data is not None else body.decode("utf-8", "replace")[:8000]
        if not event and isinstance(data, dict):
            ev = data.get("event") or data.get("type")
            event = str(ev)[:80] if isinstance(ev, str) else None
        did, ts = new_id("wd_"), now()
        status, run_id, response = "received", None, None
        if row["workflow_id"]:
            wf = self.engine.db.one("SELECT * FROM workflows WHERE id = ? AND workspace_id = ?",
                                    (row["workflow_id"], row["workspace_id"]))
            if wf is None or not wf["enabled"]:
                status, response = "received", "The linked workflow is missing or switched off."
            else:
                workspace = self.engine.db.one("SELECT * FROM workspaces WHERE id = ?", (row["workspace_id"],))
                trigger = {"type": "webhook", "source": row["name"], "event": event,
                           "data": data if isinstance(data, (dict, list)) else None, "text": text, "received_at": ts}
                try:
                    run_id = self.engine.workflows.start(workspace, {**wf, "definition": json.loads(wf["definition"])},
                                                         trigger, wf["created_by"])
                    status = "workflow_started"
                except AppError as exc:
                    status, response = "received", f"The workflow could not start: {exc.message}"
        self.engine.db.insert("webhook_deliveries", {
            "id": did, "webhook_id": row["id"], "workspace_id": row["workspace_id"], "direction": "in", "event": event,
            "status": status, "http_status": 202, "attempts": 1, "payload": dumps(data) if data is not None else dumps(text),
            "response": response, "run_id": run_id, "created_at": ts, "finished_at": ts})
        self.engine.db.update("webhooks", row["id"], {"last_delivery_at": ts, "last_status": status})
        self._trim(row["id"])
        return {"delivery_id": did, "status": status, "run_id": run_id}

    def record_rejected(self, row: dict[str, Any], reason: str) -> None:
        ts = now()
        self.engine.db.insert("webhook_deliveries", {
            "id": new_id("wd_"), "webhook_id": row["id"], "workspace_id": row["workspace_id"], "direction": "in",
            "event": None, "status": "rejected", "http_status": 401, "attempts": 1, "payload": None, "response": reason,
            "created_at": ts, "finished_at": ts})
        self.engine.db.update("webhooks", row["id"], {"last_delivery_at": ts, "last_status": "rejected"})
        self._trim(row["id"])

    # -- outgoing -------------------------------------------------------------------------------------------------
    def emit(self, ws_id: str | None, user_id: str | None, action: str, target: str | None, detail: Any) -> None:
        """Queues the event for every enabled outgoing hook of the workspace whose groups cover it."""
        if not ws_id or action.startswith("webhook."):
            return
        try:
            hooks = self.engine.db.all("SELECT * FROM webhooks WHERE workspace_id = ? AND direction = 'out' AND enabled = 1",
                                       (ws_id,))
        except Exception:  # noqa: BLE001 - never let a hook break the action being audited
            return
        wanted = [h for h in hooks if matches(loads(h["events"], []), action)]
        if not wanted:
            return
        user = self.engine.db.one("SELECT id, name, email FROM users WHERE id = ?", (user_id,)) if user_id else None
        ws = self.engine.db.one("SELECT id, name FROM workspaces WHERE id = ?", (ws_id,))
        ts = now()
        for h in wanted:
            did = new_id("wd_")
            payload = {"id": did, "event": action, "occurred_at": ts, "workspace": ws, "target": target,
                       "detail": _clean(detail), "user": {"id": user["id"], "name": user["name"]} if user else None}
            text = dumps(payload)
            if len(text) > MAX_OUT_PAYLOAD:
                payload["detail"] = {"truncated": True}
                text = dumps(payload)
            self.engine.db.insert("webhook_deliveries", {
                "id": did, "webhook_id": h["id"], "workspace_id": ws_id, "direction": "out", "event": action,
                "status": "pending", "attempts": 0, "payload": text, "created_at": ts, "next_attempt_at": ts})

    def send_test(self, ws_id: str, user_id: str, hook_id: str) -> dict[str, Any]:
        row = self._row(ws_id, hook_id)
        if row["direction"] != "out":
            raise Invalid("Only outgoing webhooks send test events.")
        ws = self.engine.db.one("SELECT id, name FROM workspaces WHERE id = ?", (ws_id,))
        did, ts = new_id("wd_"), now()
        payload = {"id": did, "event": "test.ping", "occurred_at": ts, "workspace": ws, "target": hook_id,
                   "detail": {"message": "Test event from OfinTech Desk"}, "user": None}
        self.engine.db.insert("webhook_deliveries", {
            "id": did, "webhook_id": hook_id, "workspace_id": ws_id, "direction": "out", "event": "test.ping",
            "status": "pending", "attempts": 0, "payload": dumps(payload), "created_at": ts, "next_attempt_at": ts})
        self.engine.audit(ws_id, user_id, "webhook.test", hook_id)
        return {"delivery_id": did}

    async def deliver_pending(self, limit: int = 20) -> int:
        """Posts due deliveries; called by the scheduler every few seconds. Returns how many were attempted."""
        import httpx2 as httpx

        due = self.engine.db.all("SELECT d.*, w.url, w.secret_id, w.enabled, w.workspace_id AS ws FROM webhook_deliveries d "
                                 "JOIN webhooks w ON w.id = d.webhook_id WHERE d.status = 'pending' AND d.next_attempt_at <= ? "
                                 "ORDER BY d.next_attempt_at LIMIT ?", (now(), limit))
        sent = 0
        for d in due:
            if not d["enabled"] or not d["url"]:
                self.engine.db.update("webhook_deliveries", d["id"], {"status": "failed", "response": "webhook disabled",
                                                                      "finished_at": now()})
                continue
            secret = str(self.engine.secrets.get(d["secret_id"], workspace_id=d["ws"]).get("secret", ""))
            body = (d["payload"] or "{}").encode()
            headers = {"Content-Type": "application/json", "User-Agent": f"OfinTech-Desk/{VERSION}",
                       "X-OfinTech-Event": d["event"] or "", "X-OfinTech-Delivery": d["id"],
                       "X-OfinTech-Signature-256": "sha256=" + hmac_sha256_hex(secret, body)}
            attempt = int(d["attempts"]) + 1
            status, code, response = "failed", None, ""
            try:
                async with httpx.AsyncClient(timeout=15, transport=self.engine.http_transport, follow_redirects=False) as c:
                    resp = await c.post(d["url"], content=body, headers=headers)
                code = resp.status_code
                response = resp.text[:1000]
                status = "delivered" if 200 <= code < 300 else "failed"
            except Exception as exc:  # noqa: BLE001 - the receiver is someone else's server
                response = f"{type(exc).__name__}: {exc}"[:1000]
            sets: dict[str, Any] = {"attempts": attempt, "http_status": code, "response": response}
            if status == "delivered" or attempt > len(RETRY_AFTER):
                sets |= {"status": status, "finished_at": now()}
            else:
                sets |= {"status": "pending", "next_attempt_at": now() + RETRY_AFTER[attempt - 1]}
            self.engine.db.update("webhook_deliveries", d["id"], sets)
            self.engine.db.update("webhooks", d["webhook_id"], {"last_delivery_at": now(),
                                                                "last_status": status if sets.get("finished_at") else "retrying"})
            self._trim(d["webhook_id"])
            sent += 1
        return sent

    def redeliver(self, ws_id: str, user_id: str, hook_id: str, delivery_id: str) -> None:
        self._row(ws_id, hook_id)
        d = self.engine.db.one("SELECT * FROM webhook_deliveries WHERE id = ? AND webhook_id = ? AND direction = 'out'",
                               (delivery_id, hook_id))
        if d is None:
            raise NotFound("Delivery not found.")
        self.engine.db.update("webhook_deliveries", delivery_id, {"status": "pending", "attempts": 0, "next_attempt_at": now(),
                                                                  "finished_at": None})
        self.engine.audit(ws_id, user_id, "webhook.redeliver", hook_id, {"delivery_id": delivery_id})
