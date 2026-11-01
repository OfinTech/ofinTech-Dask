"""Privacy-preserving website and product analytics.

What is collected
-----------------
* **Website events** (``kind = web``) come from the public collector ``POST /api/analytics/collect``: page views and
  a few funnel events (pricing viewed, checkout started/completed, contact-sales submitted, registration). The
  visitor id is ``sha256(daily salt | client address | user agent)`` truncated — it cannot be reversed and changes
  every day, so no cross-day tracking exists. The user agent is reduced to a device class and a browser family;
  the referrer is reduced to its host; the country comes only from a header the reverse proxy sets (``CF-IPCountry``
  or ``X-Country``) and only when the owner allows it. Nothing is collected without consent when the owner requires
  consent (the default), and nothing when analytics is switched off.
* **Product events** (``kind = platform``) are derived from the engine's own audit actions: sign-ups, sign-ins,
  workspace creation, uploads, assistant runs, reconciliation, analysis, exports, API use. Only the event name, the
  account, the workspace and the time are kept — never prompts, responses, figures, file contents or arguments.

What is never collected: bank credentials, transactions, prompts or answers, documents, accounting records, e-mail
addresses in events, IP addresses (the hash salt is rotated daily and never stored beyond the day).

Every metric the dashboard shows has its definition in ``METRIC_DEFINITIONS`` and is computed here from these tables
and the engine's own records (users, sessions, workspaces, subscriptions, invoices). Absent data is reported as
``null``, not zero.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import secrets
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

from ..db import dumps, now
from ..errors import Invalid
from .settings import get_setting

if TYPE_CHECKING:
    from ..app import Engine

WEB_EVENTS = {"page_view", "pricing_view", "signup_view", "signup_completed", "checkout_started", "checkout_completed",
              "contact_sales_submitted", "login_view"}

# audit action → product event name (feature adoption); anything not listed is not an analytics event
AUDIT_TO_EVENT: dict[str, str] = {
    "user.signup": "signup", "user.login": "login", "workspace.create": "workspace_created",
    "file.upload": "file_uploaded", "document.add": "document_added", "dataset.import": "dataset_imported",
    "agent.run": "ask_finance", "tool.run": "assistant_tool", "close.prepare": "reconciliation_run",
    "close.status": "close_status", "forecast.create": "financial_intelligence", "analysis.kpi": "financial_intelligence",
    "workflow.run": "workflow_run", "workflow.create": "workflow_created", "dashboard.create": "dashboard_created",
    "export.download": "data_export", "file.download": "data_export", "mcp_key.create": "api_access",
    "mcp_server.call": "api_access", "connection.connected": "connection_added", "billing.checkout.started": "checkout_started",
    "billing.checkout.completed": "checkout_completed", "sales.lead.create": "contact_sales_submitted",
}
FEATURE_EVENTS = ("ask_finance", "assistant_tool", "reconciliation_run", "financial_intelligence", "workflow_run",
                  "dashboard_created", "file_uploaded", "document_added", "dataset_imported", "data_export", "api_access",
                  "connection_added")

METRIC_DEFINITIONS: dict[str, str] = {
    "total_users": "Accounts in `users` (excluding the desktop's local account), whatever their state.",
    "new_users": "Accounts whose `created_at` falls in the period.",
    "active_users": "Distinct accounts with a session whose `last_seen` falls in the period (any request that counts as activity).",
    "dau": "Distinct accounts active (session last_seen or product event) on the calendar day (UTC).",
    "wau": "Distinct accounts active in the trailing 7 days.",
    "mau": "Distinct accounts active in the trailing 30 days.",
    "total_workspaces": "Rows in `workspaces`.",
    "free_workspaces": "Workspaces whose owning account has no active paid subscription.",
    "paid_workspaces": "Workspaces whose owning account has an active or trialing paid subscription.",
    "active_subscriptions": "Subscriptions with status active (past_due within grace counts; trials do not).",
    "trial_subscriptions": "Subscriptions with status trialing.",
    "cancelled_subscriptions": "Subscriptions cancelled in the period (`canceled_at`).",
    "mrr": "Sum over active paid subscriptions of monthly price, or annual price / 12, per currency, in minor units.",
    "arr": "MRR × 12.",
    "failed_payments": "Invoices with status payment_failed in the period.",
    "conversion_rate": "Accounts created in the period that have ever had a paid subscription ÷ accounts created in the period.",
    "churn_rate": "Paid subscriptions cancelled in the period ÷ paid subscriptions active at the start of the period.",
    "storage_bytes": "Sum of document sizes and stored file outputs across all workspaces.",
    "security_alerts": "Audit entries with decision = denied in the period (refusals, failed sign-ins, expired sessions).",
    "pending_leads": "Sales leads with status NEW.",
    "page_views": "Web events named page_view in the period (consented visitors only).",
    "unique_visitors": "Distinct daily visitor hashes in the period (a visitor seen on two days counts twice by design).",
    "sessions_web": "Distinct (visitor, hour) pairs — an approximation, since no cross-request cookie is set.",
    "feature_usage": "Product events per feature name in the period (see AUDIT_TO_EVENT).",
    "email_delivery": "E-mail jobs by status per day.",
}

PERIODS = {"today": 0, "yesterday": 1, "7d": 7, "30d": 30, "90d": 90}

_SALT: dict[str, str] = {}


def _daily_salt() -> str:
    day = dt.datetime.now(dt.UTC).strftime("%Y-%m-%d")
    if day not in _SALT:
        _SALT.clear()
        _SALT[day] = secrets.token_hex(16)
    return _SALT[day]


def visitor_hash(client_host: str, user_agent: str) -> str:
    return hashlib.sha256(f"{_daily_salt()}|{client_host}|{user_agent}".encode()).hexdigest()[:24]


def classify_agent(ua: str) -> tuple[str, str]:
    ua_l = (ua or "").lower()
    if "bot" in ua_l or "crawler" in ua_l or "spider" in ua_l:
        return "bot", "bot"
    device = "mobile" if ("mobile" in ua_l or "android" in ua_l or "iphone" in ua_l) else "tablet" if "ipad" in ua_l or "tablet" in ua_l else "desktop"
    if "edg/" in ua_l:
        browser = "edge"
    elif "chrome/" in ua_l or "crios" in ua_l:
        browser = "chrome"
    elif "firefox/" in ua_l:
        browser = "firefox"
    elif "safari/" in ua_l:
        browser = "safari"
    elif "electron" in ua_l:
        browser = "desktop_app"
    else:
        browser = "other"
    return device, browser


def referrer_host(ref: str | None) -> str | None:
    if not ref:
        return None
    try:
        host = urlsplit(ref).hostname
    except ValueError:
        return None
    return (host or "")[:100].lower() or None


def traffic_source(ref_host: str | None, own_hosts: set[str]) -> str:
    if not ref_host:
        return "direct"
    if ref_host in own_hosts:
        return "internal"
    if any(s in ref_host for s in ("google.", "bing.", "duckduckgo.", "yahoo.", "yandex.", "baidu.")):
        return "search"
    if any(s in ref_host for s in ("twitter.", "x.com", "linkedin.", "facebook.", "instagram.", "t.co", "reddit.", "youtube.")):
        return "social"
    return "referral"


def collect_web(engine: Engine, *, name: str, path: str | None, referrer: str | None, lang: str | None, consent: bool,
                client_host: str, user_agent: str, country_header: str | None, props: dict[str, Any] | None = None) -> bool:
    """Stores one website event if policy allows. Returns whether it was stored."""
    if not get_setting(engine, "analytics.enabled"):
        return False
    if get_setting(engine, "analytics.require_consent") and not consent:
        return False
    if name not in WEB_EVENTS:
        raise Invalid("Unknown event.")
    device, browser = classify_agent(user_agent)
    if device == "bot":
        return False
    ref_host = referrer_host(referrer)
    own = {urlsplit(engine.settings.public_url).hostname or "", urlsplit(engine.settings.site_url or "").hostname or ""} - {""}
    country = (country_header or "").strip().upper()[:2] if get_setting(engine, "analytics.collect_country") else None
    clean_props = None
    if props and isinstance(props, dict):
        clean_props = {k: v for k, v in list(props.items())[:10] if isinstance(k, str) and isinstance(v, (str, int, float, bool))
                       and k in {"plan", "interval", "variant", "screen"}}
    engine.db.insert("analytics_events", {"kind": "web", "name": name, "visitor": visitor_hash(client_host, user_agent),
                                          "path": (path or "")[:200] or None, "referrer": ref_host,
                                          "source": traffic_source(ref_host, own), "device": device, "browser": browser,
                                          "country": country or None, "lang": (lang or "")[:5] or None,
                                          "props": dumps(clean_props) if clean_props else None, "created_at": now()})
    return True


def on_audit(engine: Engine, action: str, user_id: str | None, workspace_id: str | None) -> None:
    """Mirrors a product event from an audit action: name, account, workspace, time — and nothing else."""
    name = AUDIT_TO_EVENT.get(action)
    if name is None or not get_setting(engine, "analytics.enabled"):
        return
    try:
        engine.db.insert("analytics_events", {"kind": "platform", "name": name, "user_id": user_id, "workspace_id": workspace_id,
                                              "created_at": now()})
    except Exception:  # pragma: no cover - analytics must never break the audited action
        pass


def purge(engine: Engine) -> int:
    days = int(get_setting(engine, "analytics.retention_days") or 400)
    return engine.db.execute("DELETE FROM analytics_events WHERE created_at < ?", (now() - days * 86400,))


# --- ranges -----------------------------------------------------------------------------------------------------------

def period_range(period: str | None, start: float | None = None, end: float | None = None) -> tuple[float, float, float, float]:
    """(from, to, compare_from, compare_to) as timestamps; the comparison period is the one just before, same length."""
    t = now()
    today = dt.datetime.now(dt.UTC).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    if start is not None and end is not None:
        if end <= start or end - start > 400 * 86400:
            raise Invalid("The date range must be positive and at most 400 days.")
        a, b = float(start), float(end)
    elif period == "today":
        a, b = today, t
    elif period == "yesterday":
        a, b = today - 86400, today
    else:
        days = PERIODS.get(period or "30d", 30)
        a, b = t - days * 86400, t
    length = b - a
    return a, b, a - length, a


def _count(engine: Engine, sql: str, params: tuple[Any, ...] = ()) -> int:
    return int(engine.db.scalar(sql, params) or 0)


def kpis(engine: Engine, period: str | None = None, start: float | None = None, end: float | None = None) -> dict[str, Any]:
    from .billing import mrr as mrr_fn
    from .storage import platform_totals

    a, b, ca, cb = period_range(period, start, end)
    t = now()
    today = dt.datetime.now(dt.UTC).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    users_total = _count(engine, "SELECT COUNT(*) FROM users WHERE is_local = 0")

    def new_users(x: float, y: float) -> int:
        return _count(engine, "SELECT COUNT(*) FROM users WHERE is_local = 0 AND created_at >= ? AND created_at < ?", (x, y))

    def active(x: float, y: float) -> int:
        return _count(engine, "SELECT COUNT(*) FROM (SELECT user_id FROM sessions WHERE last_seen >= ? AND last_seen < ? UNION "
                      "SELECT user_id FROM analytics_events WHERE kind = 'platform' AND user_id IS NOT NULL AND created_at >= ? AND created_at < ?)",
                      (x, y, x, y))

    paid_owner_sql = ("SELECT DISTINCT s.user_id FROM subscriptions s JOIN plans p ON p.id = s.plan_id WHERE s.status IN ('active','trialing','past_due') "
                      "AND (s.grace_until IS NULL OR s.grace_until > ?) AND (p.monthly_price > 0 OR p.annual_price > 0)")
    paid_ws = _count(engine, f"SELECT COUNT(DISTINCT m.workspace_id) FROM members m WHERE m.role = 'owner' AND m.user_id IN ({paid_owner_sql})", (t,))
    total_ws = _count(engine, "SELECT COUNT(*) FROM workspaces")
    cancelled = _count(engine, "SELECT COUNT(*) FROM subscriptions WHERE canceled_at >= ? AND canceled_at < ?", (a, b))
    paid_at_start = _count(engine, "SELECT COUNT(*) FROM subscriptions s JOIN plans p ON p.id = s.plan_id WHERE s.created_at < ? AND "
                           "(s.canceled_at IS NULL OR s.canceled_at >= ?) AND (p.monthly_price > 0 OR p.annual_price > 0)", (a, a))
    new_in_period = new_users(a, b)
    converted = _count(engine, "SELECT COUNT(DISTINCT u.id) FROM users u JOIN subscriptions s ON s.user_id = u.id JOIN plans p ON p.id = s.plan_id "
                       "WHERE u.created_at >= ? AND u.created_at < ? AND (p.monthly_price > 0 OR p.annual_price > 0)", (a, b))
    revenue = mrr_fn(engine)
    storage = platform_totals(engine)
    out = {
        "period": {"from": a, "to": b, "compare_from": ca, "compare_to": cb},
        "total_users": users_total,
        "new_users_today": new_users(today, t), "new_users_week": new_users(t - 7 * 86400, t), "new_users_month": new_users(t - 30 * 86400, t),
        "new_users": {"value": new_in_period, "previous": new_users(ca, cb)},
        "active_users": {"value": active(a, b), "previous": active(ca, cb)},
        "dau": active(today, t), "wau": active(t - 7 * 86400, t), "mau": active(t - 30 * 86400, t),
        "total_workspaces": total_ws, "paid_workspaces": paid_ws, "free_workspaces": total_ws - paid_ws,
        "active_subscriptions": _count(engine, "SELECT COUNT(*) FROM subscriptions WHERE status = 'active' OR (status = 'past_due' AND "
                                       "(grace_until IS NULL OR grace_until > ?))", (t,)),
        "trial_subscriptions": _count(engine, "SELECT COUNT(*) FROM subscriptions WHERE status = 'trialing'"),
        "cancelled_subscriptions": {"value": cancelled, "previous": _count(engine, "SELECT COUNT(*) FROM subscriptions WHERE canceled_at >= ? AND canceled_at < ?", (ca, cb))},
        "mrr": revenue["by_currency"], "arr": revenue["arr_by_currency"],
        "failed_payments": {"value": _count(engine, "SELECT COUNT(*) FROM invoices WHERE status = 'payment_failed' AND updated_at >= ? AND updated_at < ?", (a, b))},
        "conversion_rate": round(100 * converted / new_in_period, 1) if new_in_period else None,
        "churn_rate": round(100 * cancelled / paid_at_start, 1) if paid_at_start else None,
        "storage_bytes": storage["total"],
        "security_alerts": {"value": _count(engine, "SELECT COUNT(*) FROM audit_log WHERE decision = 'denied' AND created_at >= ? AND created_at < ?", (a, b)),
                            "previous": _count(engine, "SELECT COUNT(*) FROM audit_log WHERE decision = 'denied' AND created_at >= ? AND created_at < ?", (ca, cb))},
        "pending_leads": _count(engine, "SELECT COUNT(*) FROM sales_leads WHERE status = 'NEW'"),
        "page_views": {"value": _count(engine, "SELECT COUNT(*) FROM analytics_events WHERE kind = 'web' AND name = 'page_view' AND created_at >= ? AND created_at < ?", (a, b)),
                       "previous": _count(engine, "SELECT COUNT(*) FROM analytics_events WHERE kind = 'web' AND name = 'page_view' AND created_at >= ? AND created_at < ?", (ca, cb))},
        "unique_visitors": {"value": _count(engine, "SELECT COUNT(DISTINCT visitor) FROM analytics_events WHERE kind = 'web' AND created_at >= ? AND created_at < ?", (a, b))},
        "analytics_enabled": bool(get_setting(engine, "analytics.enabled")),
        "hosted": engine.settings.hosted,
    }
    return out


def _bucket_sql(bucket: str) -> str:
    return {"day": "date(created_at, 'unixepoch')", "week": "strftime('%Y-W%W', created_at, 'unixepoch')",
            "month": "strftime('%Y-%m', created_at, 'unixepoch')"}.get(bucket, "date(created_at, 'unixepoch')")


def series(engine: Engine, metric: str, period: str | None = None, start: float | None = None, end: float | None = None,
           bucket: str = "day") -> dict[str, Any]:
    """Time series for the charts. Each point: {bucket, value} (or {bucket, name, value} for grouped metrics)."""
    a, b, _, _ = period_range(period, start, end)
    g = _bucket_sql(bucket)
    if metric == "user_growth":
        rows = engine.db.all(f"SELECT {g} AS bucket, COUNT(*) AS value FROM users WHERE is_local = 0 AND created_at >= ? AND created_at < ? GROUP BY bucket ORDER BY bucket", (a, b))
        base = _count(engine, "SELECT COUNT(*) FROM users WHERE is_local = 0 AND created_at < ?", (a,))
        running = base
        for r in rows:
            running += r["value"]
            r["cumulative"] = running
        return {"metric": metric, "points": rows}
    if metric == "registrations":
        return {"metric": metric, "points": engine.db.all(f"SELECT {g} AS bucket, COUNT(*) AS value FROM users WHERE is_local = 0 AND created_at >= ? AND created_at < ? GROUP BY bucket ORDER BY bucket", (a, b))}
    if metric == "active_users":
        return {"metric": metric, "points": engine.db.all(
            f"SELECT bucket, COUNT(DISTINCT user_id) AS value FROM (SELECT {g.replace('created_at', 'last_seen')} AS bucket, user_id FROM sessions WHERE last_seen >= ? AND last_seen < ? "
            f"UNION ALL SELECT {g} AS bucket, user_id FROM analytics_events WHERE kind = 'platform' AND user_id IS NOT NULL AND created_at >= ? AND created_at < ?) GROUP BY bucket ORDER BY bucket",
            (a, b, a, b))}
    if metric == "revenue":
        return {"metric": metric, "points": engine.db.all(
            f"SELECT {g.replace('created_at', 'paid_at')} AS bucket, currency AS name, SUM(amount_paid) AS value FROM invoices WHERE paid_at >= ? AND paid_at < ? AND status != 'payment_failed' GROUP BY bucket, currency ORDER BY bucket", (a, b))}
    if metric == "funnel":
        steps = ["page_view", "pricing_view", "signup_view", "signup_completed", "checkout_started", "checkout_completed"]
        points = []
        for s in steps:
            web = _count(engine, "SELECT COUNT(DISTINCT visitor) FROM analytics_events WHERE kind = 'web' AND name = ? AND created_at >= ? AND created_at < ?", (s, a, b))
            platform_n = _count(engine, "SELECT COUNT(*) FROM analytics_events WHERE kind = 'platform' AND name = ? AND created_at >= ? AND created_at < ?",
                                ({"signup_completed": "signup", "checkout_started": "checkout_started", "checkout_completed": "checkout_completed"}.get(s, "-"), a, b))
            points.append({"name": s, "value": max(web, platform_n)})
        return {"metric": metric, "points": points}
    if metric == "free_to_paid":
        return {"metric": metric, "points": engine.db.all(
            f"SELECT {g} AS bucket, COUNT(*) AS value FROM subscriptions s JOIN plans p ON p.id = s.plan_id WHERE s.created_at >= ? AND s.created_at < ? "
            "AND (p.monthly_price > 0 OR p.annual_price > 0) GROUP BY bucket ORDER BY bucket", (a, b))}
    if metric == "churn":
        return {"metric": metric, "points": engine.db.all(f"SELECT {g.replace('created_at', 'canceled_at')} AS bucket, COUNT(*) AS value FROM subscriptions WHERE canceled_at >= ? AND canceled_at < ? GROUP BY bucket ORDER BY bucket", (a, b))}
    if metric == "traffic":
        return {"metric": metric, "points": engine.db.all(f"SELECT {g} AS bucket, COUNT(*) AS value, COUNT(DISTINCT visitor) AS visitors FROM analytics_events WHERE kind = 'web' AND name = 'page_view' AND created_at >= ? AND created_at < ? GROUP BY bucket ORDER BY bucket", (a, b))}
    if metric in ("sources", "referrers", "landing_pages", "devices", "browsers", "countries", "languages"):
        col = {"sources": "source", "referrers": "referrer", "landing_pages": "path", "devices": "device", "browsers": "browser",
               "countries": "country", "languages": "lang"}[metric]
        return {"metric": metric, "points": engine.db.all(f"SELECT {col} AS name, COUNT(*) AS value FROM analytics_events WHERE kind = 'web' AND name = 'page_view' AND created_at >= ? AND created_at < ? AND {col} IS NOT NULL GROUP BY {col} ORDER BY value DESC LIMIT 20", (a, b))}
    if metric == "features":
        marks = ",".join("?" for _ in FEATURE_EVENTS)
        return {"metric": metric, "points": engine.db.all(f"SELECT name, COUNT(*) AS value, COUNT(DISTINCT workspace_id) AS workspaces FROM analytics_events WHERE kind = 'platform' AND name IN ({marks}) AND created_at >= ? AND created_at < ? GROUP BY name ORDER BY value DESC", (*FEATURE_EVENTS, a, b))}
    if metric == "feature_timeline":
        marks = ",".join("?" for _ in FEATURE_EVENTS)
        return {"metric": metric, "points": engine.db.all(f"SELECT {g} AS bucket, name, COUNT(*) AS value FROM analytics_events WHERE kind = 'platform' AND name IN ({marks}) AND created_at >= ? AND created_at < ? GROUP BY bucket, name ORDER BY bucket", (*FEATURE_EVENTS, a, b))}
    if metric == "storage":
        from .storage import per_workspace
        rows = per_workspace(engine, 20)
        return {"metric": metric, "points": [{"name": r["name"], "value": r["total"], "quota": r["quota"]} for r in rows]}
    if metric == "email":
        from .email import daily_stats
        return {"metric": metric, "points": daily_stats(engine, max(1, int((b - a) // 86400) or 1))}
    if metric == "security":
        return {"metric": metric, "points": engine.db.all(f"SELECT {g} AS bucket, action AS name, COUNT(*) AS value FROM audit_log WHERE decision = 'denied' AND created_at >= ? AND created_at < ? GROUP BY bucket, action ORDER BY bucket", (a, b))}
    if metric == "workspaces_created":
        return {"metric": metric, "points": engine.db.all(f"SELECT {g} AS bucket, COUNT(*) AS value FROM workspaces WHERE created_at >= ? AND created_at < ? GROUP BY bucket ORDER BY bucket", (a, b))}
    if metric == "plan_segments":
        return {"metric": metric, "points": engine.db.all(
            "SELECT p.name, COUNT(DISTINCT s.user_id) AS value FROM subscriptions s JOIN plans p ON p.id = s.plan_id WHERE s.status IN ('active','trialing','past_due') GROUP BY p.name "
            "UNION ALL SELECT 'Free (no subscription)', COUNT(*) FROM users WHERE is_local = 0 AND id NOT IN (SELECT user_id FROM subscriptions WHERE status IN ('active','trialing','past_due'))")}
    if metric == "retention":
        return {"metric": metric, "points": cohort_retention(engine, a, b)}
    if metric == "errors":
        return {"metric": metric, "points": engine.db.all(f"SELECT {g} AS bucket, COUNT(*) AS value FROM audit_log WHERE result = 'failed' AND created_at >= ? AND created_at < ? GROUP BY bucket ORDER BY bucket", (a, b))}
    if metric == "ai_usage":
        return {"metric": metric, "points": engine.db.all(f"SELECT {g} AS bucket, SUM(input_tokens + output_tokens) AS value, COUNT(DISTINCT workspace_id) AS workspaces FROM usage WHERE created_at >= ? AND created_at < ? GROUP BY bucket ORDER BY bucket", (a, b))}
    raise Invalid("Unknown metric.")


def cohort_retention(engine: Engine, a: float, b: float) -> list[dict[str, Any]]:
    """Weekly sign-up cohorts and the share still active 1, 2, 4 and 8 weeks later."""
    cohorts = engine.db.all("SELECT strftime('%Y-W%W', created_at, 'unixepoch') AS cohort, MIN(created_at) AS start, COUNT(*) AS size, "
                            "GROUP_CONCAT(id) AS ids FROM users WHERE is_local = 0 AND created_at >= ? AND created_at < ? GROUP BY cohort ORDER BY cohort", (a, b))
    out = []
    for c in cohorts:
        ids = c["ids"].split(",")
        marks = ",".join("?" for _ in ids)
        row: dict[str, Any] = {"cohort": c["cohort"], "size": c["size"]}
        for weeks in (1, 2, 4, 8):
            x = c["start"] + weeks * 7 * 86400
            if x > now():
                row[f"w{weeks}"] = None
                continue
            active = _count(engine, f"SELECT COUNT(DISTINCT user_id) FROM sessions WHERE user_id IN ({marks}) AND last_seen >= ? AND last_seen < ?",
                            (*ids, x, x + 7 * 86400))
            row[f"w{weeks}"] = round(100 * active / c["size"], 1) if c["size"] else None
        out.append(row)
    return out


def export_csv(engine: Engine, report: str, period: str | None = None, start: float | None = None, end: float | None = None) -> str:
    import csv
    import io

    buf = io.StringIO()
    w = csv.writer(buf)
    if report == "kpis":
        data = kpis(engine, period, start, end)
        w.writerow(["metric", "value", "previous", "definition"])
        for k, v in data.items():
            if k in ("period", "analytics_enabled", "hosted"):
                continue
            if isinstance(v, dict) and "value" in v:
                w.writerow([k, v["value"], v.get("previous", ""), METRIC_DEFINITIONS.get(k, "")])
            else:
                w.writerow([k, dumps(v) if isinstance(v, dict) else v, "", METRIC_DEFINITIONS.get(k, "")])
    elif report in ("users",):
        w.writerow(["id", "email", "name", "created_at", "signup_method", "disabled", "email_verified", "mfa"])
        for u in engine.db.all("SELECT id, email, name, created_at, signup_method, disabled, email_verified_at, mfa_enabled_at FROM users WHERE is_local = 0 ORDER BY created_at"):
            w.writerow([u["id"], u["email"], u["name"], _iso(u["created_at"]), u["signup_method"] or "", u["disabled"],
                        bool(u["email_verified_at"]), bool(u["mfa_enabled_at"])])
    elif report == "subscriptions":
        w.writerow(["id", "user_email", "plan", "status", "interval", "source", "period_end", "created_at"])
        for s in engine.db.all("SELECT s.*, u.email, p.code FROM subscriptions s JOIN users u ON u.id = s.user_id JOIN plans p ON p.id = s.plan_id ORDER BY s.created_at"):
            w.writerow([s["id"], s["email"], s["code"], s["status"], s["interval"], s["source"], _iso(s["current_period_end"]), _iso(s["created_at"])])
    elif report == "leads":
        w.writerow(["id", "name", "email", "company", "country", "company_size", "users", "workspaces", "storage", "deployment", "status", "created_at"])
        for l in engine.db.all("SELECT * FROM sales_leads ORDER BY created_at"):
            w.writerow([l["id"], l["name"], l["email"], l["company"], l["country"], l["company_size"], l["users_estimate"], l["workspaces_needed"],
                        l["storage_estimate"], l["deployment"], l["status"], _iso(l["created_at"])])
    elif report == "coupons":
        w.writerow(["code", "percent_off", "amount_off", "currency", "duration", "redemptions", "active", "expires_at"])
        for c in engine.db.all("SELECT * FROM coupons ORDER BY created_at"):
            w.writerow([c["code"], c["percent_off"], c["amount_off"], c["currency"], c["duration"], c["redemptions"], c["active"], _iso(c["expires_at"])])
    elif report in ("traffic", "features", "revenue", "registrations", "active_users", "churn", "sources", "devices", "countries", "storage", "email", "security"):
        s = series(engine, report, period, start, end)
        keys = sorted({k for p in s["points"] for k in p})
        w.writerow(keys)
        for p in s["points"]:
            w.writerow([p.get(k, "") for k in keys])
    else:
        raise Invalid("Unknown report.")
    return buf.getvalue()


def _iso(ts: float | None) -> str:
    return dt.datetime.fromtimestamp(ts, dt.UTC).isoformat(timespec="seconds") if ts else ""
