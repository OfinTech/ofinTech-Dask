"""Platform settings: one validated registry, stored in ``platform_settings`` and audited on every change.

A setting has a type and bounds; a value outside them is refused rather than clamped silently. Secret-bearing
settings (Stripe keys, the SMTP password, OAuth client secrets) live in the encrypted ``secrets`` table under the
``platform`` purpose and are never returned by the API — only whether one is set and, for keys with a public
prefix, the last four characters.

Settings the engine reads from the environment at start (session lifetimes, the idle window) can be *narrowed*
here within the ranges ``config.py`` allows; they can never be loosened past those ranges, and feature flags can
never switch off authentication, tenant isolation or any mandatory control: the registry simply has no such key.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable

from ..db import dumps, loads, now
from ..errors import Invalid

if TYPE_CHECKING:
    from ..app import Engine

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
DOMAIN_RE = re.compile(r"^[a-z0-9.-]+\.[a-z]{2,}$")
CURRENCY_RE = re.compile(r"^[A-Z]{3}$")


@dataclass(frozen=True)
class Spec:
    kind: str                       # bool | int | str | list | dict | choice | secret
    default: Any = None
    choices: tuple[str, ...] = ()
    lo: int | None = None
    hi: int | None = None
    max_len: int = 500
    item: Callable[[str], bool] | None = None   # validator for list items
    public: bool = False            # exposed to signed-in users through /api/session (never secrets)
    critical: bool = False          # a change goes through dual approval when that policy is on
    group: str = "general"


REGISTRY: dict[str, Spec] = {
    # general
    "platform.name": Spec("str", "OfinTech Desk", max_len=80, public=True),
    "platform.logo_url": Spec("str", "", max_len=500, public=True),
    "platform.support_email": Spec("str", "", max_len=254, public=True),
    "platform.default_language": Spec("choice", "en", choices=("en", "ar", "es", "fr"), public=True),
    "platform.supported_languages": Spec("list", ["en", "ar", "es", "fr"], item=lambda v: v in ("en", "ar", "es", "fr"), public=True),
    "platform.default_timezone": Spec("str", "UTC", max_len=60),
    "platform.maintenance_mode": Spec("bool", False, public=True),
    "platform.maintenance_message": Spec("str", "", max_len=500, public=True),
    # registration
    "registration.enabled": Spec("bool", None, public=True),          # None: follow DESK_OPEN_SIGNUP
    "registration.require_verified_email": Spec("bool", False),
    "registration.allowed_domains": Spec("list", [], item=lambda v: bool(DOMAIN_RE.match(v))),
    # Google sign-in
    "google.enabled": Spec("bool", True, group="google"),
    "google.registration": Spec("choice", "follow", choices=("follow", "open", "invite_only", "disabled"), group="google"),
    "google.allowed_domains": Spec("list", [], item=lambda v: bool(DOMAIN_RE.match(v)), group="google"),
    "google.link_existing": Spec("choice", "verified_email", choices=("verified_email", "never"), group="google"),
    # Stripe
    "stripe.enabled": Spec("bool", False, group="stripe", critical=True),
    "stripe.mode": Spec("choice", "test", choices=("test", "live"), group="stripe", critical=True),
    "stripe.publishable_key": Spec("str", "", max_len=200, group="stripe"),
    "stripe.secret_key": Spec("secret", group="stripe", critical=True),
    "stripe.webhook_secret": Spec("secret", group="stripe", critical=True),
    "stripe.currencies": Spec("list", ["USD"], item=lambda v: bool(CURRENCY_RE.match(v)), group="stripe"),
    "stripe.portal_enabled": Spec("bool", True, group="stripe"),
    "stripe.tax_enabled": Spec("bool", False, group="stripe"),
    "billing.grace_days": Spec("int", 7, lo=1, hi=60, group="stripe"),
    "billing.allow_promotion_codes": Spec("bool", True, group="stripe"),
    # e-mail
    "email.provider": Spec("choice", "smtp", choices=("smtp",), group="email"),
    "email.smtp_host": Spec("str", "", max_len=253, group="email"),
    "email.smtp_port": Spec("int", 587, lo=1, hi=65535, group="email"),
    "email.smtp_user": Spec("str", "", max_len=254, group="email"),
    "email.smtp_password": Spec("secret", group="email"),
    "email.smtp_security": Spec("choice", "starttls", choices=("starttls", "ssl", "none"), group="email"),
    "email.from_address": Spec("str", "", max_len=254, group="email"),
    "email.from_name": Spec("str", "OfinTech Desk", max_len=120, group="email"),
    "email.reply_to": Spec("str", "", max_len=254, group="email"),
    "email.max_attempts": Spec("int", 5, lo=1, hi=20, group="email"),
    # analytics
    "analytics.enabled": Spec("bool", True, group="analytics", public=True),
    "analytics.require_consent": Spec("bool", True, group="analytics", public=True),
    "analytics.collect_country": Spec("bool", True, group="analytics"),
    "analytics.retention_days": Spec("int", 400, lo=30, hi=1825, group="analytics"),
    # security
    "security.mfa_policy": Spec("choice", "none", choices=("none", "admins", "all"), group="security", critical=True),
    "security.dual_approval": Spec("bool", False, group="security"),
    "security.lockout_threshold": Spec("int", 8, lo=3, hi=50, group="security"),
    "security.lockout_minutes": Spec("int", 5, lo=1, hi=1440, group="security"),
    "security.alert_failed_logins": Spec("int", 10, lo=3, hi=1000, group="security"),
    "security.session_idle_minutes": Spec("int", None, lo=30, hi=45, group="security", critical=True),
    "security.session_hours": Spec("int", None, lo=1, hi=24, group="security", critical=True),
    "security.admin_session_hours": Spec("int", None, lo=1, hi=24, group="security", critical=True),
    "security.audit_retention_days": Spec("int", 730, lo=90, hi=3650, group="security"),
    "security.api_key_max_age_days": Spec("int", 0, lo=0, hi=3650, group="security"),
    "security.oauth_providers": Spec("list", ["google"], item=lambda v: v in ("google",), group="security"),
    # storage
    "storage.over_quota_policy": Spec("choice", "read_only", choices=("read_only", "grace"), group="storage"),
    "storage.grace_days": Spec("int", 14, lo=1, hi=90, group="storage"),
    "storage.retention_days_after_delete": Spec("int", 30, lo=0, hi=365, group="storage"),
    "storage.max_file_mb": Spec("int", None, lo=1, hi=200, group="storage"),
    # feature flags: global kill switches for optional product features (never for security controls)
    "features.flags": Spec("dict", {}, group="features", public=True),
    "features.experimental": Spec("dict", {}, group="features", public=True),
    # notifications / alerts
    "alerts.rules": Spec("dict", {}, group="alerts"),
    "notifications.webhook_url": Spec("str", "", max_len=500, group="alerts"),
    "notifications.webhook_secret": Spec("secret", group="alerts"),
}

FEATURE_FLAG_KEYS = ("ask_finance", "autonomous_reconciliation", "financial_intelligence", "bank_connections",
                     "erp_connections", "file_storage", "advanced_analytics", "api_access", "data_export",
                     "advanced_audit_logs", "priority_support", "workflows", "mcp")


def _validate(key: str, value: Any) -> Any:
    spec = REGISTRY.get(key)
    if spec is None:
        raise Invalid(f"Unknown setting {key}.", code="unknown_setting")
    if spec.kind == "secret":
        if not isinstance(value, str) or not value.strip() or len(value) > 1000:
            raise Invalid(f"{key}: a secret must be a non-empty string.")
        return value.strip()
    if value is None:
        return None
    if spec.kind == "bool":
        if not isinstance(value, bool):
            raise Invalid(f"{key} must be true or false.")
        return value
    if spec.kind == "int":
        if isinstance(value, bool) or not isinstance(value, int):
            raise Invalid(f"{key} must be a whole number.")
        if (spec.lo is not None and value < spec.lo) or (spec.hi is not None and value > spec.hi):
            raise Invalid(f"{key} must be between {spec.lo} and {spec.hi}.", code="out_of_range")
        return value
    if spec.kind == "str":
        if not isinstance(value, str):
            raise Invalid(f"{key} must be text.")
        value = value.strip()
        if len(value) > spec.max_len:
            raise Invalid(f"{key} is too long (max {spec.max_len}).")
        if key.endswith(("_email", "_address", "reply_to")) and value and not EMAIL_RE.match(value):
            raise Invalid(f"{key} must be an e-mail address.")
        if key == "platform.logo_url" and value and not re.match(r"^(https://|/)[^\s]+$", value):
            raise Invalid("The logo must be an https:// address or a path on this server.")
        if key == "notifications.webhook_url" and value and not value.startswith("https://"):
            raise Invalid("The notification webhook must be an https:// address.")
        return value
    if spec.kind == "choice":
        if value not in spec.choices:
            raise Invalid(f"{key} must be one of: {', '.join(spec.choices)}.")
        return value
    if spec.kind == "list":
        if not isinstance(value, list) or len(value) > 100:
            raise Invalid(f"{key} must be a list.")
        out = []
        for item in value:
            if not isinstance(item, str):
                raise Invalid(f"{key}: items must be text.")
            item = item.strip().lower() if "domain" in key or "language" in key else item.strip()
            if spec.item and not spec.item(item):
                raise Invalid(f"{key}: {item!r} is not allowed.")
            out.append(item)
        return out
    if spec.kind == "dict":
        if not isinstance(value, dict) or len(value) > 200:
            raise Invalid(f"{key} must be an object.")
        for k, v in value.items():
            if not isinstance(k, str) or not re.fullmatch(r"[a-z0-9_.-]{1,60}", k):
                raise Invalid(f"{key}: bad key {k!r}.")
            if key.startswith("features.") and not isinstance(v, bool):
                raise Invalid(f"{key}: flags are true or false.")
            if key == "alerts.rules" and not (isinstance(v, dict) and set(v) <= {"in_app", "email", "threshold"}):
                raise Invalid("alerts.rules: each rule is {in_app, email, threshold}.")
        return value
    raise Invalid(f"Unknown setting kind for {key}.")  # pragma: no cover


def get_setting(engine: Engine, key: str) -> Any:
    spec = REGISTRY[key]
    if spec.kind == "secret":
        raise ValueError("use get_secret for secret settings")
    row = engine.db.one("SELECT value FROM platform_settings WHERE key = ?", (key,))
    if row is None:
        return spec.default
    return loads(row["value"])


def set_setting(engine: Engine, key: str, value: Any, *, by: str | None) -> Any:
    spec = REGISTRY.get(key)
    if spec is None:
        raise Invalid(f"Unknown setting {key}.", code="unknown_setting")
    if spec.kind == "secret":
        return set_secret(engine, key, value, by=by)
    clean = _validate(key, value)
    old = get_setting(engine, key)
    with engine.db.tx() as c:
        c.execute("INSERT INTO platform_settings (key, value, updated_by, updated_at) VALUES (?,?,?,?) ON CONFLICT(key) "
                  "DO UPDATE SET value = excluded.value, updated_by = excluded.updated_by, updated_at = excluded.updated_at",
                  (key, dumps(clean), by, now()))
    engine.audit(None, by, "platform.setting.update", key, {"from": old, "to": clean}, resource_type="platform_setting",
                 decision="allowed", result="ok")
    _apply_runtime(engine, key)
    return clean


def set_secret(engine: Engine, key: str, value: str, *, by: str | None) -> dict[str, Any]:
    clean = _validate(key, value)
    marker_key = f"{key}:secret"
    existing = engine.db.one("SELECT value FROM platform_settings WHERE key = ?", (marker_key,))
    secret_id = loads(existing["value"]).get("secret_id") if existing else None
    secret_id = engine.secrets.put("platform", {"value": clean}, workspace_id=None, secret_id=secret_id)
    marker = {"secret_id": secret_id, "last4": clean[-4:] if len(clean) >= 12 else "", "set_at": now()}
    with engine.db.tx() as c:
        c.execute("INSERT INTO platform_settings (key, value, updated_by, updated_at) VALUES (?,?,?,?) ON CONFLICT(key) "
                  "DO UPDATE SET value = excluded.value, updated_by = excluded.updated_by, updated_at = excluded.updated_at",
                  (marker_key, dumps(marker), by, now()))
    engine.audit(None, by, "platform.secret.update", key, {"last4": marker["last4"]}, resource_type="platform_setting",
                 decision="allowed", result="ok")
    return {"configured": True, "last4": marker["last4"]}


def clear_secret(engine: Engine, key: str, *, by: str | None) -> None:
    marker_key = f"{key}:secret"
    existing = engine.db.one("SELECT value FROM platform_settings WHERE key = ?", (marker_key,))
    if not existing:
        return
    secret_id = loads(existing["value"]).get("secret_id")
    with engine.db.tx() as c:
        c.execute("DELETE FROM platform_settings WHERE key = ?", (marker_key,))
        if secret_id:
            c.execute("DELETE FROM secrets WHERE id = ?", (secret_id,))
    engine.audit(None, by, "platform.secret.clear", key, None, resource_type="platform_setting", decision="allowed", result="ok")


def get_secret(engine: Engine, key: str) -> str | None:
    """The plain secret, for server-side use only (never returned by a route)."""
    existing = engine.db.one("SELECT value FROM platform_settings WHERE key = ?", (f"{key}:secret",))
    if not existing:
        return None
    secret_id = loads(existing["value"]).get("secret_id")
    try:
        return engine.secrets.get(secret_id, workspace_id=None)["value"]
    except Exception:
        return None


def secret_status(engine: Engine, key: str) -> dict[str, Any]:
    existing = engine.db.one("SELECT value FROM platform_settings WHERE key = ?", (f"{key}:secret",))
    if not existing:
        return {"configured": False, "last4": ""}
    marker = loads(existing["value"], {})
    return {"configured": True, "last4": marker.get("last4", ""), "set_at": marker.get("set_at")}


def group_view(engine: Engine, group: str) -> dict[str, Any]:
    """Every setting of a group as the dashboard shows it: values for plain settings, status for secrets."""
    out: dict[str, Any] = {}
    for key, spec in REGISTRY.items():
        if spec.group != group:
            continue
        out[key] = secret_status(engine, key) if spec.kind == "secret" else get_setting(engine, key)
    return out


def public_view(engine: Engine) -> dict[str, Any]:
    """The settings every signed-in person may know (branding, languages, maintenance, flags)."""
    return {key: get_setting(engine, key) for key, spec in REGISTRY.items() if spec.public}


def feature_flag(engine: Engine, name: str) -> bool:
    """A global product switch; absent means on. Security controls are not flags and cannot be switched here."""
    flags = get_setting(engine, "features.flags") or {}
    return bool(flags.get(name, True))


def _apply_runtime(engine: Engine, key: str) -> None:
    """Session limits set from the dashboard take effect at once, within the bounds the configuration allows."""
    if not key.startswith("security.session") and key != "security.admin_session_hours":
        return
    apply_session_overrides(engine)


def apply_session_overrides(engine: Engine) -> None:
    import dataclasses

    from ..config import IDLE_MAX, IDLE_MIN

    s = engine.settings
    if s.is_desktop:
        return
    changes: dict[str, Any] = {}
    idle = get_setting(engine, "security.session_idle_minutes")
    if idle is not None:
        changes["session_idle_minutes"] = max(IDLE_MIN, min(IDLE_MAX, int(idle)))
    hours = get_setting(engine, "security.session_hours")
    if hours is not None:
        changes["session_hours"] = max(1, min(24, int(hours)))
    admin_hours = get_setting(engine, "security.admin_session_hours")
    if admin_hours is not None:
        changes["admin_session_hours"] = max(1, min(changes.get("session_hours", s.session_hours), int(admin_hours)))
    if changes:
        engine.settings = dataclasses.replace(s, **changes)
