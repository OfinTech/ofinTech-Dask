"""E-mail: provider settings saved by the owner, editable bilingual templates, a durable queue with retries.

The SMTP settings saved in the dashboard take precedence over the environment (``DESK_SMTP_*``); the password lives
in the encrypted secrets table and is never returned or logged. Every message goes through ``email_jobs``: queued,
sent by the scheduler with exponential back-off, parked as ``failed`` after ``email.max_attempts`` tries (the owner
sees why and can retry), skipped for a suppressed address. ``dedupe_key`` makes an event's e-mail single-send even
when the event is delivered twice. Templates use ``{name}``-style placeholders; an unknown placeholder renders empty
rather than failing a send.
"""

from __future__ import annotations

import logging
import string
from typing import TYPE_CHECKING, Any

from ..db import new_id, now
from ..errors import Invalid, NotFound
from ..mail import Mailer
from .settings import get_secret, get_setting, secret_status

if TYPE_CHECKING:
    from ..app import Engine

log = logging.getLogger("desk.email")

TEMPLATE_KEYS = ("welcome", "email_verification", "password_reset", "new_login", "suspicious_login",
                 "subscription_confirmation", "payment_success", "payment_failure", "subscription_cancellation",
                 "trial_expiration", "storage_quota_warning", "sales_inquiry_confirmation", "platform_announcement",
                 "sales_lead_notification", "owner_alert")
LANGS = ("en", "ar")

DEFAULT_TEMPLATES: dict[str, dict[str, tuple[str, str]]] = {
    "welcome": {
        "en": ("Welcome to {platform}", "Hello {name},\n\nYour {platform} account is ready. Sign in at {url} to set up your "
               "first workspace.\n\nIf you did not create this account, ignore this message.\n\n{platform}"),
        "ar": ("مرحباً بك في {platform}", "مرحباً {name}،\n\nحسابك في {platform} جاهز. سجّل الدخول من {url} لإعداد مساحة العمل "
               "الأولى.\n\nإذا لم تنشئ هذا الحساب فتجاهل هذه الرسالة.\n\n{platform}"),
    },
    "email_verification": {
        "en": ("Confirm your e-mail address", "Hello {name},\n\nConfirm your e-mail address for {platform} by opening this "
               "link within 24 hours:\n\n{link}\n\nIf you did not sign up, ignore this message."),
        "ar": ("أكّد عنوان بريدك الإلكتروني", "مرحباً {name}،\n\nأكّد عنوان بريدك الإلكتروني في {platform} بفتح هذا الرابط خلال "
               "24 ساعة:\n\n{link}\n\nإذا لم تسجّل فتجاهل هذه الرسالة."),
    },
    "password_reset": {
        "en": ("Reset your {platform} password", "Open this link within one hour to choose a new password:\n\n{link}\n\n"
               "If you did not ask for this, ignore this message; your password is unchanged."),
        "ar": ("إعادة تعيين كلمة مرور {platform}", "افتح هذا الرابط خلال ساعة لاختيار كلمة مرور جديدة:\n\n{link}\n\nإذا لم تطلب ذلك "
               "فتجاهل هذه الرسالة؛ كلمة مرورك لم تتغير."),
    },
    "new_login": {
        "en": ("New sign-in to {platform}", "Hello {name},\n\nYour account was signed in to on {when} from {device}.\n\nIf this "
               "was you, nothing to do. If not, change your password and sign out of other devices from Settings."),
        "ar": ("تسجيل دخول جديد إلى {platform}", "مرحباً {name}،\n\nتم تسجيل الدخول إلى حسابك في {when} من {device}.\n\nإذا كان هذا "
               "أنت فلا شيء مطلوب. وإلا فغيّر كلمة المرور وسجّل الخروج من الأجهزة الأخرى من الإعدادات."),
    },
    "suspicious_login": {
        "en": ("Unusual sign-in activity on your {platform} account", "Hello {name},\n\nThere were {count} failed sign-in attempts "
               "on your account. If this was not you, change your password now from {url}."),
        "ar": ("نشاط تسجيل دخول غير معتاد في حسابك على {platform}", "مرحباً {name}،\n\nحدثت {count} محاولات تسجيل دخول فاشلة على "
               "حسابك. إذا لم تكن أنت فغيّر كلمة المرور الآن من {url}."),
    },
    "subscription_confirmation": {
        "en": ("Your {platform} plan: {plan}", "Hello {name},\n\nYour account is now on the {plan} plan. You can manage billing "
               "from Settings → Plan.\n\nThank you for choosing {platform}."),
        "ar": ("خطتك في {platform}: {plan}", "مرحباً {name}،\n\nحسابك الآن على خطة {plan}. يمكنك إدارة الفوترة من الإعدادات ← الخطة."
               "\n\nشكراً لاختيارك {platform}."),
    },
    "payment_success": {
        "en": ("Payment received — {amount}", "Hello {name},\n\nWe received your payment of {amount}. Your invoice: {invoice_url}"),
        "ar": ("تم استلام الدفعة — {amount}", "مرحباً {name}،\n\nاستلمنا دفعتك بمبلغ {amount}. فاتورتك: {invoice_url}"),
    },
    "payment_failure": {
        "en": ("Payment failed — action needed", "Hello {name},\n\nYour payment of {amount} did not go through. Update your "
               "payment method from Settings → Plan → Manage billing to keep your plan.\n\n{invoice_url}"),
        "ar": ("فشلت عملية الدفع — مطلوب إجراء", "مرحباً {name}،\n\nلم تتم عملية الدفع بمبلغ {amount}. حدّث وسيلة الدفع من "
               "الإعدادات ← الخطة ← إدارة الفوترة للاحتفاظ بخطتك.\n\n{invoice_url}"),
    },
    "subscription_cancellation": {
        "en": ("Your {plan} subscription was cancelled", "Hello {name},\n\nYour {plan} subscription has been cancelled. Your "
               "workspaces and files stay; those beyond the Free plan become read-only. You can subscribe again at any time."),
        "ar": ("تم إلغاء اشتراكك في {plan}", "مرحباً {name}،\n\nتم إلغاء اشتراكك في {plan}. تبقى مساحات العمل والملفات؛ وما يتجاوز "
               "الخطة المجانية يصبح للقراءة فقط. يمكنك الاشتراك مجدداً في أي وقت."),
    },
    "trial_expiration": {
        "en": ("Your {plan} trial ends soon", "Hello {name},\n\nYour {plan} trial ends in a few days. Add a payment method from "
               "Settings → Plan to keep the features you are using."),
        "ar": ("تنتهي فترتك التجريبية في {plan} قريباً", "مرحباً {name}،\n\nتنتهي فترتك التجريبية في {plan} خلال أيام. أضف وسيلة دفع "
               "من الإعدادات ← الخطة للاحتفاظ بالميزات التي تستخدمها."),
    },
    "storage_quota_warning": {
        "en": ("Storage almost full in {workspace}", "Hello {name},\n\n{workspace} has used {percent}% of its storage. Delete "
               "files you no longer need or upgrade the plan."),
        "ar": ("مساحة التخزين تكاد تمتلئ في {workspace}", "مرحباً {name}،\n\nاستخدمت {workspace} {percent}% من مساحة التخزين. "
               "احذف الملفات التي لم تعد بحاجة إليها أو قم بترقية الخطة."),
    },
    "sales_inquiry_confirmation": {
        "en": ("We received your enquiry", "Hello {name},\n\nThank you for your interest in {platform} Enterprise. We will be "
               "in touch shortly.\n\n{platform}"),
        "ar": ("استلمنا استفسارك", "مرحباً {name}،\n\nشكراً لاهتمامك بنسخة المؤسسات من {platform}. سنتواصل معك قريباً.\n\n{platform}"),
    },
    "platform_announcement": {
        "en": ("{title}", "{body}\n\n— {platform}"),
        "ar": ("{title}", "{body}\n\n— {platform}"),
    },
    "sales_lead_notification": {
        "en": ("New sales enquiry: {company}", "{name} <{email}> from {company} ({country}) asked about Enterprise.\n\n{message}\n\n"
               "Open the lead: {url}"),
        "ar": ("استفسار مبيعات جديد: {company}", "{name} <{email}> من {company} ({country}) استفسر عن نسخة المؤسسات.\n\n{message}\n\n"
               "افتح العميل المحتمل: {url}"),
    },
    "owner_alert": {
        "en": ("[{platform}] {title}", "{title}\n\n{body}\n\n{url}"),
        "ar": ("[{platform}] {title}", "{title}\n\n{body}\n\n{url}"),
    },
}


class _Safe(dict):
    def __missing__(self, key: str) -> str:
        return ""


def render(template: str, vars: dict[str, Any]) -> str:
    try:
        return string.Formatter().vformat(template, (), _Safe({k: ("" if v is None else v) for k, v in vars.items()}))
    except (ValueError, IndexError):
        return template


def get_template(engine: Engine, key: str, lang: str) -> dict[str, Any]:
    if key not in TEMPLATE_KEYS:
        raise NotFound("Unknown template.")
    lang = lang if lang in LANGS else "en"
    row = engine.db.one("SELECT * FROM email_templates WHERE key = ? AND lang = ?", (key, lang))
    if row:
        return {**row, "custom": True}
    subject, body = DEFAULT_TEMPLATES[key][lang]
    return {"key": key, "lang": lang, "subject": subject, "body": body, "custom": False}


def list_templates(engine: Engine) -> list[dict[str, Any]]:
    return [get_template(engine, k, lang) for k in TEMPLATE_KEYS for lang in LANGS]


def save_template(engine: Engine, key: str, lang: str, *, subject: str, body: str, by: str) -> dict[str, Any]:
    if key not in TEMPLATE_KEYS or lang not in LANGS:
        raise NotFound("Unknown template.")
    if not subject.strip() or len(subject) > 300 or not body.strip() or len(body) > 20_000:
        raise Invalid("A template needs a subject (up to 300 characters) and a body (up to 20,000).")
    with engine.db.tx() as c:
        c.execute("INSERT INTO email_templates (key, lang, subject, body, updated_by, updated_at) VALUES (?,?,?,?,?,?) ON CONFLICT(key, lang) "
                  "DO UPDATE SET subject = excluded.subject, body = excluded.body, updated_by = excluded.updated_by, updated_at = excluded.updated_at",
                  (key, lang, subject.strip(), body, by, now()))
    engine.audit(None, by, "email.template.update", f"{key}/{lang}", None, resource_type="email_template", decision="allowed", result="ok")
    return get_template(engine, key, lang)


def reset_template(engine: Engine, key: str, lang: str, *, by: str) -> dict[str, Any]:
    engine.db.execute("DELETE FROM email_templates WHERE key = ? AND lang = ?", (key, lang))
    engine.audit(None, by, "email.template.reset", f"{key}/{lang}", None, resource_type="email_template", decision="allowed", result="ok")
    return get_template(engine, key, lang)


def preview(engine: Engine, key: str, lang: str, vars: dict[str, Any] | None = None) -> dict[str, str]:
    t = get_template(engine, key, lang)
    sample = {"platform": get_setting(engine, "platform.name"), "name": "Olivia", "url": engine.settings.public_url,
              "link": f"{engine.settings.public_url}/#/example", "plan": "Pro", "amount": "29.00 USD", "invoice_url": "",
              "when": "today", "device": "Chrome on Windows", "count": 5, "workspace": "Acme Ltd", "percent": 92,
              "title": "Scheduled maintenance", "body": "The service will be unavailable for 20 minutes on Sunday.",
              "company": "Acme Holdings", "email": "cfo@acme.example", "country": "SA", "message": "We need 40 seats."}
    sample.update(vars or {})
    return {"subject": render(t["subject"], sample), "body": render(t["body"], sample)}


# --- provider ----------------------------------------------------------------------------------------------------------

def mailer(engine: Engine) -> Mailer:
    """The owner's SMTP settings when saved, else the environment's."""
    host = get_setting(engine, "email.smtp_host")
    if host:
        user = get_setting(engine, "email.smtp_user") or ""
        sender_addr = get_setting(engine, "email.from_address") or user
        sender_name = get_setting(engine, "email.from_name") or "OfinTech Desk"
        sender = f"{sender_name} <{sender_addr}>" if sender_addr else ""
        return Mailer(host=host, port=int(get_setting(engine, "email.smtp_port") or 587), user=user,
                      password=get_secret(engine, "email.smtp_password") or "", sender=sender,
                      security=get_setting(engine, "email.smtp_security") or "starttls")
    return engine.mailer


def provider_status(engine: Engine) -> dict[str, Any]:
    m = mailer(engine)
    source = "dashboard" if get_setting(engine, "email.smtp_host") else ("environment" if engine.mailer.configured else "none")
    stats = engine.db.one("SELECT SUM(status = 'sent') AS sent, SUM(status = 'failed') AS failed, SUM(status = 'queued') AS queued, "
                          "SUM(status = 'suppressed') AS suppressed, COUNT(*) AS total FROM email_jobs") or {}
    day = now() - 86400
    recent = engine.db.one("SELECT SUM(status = 'sent') AS sent, SUM(status = 'failed') AS failed FROM email_jobs WHERE created_at > ?",
                           (day,)) or {}
    return {"configured": m.configured, "source": source, "host": m.host if source != "none" else "",
            "port": m.port, "security": m.security, "sender": m.sender, "user": (m.user or ""),
            "password": secret_status(engine, "email.smtp_password"), "reply_to": get_setting(engine, "email.reply_to"),
            "stats": {k: int(v or 0) for k, v in stats.items()}, "last_24h": {k: int(v or 0) for k, v in recent.items()},
            "suppressed": engine.db.scalar("SELECT COUNT(*) FROM email_suppressions") or 0}


# --- queue -------------------------------------------------------------------------------------------------------------

def enqueue(engine: Engine, *, to: str, template_key: str, lang: str, vars: dict[str, Any], category: str,
            dedupe_key: str | None = None) -> str | None:
    """Queues one message; returns the job id (None when deduplicated or suppressed)."""
    t = get_template(engine, template_key, lang if lang in LANGS else "en")
    base = {"platform": get_setting(engine, "platform.name"), "url": engine.settings.public_url}
    subject = render(t["subject"], base | vars)[:300]
    body = render(t["body"], base | vars)
    reply_to = get_setting(engine, "email.reply_to")
    if reply_to:
        body = body + f"\n\nReply to: {reply_to}"
    suppressed = engine.db.one("SELECT reason FROM email_suppressions WHERE address = ?", (to.lower(),))
    status = "suppressed" if suppressed else "queued"
    jid = new_id("em_")
    with engine.db.tx() as c:
        inserted = c.execute("INSERT OR IGNORE INTO email_jobs (id, to_address, subject, body, template_key, category, status, "
                             "next_attempt_at, dedupe_key, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                             (jid, to.lower(), subject, body, template_key, category, status, now(), dedupe_key, now())).rowcount
    return jid if inserted and status == "queued" else None


async def deliver_pending(engine: Engine, limit: int = 25) -> int:
    """Called by the scheduler: sends due jobs, backs off on failure (1, 2, 4, 8… minutes), parks after the limit."""
    m = mailer(engine)
    if not m.configured:
        return 0
    max_attempts = int(get_setting(engine, "email.max_attempts") or 5)
    jobs = engine.db.all("SELECT * FROM email_jobs WHERE status = 'queued' AND (next_attempt_at IS NULL OR next_attempt_at <= ?) "
                         "ORDER BY created_at LIMIT ?", (now(), limit))
    sent = 0
    for job in jobs:
        # claim first so two scheduler ticks never send the same message
        if engine.db.execute("UPDATE email_jobs SET status = 'sending' WHERE id = ? AND status = 'queued'", (job["id"],)) != 1:
            continue
        try:
            ok = await m.send(job["to_address"], job["subject"], job["body"])
            if not ok:
                raise RuntimeError("the mailer refused the message")
            engine.db.update("email_jobs", job["id"], {"status": "sent", "sent_at": now(), "attempts": job["attempts"] + 1})
            sent += 1
        except Exception as exc:  # noqa: BLE001 - recorded on the job
            attempts = job["attempts"] + 1
            reason = f"{type(exc).__name__}: {str(exc)[:300]}"
            if attempts >= max_attempts:
                engine.db.update("email_jobs", job["id"], {"status": "failed", "attempts": attempts, "last_error": reason})
            else:
                engine.db.update("email_jobs", job["id"], {"status": "queued", "attempts": attempts, "last_error": reason,
                                                           "next_attempt_at": now() + 60 * (2 ** (attempts - 1))})
            log.warning("e-mail to %s failed (%d/%d): %s", job["to_address"], attempts, max_attempts, reason)
    return sent


def retry(engine: Engine, job_id: str, *, by: str) -> None:
    n = engine.db.execute("UPDATE email_jobs SET status = 'queued', attempts = 0, next_attempt_at = ?, last_error = NULL WHERE id = ? "
                          "AND status IN ('failed','suppressed')", (now(), job_id))
    if not n:
        raise NotFound("Job not found or not retryable.")
    engine.audit(None, by, "email.job.retry", job_id, None, resource_type="email_job", decision="allowed", result="ok")


def list_jobs(engine: Engine, *, status: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
    sql = "SELECT id, to_address, subject, template_key, category, status, attempts, last_error, next_attempt_at, created_at, sent_at FROM email_jobs"
    params: list[Any] = []
    if status:
        sql += " WHERE status = ?"
        params.append(status)
    return engine.db.all(sql + " ORDER BY created_at DESC LIMIT ?", [*params, limit])


def suppress(engine: Engine, address: str, reason: str, *, by: str | None) -> None:
    with engine.db.tx() as c:
        c.execute("INSERT INTO email_suppressions (address, reason, created_at) VALUES (?,?,?) ON CONFLICT(address) DO UPDATE SET "
                  "reason = excluded.reason", (address.lower(), reason[:100], now()))
    engine.audit(None, by, "email.suppress", address.lower(), {"reason": reason[:100]}, resource_type="email", decision="allowed", result="ok")


def unsuppress(engine: Engine, address: str, *, by: str) -> None:
    engine.db.execute("DELETE FROM email_suppressions WHERE address = ?", (address.lower(),))
    engine.audit(None, by, "email.unsuppress", address.lower(), None, resource_type="email", decision="allowed", result="ok")


def daily_stats(engine: Engine, days: int = 30) -> list[dict[str, Any]]:
    since = now() - days * 86400
    return engine.db.all("SELECT date(created_at, 'unixepoch') AS day, SUM(status = 'sent') AS sent, SUM(status = 'failed') AS failed, "
                         "SUM(status = 'queued') AS queued FROM email_jobs WHERE created_at > ? GROUP BY day ORDER BY day", (since,))


async def send_test(engine: Engine, to: str, *, by: str) -> dict[str, Any]:
    m = mailer(engine)
    if not m.configured:
        raise Invalid("E-mail is not configured yet.", code="email_unconfigured")
    ok = await m.send(to, f"[{get_setting(engine, 'platform.name')}] Test message",
                      "This is a test message from the Owner Control Center. E-mail delivery works.")
    engine.audit(None, by, "email.test", to.lower(), {"ok": ok}, resource_type="email", decision="allowed", result="ok" if ok else "failed")
    if not ok:
        raise Invalid("The SMTP server refused the message. Check the host, port, security and credentials.", code="smtp_failed")
    return {"ok": True}
