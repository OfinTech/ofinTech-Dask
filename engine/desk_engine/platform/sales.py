"""Contact Sales: enterprise enquiries from the public form, worked as leads in the dashboard."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from ..db import dumps, loads, new_id, now
from ..errors import Invalid, NotFound

if TYPE_CHECKING:
    from ..app import Engine

STATUSES = ("NEW", "CONTACTED", "QUALIFIED", "PROPOSAL", "WON", "LOST")
COMPANY_SIZES = ("1-10", "11-50", "51-200", "201-1000", "1000+")
DEPLOYMENTS = ("hosted", "private", "undecided")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _row(l: dict[str, Any]) -> dict[str, Any]:
    l = dict(l)
    l["features"] = loads(l.get("features"), [])
    return l


def submit(engine: Engine, values: dict[str, Any], *, source: str) -> dict[str, Any]:
    """The public form. Validated strictly; the enquirer gets a confirmation e-mail, the owner an alert."""
    from .email import enqueue
    from .notifications import owner_alert
    from .owner import list_admins
    from .settings import get_setting

    name = str(values.get("name") or "").strip()
    email = str(values.get("email") or "").strip().lower()
    company = str(values.get("company") or "").strip()
    if not name or len(name) > 120 or not company or len(company) > 200 or not EMAIL_RE.match(email) or len(email) > 254:
        raise Invalid("Name, business e-mail and company are required.")
    country = str(values.get("country") or "").strip().upper()[:2] or None
    size = values.get("company_size")
    if size is not None and size not in COMPANY_SIZES:
        raise Invalid("Unknown company size.")
    deployment = values.get("deployment")
    if deployment is not None and deployment not in DEPLOYMENTS:
        raise Invalid("Deployment is hosted, private or undecided.")
    ints: dict[str, int | None] = {}
    for k in ("users_estimate", "workspaces_needed"):
        v = values.get(k)
        if v is not None and (isinstance(v, bool) or not isinstance(v, int) or v < 0 or v > 1_000_000):
            raise Invalid(f"{k} must be a whole number.")
        ints[k] = v
    features = values.get("features") or []
    if not isinstance(features, list) or len(features) > 20 or any(not isinstance(f, str) or len(f) > 60 for f in features):
        raise Invalid("features is a short list of names.")
    message = str(values.get("message") or "").strip()[:4000]
    storage = str(values.get("storage_estimate") or "").strip()[:60] or None
    ts = now()
    row = {"id": new_id("lead_"), "name": name, "email": email, "company": company, "country": country, "company_size": size,
           "users_estimate": ints["users_estimate"], "workspaces_needed": ints["workspaces_needed"], "storage_estimate": storage,
           "features": dumps(features), "deployment": deployment, "message": message or None, "status": "NEW", "source": source[:40],
           "created_at": ts, "updated_at": ts}
    engine.db.insert("sales_leads", row)
    engine.audit(None, None, "sales.lead.create", row["id"], {"company": company, "country": country, "source": source},
                 resource_type="lead", decision="allowed", result="ok")
    lang = "ar" if str(values.get("lang") or "").lower().startswith("ar") else "en"
    enqueue(engine, to=email, template_key="sales_inquiry_confirmation", lang=lang, vars={"name": name}, category="sales",
            dedupe_key=f"lead-confirm:{row['id']}")
    owner_alert(engine, "new_sales_lead", f"New enterprise enquiry from {company} ({name})", category="sales",
                link=f"#/owner/leads/{row['id']}", body=message[:500] if message else None)
    # the owner's sales notification e-mail carries the detail
    for admin in list_admins(engine):
        enqueue(engine, to=admin["email"], template_key="sales_lead_notification", lang="en",
                vars={"name": name, "email": email, "company": company, "country": country or "-", "message": message or "-",
                      "url": f"{engine.settings.public_url}/#/owner/leads/{row['id']}", "platform": get_setting(engine, "platform.name")},
                category="sales", dedupe_key=f"lead-notify:{row['id']}:{admin['user_id']}")
    return {"id": row["id"], "status": "NEW"}


def list_leads(engine: Engine, *, status: str | None = None, q: str | None = None, assigned_to: str | None = None,
               limit: int = 100, offset: int = 0) -> dict[str, Any]:
    where, params = ["1=1"], []
    if status:
        if status not in STATUSES:
            raise Invalid("Unknown status.")
        where.append("status = ?")
        params.append(status)
    if assigned_to:
        where.append("assigned_to = ?")
        params.append(assigned_to)
    if q:
        where.append("(name LIKE ? OR email LIKE ? OR company LIKE ?)")
        like = f"%{q.strip()}%"
        params.extend([like, like, like])
    sql = f"FROM sales_leads WHERE {' AND '.join(where)}"
    total = engine.db.scalar(f"SELECT COUNT(*) {sql}", params) or 0
    rows = engine.db.all(f"SELECT * {sql} ORDER BY created_at DESC LIMIT ? OFFSET ?", [*params, limit, offset])
    return {"total": total, "items": [_row(r) for r in rows]}


def get(engine: Engine, lead_id: str) -> dict[str, Any]:
    l = engine.db.one("SELECT * FROM sales_leads WHERE id = ?", (lead_id,))
    if l is None:
        raise NotFound("Lead not found.")
    out = _row(l)
    out["notes"] = engine.db.all("SELECT n.*, u.name AS author FROM lead_notes n LEFT JOIN users u ON u.id = n.author_id WHERE n.lead_id = ? "
                                 "ORDER BY n.created_at DESC", (lead_id,))
    return out


def update(engine: Engine, lead_id: str, values: dict[str, Any], *, by: str) -> dict[str, Any]:
    get(engine, lead_id)
    clean: dict[str, Any] = {}
    if "status" in values:
        if values["status"] not in STATUSES:
            raise Invalid("Unknown status.")
        clean["status"] = values["status"]
    if "assigned_to" in values:
        a = values["assigned_to"]
        if a is not None and engine.db.one("SELECT 1 FROM platform_admins WHERE user_id = ?", (a,)) is None \
                and engine.db.scalar("SELECT value FROM settings WHERE key = 'instance_admin'") != dumps(a):
            raise Invalid("Leads are assigned to platform administrators.")
        clean["assigned_to"] = a
    if "follow_up_at" in values:
        f = values["follow_up_at"]
        if f is not None and not isinstance(f, (int, float)):
            raise Invalid("follow_up_at is a timestamp.")
        clean["follow_up_at"] = f
    if "converted_user_id" in values:
        u = values["converted_user_id"]
        if u is not None and engine.db.one("SELECT 1 FROM users WHERE id = ?", (u,)) is None:
            raise Invalid("Unknown user.")
        clean["converted_user_id"] = u
    if not clean:
        raise Invalid("Nothing to change.")
    clean["updated_at"] = now()
    engine.db.update("sales_leads", lead_id, clean)
    engine.audit(None, by, "sales.lead.update", lead_id, {k: v for k, v in clean.items() if k != "updated_at"}, resource_type="lead",
                 decision="allowed", result="ok")
    return get(engine, lead_id)


def add_note(engine: Engine, lead_id: str, body: str, *, by: str) -> dict[str, Any]:
    get(engine, lead_id)
    body = (body or "").strip()
    if not body or len(body) > 4000:
        raise Invalid("A note is up to 4,000 characters.")
    engine.db.insert("lead_notes", {"id": new_id("ln_"), "lead_id": lead_id, "author_id": by, "body": body, "created_at": now()})
    engine.db.update("sales_leads", lead_id, {"updated_at": now()})
    return get(engine, lead_id)


def due_followups(engine: Engine) -> list[dict[str, Any]]:
    return [_row(r) for r in engine.db.all("SELECT * FROM sales_leads WHERE follow_up_at IS NOT NULL AND follow_up_at <= ? AND status NOT IN "
                                           "('WON','LOST') ORDER BY follow_up_at", (now(),))]


def remind_followups(engine: Engine) -> None:
    """Scheduler: an in-app reminder to the assignee (or every administrator) when a follow-up falls due."""
    from .notifications import push
    from .owner import list_admins

    for lead in due_followups(engine):
        targets = [lead["assigned_to"]] if lead.get("assigned_to") else [a["user_id"] for a in list_admins(engine)]
        for uid in targets:
            push(engine, uid, category="sales", title=f"Follow up with {lead['company']} ({lead['name']})",
                 link=f"#/owner/leads/{lead['id']}", dedupe=f"#/owner/leads/{lead['id']}")
        engine.db.update("sales_leads", lead["id"], {"follow_up_at": None, "updated_at": now()})


def stats(engine: Engine) -> dict[str, Any]:
    by_status = {r["status"]: r["n"] for r in engine.db.all("SELECT status, COUNT(*) AS n FROM sales_leads GROUP BY status")}
    return {"by_status": {s: by_status.get(s, 0) for s in STATUSES}, "total": sum(by_status.values()),
            "won_rate": round(100 * by_status.get("WON", 0) / sum(by_status.values()), 1) if by_status else None}
