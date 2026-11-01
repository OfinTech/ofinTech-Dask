"""Platform notifications: announcements to people, alerts to the owner, preferences and consent.

Platform notifications are account-level (``user_notifications``), separate from the workspace notifications the
app already has, because an announcement or a billing message is about the person's account, not a workspace. Each
category can be switched off per channel by the person (``notification_preferences``), except ``security`` and
``billing``, which are transactional and always delivered in-app (e-mail can still be declined). Delivery is
recorded per (announcement, user, channel), so re-running a send never delivers twice.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from ..db import dumps, loads, new_id, now
from ..errors import Invalid, NotFound
from .settings import get_setting

if TYPE_CHECKING:
    from ..app import Engine

log = logging.getLogger("desk.notifications")

CATEGORIES = ("security", "billing", "account", "workspace", "storage", "product", "maintenance", "incident", "sales", "general")
MANDATORY_IN_APP = {"security", "billing"}
CHANNELS = ("in_app", "email", "webhook")

ALERT_EVENTS = {  # owner alert key → default channels
    "new_registration": {"in_app": True, "email": False},
    "email_verified": {"in_app": False, "email": False},
    "first_workspace": {"in_app": True, "email": False},
    "first_paid_subscription": {"in_app": True, "email": True},
    "subscription_upgrade": {"in_app": True, "email": False},
    "subscription_downgrade": {"in_app": True, "email": False},
    "subscription_cancellation": {"in_app": True, "email": True},
    "payment_failed": {"in_app": True, "email": True},
    "suspicious_registration": {"in_app": True, "email": True},
    "repeated_failed_logins": {"in_app": True, "email": True, "threshold": 10},
    "new_sales_lead": {"in_app": True, "email": True},
    "billing_event_failed": {"in_app": True, "email": True},
    "storage_quota_reached": {"in_app": True, "email": False},
}


def alert_rules(engine: Engine) -> dict[str, dict[str, Any]]:
    saved = get_setting(engine, "alerts.rules") or {}
    return {k: {**v, **(saved.get(k) or {})} for k, v in ALERT_EVENTS.items()}


def preferences(engine: Engine, user_id: str) -> dict[str, dict[str, bool]]:
    rows = {r["category"]: r for r in engine.db.all("SELECT * FROM notification_preferences WHERE user_id = ?", (user_id,))}
    out = {}
    for cat in CATEGORIES:
        r = rows.get(cat)
        out[cat] = {"in_app": True if cat in MANDATORY_IN_APP else bool(r["in_app"]) if r else True,
                    "email": bool(r["email"]) if r else True}
    return out


def set_preferences(engine: Engine, user_id: str, prefs: dict[str, dict[str, bool]]) -> dict[str, dict[str, bool]]:
    with engine.db.tx() as c:
        for cat, v in prefs.items():
            if cat not in CATEGORIES or not isinstance(v, dict):
                raise Invalid(f"Unknown notification category {cat}.")
            in_app = True if cat in MANDATORY_IN_APP else bool(v.get("in_app", True))
            c.execute("INSERT INTO notification_preferences (user_id, category, in_app, email) VALUES (?,?,?,?) ON CONFLICT(user_id, category) "
                      "DO UPDATE SET in_app = excluded.in_app, email = excluded.email", (user_id, cat, int(in_app), int(bool(v.get("email", True)))))
    return preferences(engine, user_id)


def push(engine: Engine, user_id: str, *, category: str, title: str, body: str | None = None, link: str | None = None,
         email_template: str | None = None, email_vars: dict[str, Any] | None = None, dedupe: str | None = None) -> str | None:
    """One platform notification to one person: in-app (unless declined) and, with a template, by e-mail (unless
    declined). Returns the in-app notification id."""
    from .email import enqueue

    if category not in CATEGORIES:
        raise ValueError(category)
    prefs = preferences(engine, user_id)[category]
    user = engine.db.one("SELECT * FROM users WHERE id = ? AND disabled = 0", (user_id,))
    if user is None:
        return None
    nid = None
    if prefs["in_app"]:
        if dedupe and engine.db.one("SELECT 1 FROM user_notifications WHERE user_id = ? AND link = ? AND title = ?", (user_id, dedupe, title[:300])):
            return None
        nid = new_id("un_")
        engine.db.insert("user_notifications", {"id": nid, "user_id": user_id, "category": category, "title": title[:300],
                                                "body": (body or "")[:4000] or None, "link": link, "created_at": now()})
        engine.events.publish(f"user:{user_id}", {"type": "notification", "title": title[:300], "link": link})
    if email_template and prefs["email"]:
        enqueue(engine, to=user["email"], template_key=email_template, lang=user.get("language") or "en",
                vars={"name": user["name"], **(email_vars or {})}, category=category,
                dedupe_key=f"{email_template}:{user_id}:{dedupe}" if dedupe else None)
    return nid


def user_event(engine: Engine, user: dict[str, Any], template_key: str, vars: dict[str, Any], *, category: str,
               dedupe: str | None = None) -> None:
    """A transactional message to a person (billing, account): in-app line plus the templated e-mail."""
    from .email import get_template, render

    t = get_template(engine, template_key, user.get("language") or "en")
    title = render(t["subject"], {"platform": get_setting(engine, "platform.name"), **vars})
    push(engine, user["id"], category=category, title=title, link="#/settings/plan" if category == "billing" else None,
         email_template=template_key, email_vars=vars, dedupe=dedupe)


def list_for_user(engine: Engine, user_id: str, *, unread_only: bool = False, limit: int = 50) -> list[dict[str, Any]]:
    sql = "SELECT * FROM user_notifications WHERE user_id = ?"
    if unread_only:
        sql += " AND read_at IS NULL"
    return engine.db.all(sql + " ORDER BY created_at DESC LIMIT ?", (user_id, limit))


def mark_read(engine: Engine, user_id: str, ids: list[str] | None) -> int:
    if ids is None:
        return engine.db.execute("UPDATE user_notifications SET read_at = ? WHERE user_id = ? AND read_at IS NULL", (now(), user_id))
    n = 0
    for nid in ids[:200]:
        n += engine.db.execute("UPDATE user_notifications SET read_at = ? WHERE user_id = ? AND id = ? AND read_at IS NULL", (now(), user_id, nid))
    return n


# --- owner alerts -----------------------------------------------------------------------------------------------------

def owner_alert(engine: Engine, event: str, title: str, *, category: str = "general", link: str | None = None,
                body: str | None = None) -> None:
    """Tells every platform administrator about something that happened, by the channels the alert rule allows."""
    from .owner import list_admins

    rule = alert_rules(engine).get(event)
    if rule is None or not (rule.get("in_app") or rule.get("email")):
        return
    try:
        for admin in list_admins(engine):
            uid = admin["user_id"]
            if rule.get("in_app"):
                engine.db.insert("user_notifications", {"id": new_id("un_"), "user_id": uid, "category": category, "title": title[:300],
                                                        "body": (body or "")[:4000] or None, "link": link, "created_at": now()})
                engine.events.publish(f"user:{uid}", {"type": "notification", "title": title[:300], "link": link})
            if rule.get("email"):
                from .email import enqueue
                enqueue(engine, to=admin["email"], template_key="owner_alert", lang="en",
                        vars={"title": title, "body": body or "", "url": f"{engine.settings.public_url}/{(link or '').lstrip('/')}"},
                        category="general")
    except Exception:  # pragma: no cover - an alert must never break the action it reports
        log.exception("owner alert %s could not be delivered", event)


# --- announcements -----------------------------------------------------------------------------------------------------

def _row(a: dict[str, Any]) -> dict[str, Any]:
    a = dict(a)
    a["channels"] = loads(a.get("channels"), ["in_app"])
    a["target"] = loads(a.get("target"), {})
    return a


def list_announcements(engine: Engine) -> list[dict[str, Any]]:
    return [_row(a) for a in engine.db.all("SELECT * FROM announcements ORDER BY created_at DESC LIMIT 200")]


def _validate_target(engine: Engine, target: dict[str, Any]) -> dict[str, Any]:
    allowed = {"all", "plan_codes", "new_users_days", "workspace_ids", "user_ids", "roles"}
    if not isinstance(target, dict) or set(target) - allowed:
        raise Invalid(f"Target may use {', '.join(sorted(allowed))}.")
    out: dict[str, Any] = {}
    if target.get("all"):
        out["all"] = True
    for k in ("plan_codes", "workspace_ids", "user_ids", "roles"):
        v = target.get(k)
        if v is not None:
            if not isinstance(v, list) or any(not isinstance(x, str) for x in v) or len(v) > 500:
                raise Invalid(f"{k} is a list of ids.")
            out[k] = v
    if target.get("new_users_days") is not None:
        d = target["new_users_days"]
        if isinstance(d, bool) or not isinstance(d, int) or not 1 <= d <= 365:
            raise Invalid("new_users_days is 1 to 365.")
        out["new_users_days"] = d
    if not out:
        raise Invalid("Choose who the announcement is for.")
    return out


def create_announcement(engine: Engine, values: dict[str, Any], *, by: str) -> dict[str, Any]:
    title, body = str(values.get("title") or "").strip(), str(values.get("body") or "").strip()
    if not title or len(title) > 200 or not body or len(body) > 10_000:
        raise Invalid("An announcement needs a title (up to 200 characters) and a body (up to 10,000).")
    category = values.get("category") or "general"
    if category not in CATEGORIES:
        raise Invalid("Unknown category.")
    channels = values.get("channels") or ["in_app"]
    if not isinstance(channels, list) or not channels or any(c not in CHANNELS for c in channels):
        raise Invalid("Channels are in_app, email and webhook.")
    target = _validate_target(engine, values.get("target") or {"all": True})
    scheduled = values.get("scheduled_at")
    if scheduled is not None and (not isinstance(scheduled, (int, float)) or scheduled < now() - 60):
        raise Invalid("The schedule must be in the future.")
    ts = now()
    row = {"id": new_id("ann_"), "title": title, "body": body, "category": category, "channels": dumps(channels),
           "target": dumps(target), "status": "scheduled" if scheduled else "draft", "scheduled_at": scheduled,
           "created_by": by, "created_at": ts, "updated_at": ts}
    engine.db.insert("announcements", row)
    engine.audit(None, by, "announcement.create", row["id"], {"title": title, "channels": channels, "target": target,
                 "scheduled_at": scheduled}, resource_type="announcement", decision="allowed", result="ok")
    return _row(engine.db.one("SELECT * FROM announcements WHERE id = ?", (row["id"],)))


def recipients(engine: Engine, target: dict[str, Any]) -> list[dict[str, Any]]:
    sql = "SELECT DISTINCT u.id, u.email, u.name, u.language FROM users u"
    where = ["u.is_local = 0", "u.disabled = 0"]
    params: list[Any] = []
    if target.get("all"):
        pass
    else:
        ors = []
        if target.get("plan_codes"):
            marks = ",".join("?" for _ in target["plan_codes"])
            ors.append(f"u.id IN (SELECT s.user_id FROM subscriptions s JOIN plans p ON p.id = s.plan_id WHERE p.code IN ({marks}) "
                       "AND s.status IN ('trialing','active','past_due'))")
            params.extend(target["plan_codes"])
            if "free" in target["plan_codes"]:
                ors.append("u.id NOT IN (SELECT user_id FROM subscriptions WHERE status IN ('trialing','active','past_due'))")
        if target.get("new_users_days"):
            ors.append("u.created_at > ?")
            params.append(now() - target["new_users_days"] * 86400)
        if target.get("workspace_ids"):
            marks = ",".join("?" for _ in target["workspace_ids"])
            ors.append(f"u.id IN (SELECT user_id FROM members WHERE workspace_id IN ({marks}))")
            params.extend(target["workspace_ids"])
        if target.get("user_ids"):
            marks = ",".join("?" for _ in target["user_ids"])
            ors.append(f"u.id IN ({marks})")
            params.extend(target["user_ids"])
        if target.get("roles"):
            marks = ",".join("?" for _ in target["roles"])
            ors.append(f"u.id IN (SELECT user_id FROM members WHERE role IN ({marks}))")
            params.extend(target["roles"])
        if ors:
            where.append("(" + " OR ".join(ors) + ")")
    return engine.db.all(f"{sql} WHERE {' AND '.join(where)} ORDER BY u.email", params)


def send_announcement(engine: Engine, announcement_id: str, *, by: str | None) -> dict[str, Any]:
    """Delivers to everyone targeted who has not received it yet on each channel (safe to call again)."""
    from .email import enqueue

    a = engine.db.one("SELECT * FROM announcements WHERE id = ?", (announcement_id,))
    if a is None:
        raise NotFound("Announcement not found.")
    a = _row(a)
    if a["status"] == "canceled":
        raise Invalid("This announcement was cancelled.")
    people = recipients(engine, a["target"])
    delivered = 0
    for p in people:
        prefs = preferences(engine, p["id"])[a["category"]]
        for channel in a["channels"]:
            if channel == "webhook":
                continue
            if channel == "in_app" and not prefs["in_app"]:
                continue
            if channel == "email" and not prefs["email"]:
                continue
            with engine.db.tx() as c:
                fresh = c.execute("INSERT OR IGNORE INTO announcement_deliveries (announcement_id, user_id, channel, created_at) VALUES (?,?,?,?)",
                                  (a["id"], p["id"], channel, now())).rowcount
            if not fresh:
                continue
            if channel == "in_app":
                engine.db.insert("user_notifications", {"id": new_id("un_"), "user_id": p["id"], "category": a["category"], "title": a["title"],
                                                        "body": a["body"][:4000], "link": None, "created_at": now()})
                engine.events.publish(f"user:{p['id']}", {"type": "notification", "title": a["title"], "link": None})
            else:
                enqueue(engine, to=p["email"], template_key="platform_announcement", lang=p.get("language") or "en",
                        vars={"name": p["name"], "title": a["title"], "body": a["body"]}, category=a["category"],
                        dedupe_key=f"ann:{a['id']}:{p['id']}")
            delivered += 1
    if "webhook" in a["channels"]:
        with engine.db.tx() as c:
            fresh = c.execute("INSERT OR IGNORE INTO announcement_deliveries (announcement_id, user_id, channel, created_at) VALUES (?,?,?,?)",
                              (a["id"], "-", "webhook", now())).rowcount
        if fresh:
            webhook_notify(engine, {"event": "announcement.sent", "id": a["id"], "title": a["title"], "body": a["body"],
                                    "category": a["category"], "sent_at": now()})
    total = engine.db.scalar("SELECT COUNT(*) FROM announcement_deliveries WHERE announcement_id = ?", (a["id"],)) or 0
    engine.db.update("announcements", a["id"], {"status": "sent", "sent_at": a.get("sent_at") or now(), "delivered": total, "updated_at": now()})
    engine.audit(None, by, "announcement.send", a["id"], {"recipients": len(people), "delivered_now": delivered},
                 resource_type="announcement", decision="allowed", result="ok")
    return _row(engine.db.one("SELECT * FROM announcements WHERE id = ?", (a["id"],)))


def cancel_announcement(engine: Engine, announcement_id: str, *, by: str) -> None:
    n = engine.db.execute("UPDATE announcements SET status = 'canceled', updated_at = ? WHERE id = ? AND status IN ('draft','scheduled')",
                          (now(), announcement_id))
    if not n:
        raise NotFound("Announcement not found or already sent.")
    engine.audit(None, by, "announcement.cancel", announcement_id, None, resource_type="announcement", decision="allowed", result="ok")


def tick(engine: Engine) -> None:
    """Sends announcements whose time has come (the scheduler calls it)."""
    for a in engine.db.all("SELECT id FROM announcements WHERE status = 'scheduled' AND scheduled_at <= ?", (now(),)):
        try:
            send_announcement(engine, a["id"], by=None)
        except Exception:  # pragma: no cover
            log.exception("scheduled announcement %s", a["id"])


def webhook_notify(engine: Engine, payload: dict[str, Any]) -> None:
    """The optional webhook channel: one signed POST to the owner's endpoint, sent in the background; a failure is
    logged and audited, never raised into the action that caused it."""
    import asyncio

    from ..security import check_outbound_url, hmac_sha256_hex
    from .settings import get_secret

    url = get_setting(engine, "notifications.webhook_url")
    if not url:
        return
    secret = get_secret(engine, "notifications.webhook_secret") or ""
    body = dumps(payload).encode()
    headers = {"content-type": "application/json", "x-desk-event": str(payload.get("event", "")),
               "x-desk-signature": f"sha256={hmac_sha256_hex(secret, body)}" if secret else ""}

    async def go() -> None:
        import httpx2 as httpx
        try:
            check_outbound_url(url, allow_private=engine.settings.allow_private_network_urls)
            async with httpx.AsyncClient(timeout=15, transport=engine.http_transport) as client:
                resp = await client.post(url, content=body, headers={k: v for k, v in headers.items() if v})
            engine.audit(None, None, "notification.webhook", url[:200], {"status": resp.status_code}, resource_type="webhook",
                         decision="allowed", result="ok" if resp.status_code < 400 else "failed")
        except Exception as exc:  # noqa: BLE001
            log.warning("notification webhook failed: %s", exc)
            engine.audit(None, None, "notification.webhook", url[:200], {"error": str(exc)[:200]}, resource_type="webhook",
                         decision="allowed", result="failed")

    try:
        asyncio.get_running_loop().create_task(go())
    except RuntimeError:  # no loop (a synchronous caller outside the server): deliver inline
        asyncio.run(go())


def delivery_history(engine: Engine, announcement_id: str) -> list[dict[str, Any]]:
    return engine.db.all("SELECT d.*, u.email FROM announcement_deliveries d LEFT JOIN users u ON u.id = d.user_id WHERE d.announcement_id = ? "
                         "ORDER BY d.created_at DESC LIMIT 500", (announcement_id,))
