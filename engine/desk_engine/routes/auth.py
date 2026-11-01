"""Session, sign-up, sign-in, invitations."""

from __future__ import annotations

import hmac
import re
import time
from collections import defaultdict, deque
from typing import Any, Literal

from pydantic import BaseModel, Field
from starlette.responses import JSONResponse

from ..config import VERSION
from ..db import dumps, loads, new_id, now
from ..errors import Conflict, Forbidden, Invalid, NotFound, Unauthorized
from ..security import check_password_policy, hash_password, new_token, token_hash, verify_password
from ..web import SESSION_COOKIE, Ctx, Email, Router, optional_user, public_user, session_cookie_max_age, session_expiry

router = Router()


def _rate_limit(ctx: Any, key: str, limit: int = 8, window: float = 300) -> None:
    """``ctx`` is a request context or the engine itself (for the form endpoints)."""
    engine = ctx if hasattr(ctx, "db") else ctx.engine
    if key.startswith("login:"):   # the account lockout policy set by the platform owner (Security Center)
        from ..platform.settings import get_setting
        limit = int(get_setting(engine, "security.lockout_threshold") or limit)
        window = int(get_setting(engine, "security.lockout_minutes") or 5) * 60
    attempts: dict[str, deque[float]] = engine.__dict__.setdefault("_auth_attempts", defaultdict(deque))
    q = attempts[key]
    t = time.monotonic()
    while q and t - q[0] > window:
        q.popleft()
    if len(q) >= limit:
        raise Invalid("Too many attempts. Wait a few minutes and try again.", code="rate_limited", status=429)
    q.append(t)


def instance_admin(engine: Any) -> str | None:
    value = engine.db.scalar("SELECT value FROM settings WHERE key = 'instance_admin'")
    if value:
        return loads(value)
    if engine.settings.is_desktop:
        return engine.db.scalar("SELECT id FROM users WHERE is_local = 1")
    return None


def is_instance_admin(ctx: Ctx) -> bool:
    return ctx.user is not None and instance_admin(ctx.engine) == ctx.user["id"]


def workspaces_for(engine: Any, user_id: str) -> list[dict[str, Any]]:
    return engine.db.all("SELECT w.id, w.name, w.base_currency, w.country, w.fiscal_year_start_month, "
                         "w.accounting_framework, w.timezone, w.egress_policy, m.role FROM workspaces w JOIN members m ON "
                         "m.workspace_id = w.id WHERE m.user_id = ? ORDER BY w.name", (user_id,))


@router.get("/api/session", auth=False)
async def session(ctx: Ctx) -> dict[str, Any]:
    engine = ctx.engine
    user = optional_user(engine, ctx.request)
    users_exist = engine.db.scalar("SELECT COUNT(*) FROM users WHERE is_local = 0") > 0
    base = {"version": VERSION, "mode": engine.settings.mode,
            "signup_open": engine.settings.open_signup or not users_exist,
            "needs_first_user": not engine.settings.is_desktop and not users_exist,
            "public_url": engine.settings.public_url,
            "site_url": engine.settings.site_url,
            "idle_minutes": 0 if engine.settings.is_desktop else engine.settings.session_idle_minutes,
            "session_hours": 0 if engine.settings.is_desktop else engine.settings.session_hours,
            "reauth_minutes": 0 if engine.settings.is_desktop else engine.settings.reauth_minutes,
            "lock_minutes": engine.settings.session_idle_minutes if engine.settings.is_desktop else 0,
            "warn_seconds": 120,
            "features": {"stdio_mcp": engine.settings.allow_stdio_mcp, "sealed": engine.settings.sealed,
                         "whatsapp": engine.settings.public_url.startswith("https://"),
                         "team": not engine.settings.is_desktop,
                         "google_login": google_login_available(engine),
                         "email": engine.mailer.configured}}
    from ..platform import mfa
    from ..platform.owner import permissions_for, platform_role
    from ..platform.plans import entitlements_for
    from ..platform.settings import public_view

    base["platform"] = public_view(engine)
    base["hosted"] = engine.settings.hosted
    base["features"]["billing"] = bool(base["platform"].get("features.flags", {}).get("billing", True)) and engine.settings.hosted
    if user is None:
        return {**base, "user": None, "workspaces": []}
    role = platform_role(engine, user["id"])
    return {**base, "user": public_user(user) | {"instance_admin": instance_admin(engine) == user["id"],
                                                 "platform_role": role, "permissions": permissions_for(role),
                                                 "must_enroll_mfa": mfa.must_enroll(engine, user),
                                                 "must_reset_password": bool(user.get("must_reset_password"))},
            "entitlements": entitlements_for(engine, user["id"]) if not engine.settings.is_desktop else None,
            "workspaces": workspaces_for(engine, user["id"])}


class SignupIn(BaseModel):
    email: Email
    name: str = Field(min_length=1, max_length=120)
    password: str = Field(min_length=1, max_length=200)
    invite_token: str | None = None


def forget_dead_sessions(engine: Any, user_id: str) -> None:
    """Rows nobody can use again. A session is otherwise deleted only when its own token turns up, so
    without this an abandoned one would sit in the table for ever."""
    idle_limit = engine.settings.session_idle_minutes * 60
    engine.db.execute("DELETE FROM sessions WHERE user_id = ? AND (expires_at < ? OR (? > 0 AND last_seen < ?))",
                      (user_id, now(), idle_limit, now() - idle_limit))


def create_session(engine: Any, request: Any, user_id: str) -> str:
    token = new_token(32)
    ts = now()
    forget_dead_sessions(engine, user_id)
    engine.db.insert("sessions", {"id": token_hash(token), "user_id": user_id, "created_at": ts,
                                  "expires_at": session_expiry(engine, user_id), "last_seen": ts, "reauth_at": ts,
                                  "user_agent": request.headers.get("user-agent", "")[:200]})
    return token


def set_session_cookie(engine: Any, resp: Any, token: str) -> None:
    resp.set_cookie(SESSION_COOKIE, token, max_age=session_cookie_max_age(engine), httponly=True,
                    secure=engine.settings.public_url.startswith("https://"), samesite="strict", path="/")


def _start_session(ctx: Ctx, user_id: str) -> JSONResponse:
    token = create_session(ctx.engine, ctx.request, user_id)
    user = ctx.engine.db.one("SELECT * FROM users WHERE id = ?", (user_id,))
    resp = JSONResponse({"token": token, "user": public_user(user), "workspaces": workspaces_for(ctx.engine, user_id)})
    set_session_cookie(ctx.engine, resp, token)
    return resp


def signup_user(engine: Any, *, email: str, name: str, password: str, invite_token: str | None,
                client_host: str) -> str:
    """Creates the account (the JSON API and the website's form share this) and returns its id."""
    if engine.settings.is_desktop:
        raise Forbidden("The desktop app has no accounts; it signs you in automatically.")
    _rate_limit(engine, f"signup:{client_host}")
    first = engine.db.scalar("SELECT COUNT(*) FROM users WHERE is_local = 0") == 0
    invite = None
    if invite_token:
        invite = engine.db.one("SELECT * FROM invites WHERE token_hash = ? AND accepted_at IS NULL AND expires_at > ?",
                               (token_hash(invite_token), now()))
        if invite is None:
            raise Invalid("This invitation is not valid or has expired.", code="bad_invite")
        if invite["email"].lower() != email.lower():
            raise Invalid("Sign up with the e-mail address the invitation was sent to.", code="invite_email")
    elif not (first or registration_open(engine)):
        raise Forbidden("Sign-up is by invitation. Ask a workspace administrator to invite you.", code="invitation_only")
    if not invite and not first:
        check_registration_domain(engine, email)
    check_password_policy(password)
    if engine.db.one("SELECT 1 FROM users WHERE email = ?", (email.lower(),)):
        raise Conflict("An account with this e-mail already exists. Sign in instead.", code="exists")
    uid = new_id("u_")
    engine.db.insert("users", {"id": uid, "email": email.lower(), "name": name.strip(),
                               "password_hash": hash_password(password), "created_at": now(),
                               "signup_method": "invite" if invite else "password",
                               "email_verified_at": now() if invite else None})
    if first:
        with engine.db.tx() as c:
            c.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('instance_admin', ?)", (dumps(uid),))
    if invite:
        _accept(engine, invite, uid)
    engine.audit(invite["workspace_id"] if invite else None, uid, "user.signup", uid, {"first": first})
    after_signup(engine, uid, method="invite" if invite else "password", client_host=client_host)
    return uid


def registration_open(engine: Any) -> bool:
    """The owner's switch wins over DESK_OPEN_SIGNUP when set."""
    from ..platform.settings import get_setting

    flag = get_setting(engine, "registration.enabled")
    return engine.settings.open_signup if flag is None else bool(flag)


def check_registration_domain(engine: Any, email: str) -> None:
    from ..platform.settings import get_setting

    allowed = get_setting(engine, "registration.allowed_domains") or []
    if allowed and email.rsplit("@", 1)[-1].lower() not in allowed:
        raise Forbidden("Sign-up on this server is limited to the organisation's e-mail domains.", code="domain_not_allowed")


def after_signup(engine: Any, uid: str, *, method: str, client_host: str) -> None:
    """Platform-level consequences of a new account: owner provisioning, alerts, welcome and verification e-mail."""
    from ..platform.email import enqueue
    from ..platform.notifications import owner_alert
    from ..platform.owner import on_signup

    user = engine.db.one("SELECT * FROM users WHERE id = ?", (uid,))
    if user is None:
        return
    on_signup(engine, uid, user["email"])
    owner_alert(engine, "new_registration", f"New account: {user['email']} ({method})", category="account", link="#/owner/users")
    recent = engine.db.scalar("SELECT COUNT(*) FROM audit_log WHERE action = 'user.signup' AND created_at > ?", (now() - 600,)) or 0
    if recent >= 10:
        owner_alert(engine, "suspicious_registration", f"{recent} sign-ups in the last 10 minutes", category="security", link="#/owner/security")
    lang = user.get("language") or "en"
    enqueue(engine, to=user["email"], template_key="welcome", lang=lang, vars={"name": user["name"]}, category="account",
            dedupe_key=f"welcome:{uid}")
    if not user.get("email_verified_at"):
        send_verification(engine, user)


def send_verification(engine: Any, user: dict[str, Any]) -> None:
    from ..platform.email import enqueue

    token = new_token(32)
    engine.db.insert("email_verifications", {"token_hash": token_hash(token), "user_id": user["id"], "created_at": now(),
                                             "expires_at": now() + 86400})
    enqueue(engine, to=user["email"], template_key="email_verification", lang=user.get("language") or "en",
            vars={"name": user["name"], "link": f"{engine.settings.public_url.rstrip('/')}/#/verify-email/{token}"},
            category="account")


def login_user(engine: Any, *, email: str, password: str, client_host: str) -> str:
    """Checks the password (the JSON API and the website's form share this) and returns the user's id."""
    if engine.settings.is_desktop:
        raise Forbidden("The desktop app signs you in automatically.")
    _rate_limit(engine, f"login:{email.lower()}")
    _rate_limit(engine, f"login-ip:{client_host}", limit=30)
    user = engine.db.one("SELECT * FROM users WHERE email = ? AND is_local = 0", (email.lower(),))
    if not verify_password(password, user["password_hash"] if user else None) or user is None:
        engine.audit(None, user["id"] if user else None, "user.login.failed", None, {"reason": "bad_credentials"},
                     resource_type="session", decision="denied", result="refused")
        if user is not None:
            failed_login_alerts(engine, user)
        raise Unauthorized("E-mail or password is wrong.", code="bad_credentials")
    if user["disabled"]:
        raise Forbidden("This account is disabled." + (f" Reason: {user['suspended_reason']}" if user.get("suspended_reason") else ""),
                        code="disabled")
    engine.audit(None, user["id"], "user.login", user["id"])
    engine.db.execute("UPDATE users SET last_login_at = ? WHERE id = ?", (now(), user["id"]))
    return user["id"]


def failed_login_alerts(engine: Any, user: dict[str, Any]) -> None:
    """Repeated failures on one account: the owner is alerted at the policy's threshold and the person warned once."""
    from ..platform.notifications import alert_rules, owner_alert, push
    from ..platform.settings import get_setting

    rule = alert_rules(engine).get("repeated_failed_logins") or {}
    threshold = int(rule.get("threshold") or get_setting(engine, "security.alert_failed_logins") or 10)
    recent = engine.db.scalar("SELECT COUNT(*) FROM audit_log WHERE action = 'user.login.failed' AND user_id = ? AND created_at > ?",
                              (user["id"], now() - 3600)) or 0
    if recent == threshold:
        owner_alert(engine, "repeated_failed_logins", f"{recent} failed sign-in attempts on {user['email']} in the last hour",
                    category="security", link="#/owner/security")
        push(engine, user["id"], category="security", title=f"{recent} failed sign-in attempts on your account",
             email_template="suspicious_login", email_vars={"count": recent}, dedupe=f"failed-logins:{user['id']}:{int(now() // 3600)}")


@router.post("/api/auth/signup", auth=False, body=SignupIn)
async def signup(ctx: Ctx) -> Any:
    body: SignupIn = ctx.body
    uid = signup_user(ctx.engine, email=body.email, name=body.name, password=body.password,
                      invite_token=body.invite_token, client_host=ctx.request.client.host if ctx.request.client else "-")
    return _start_session(ctx, uid)


class LoginIn(BaseModel):
    email: Email
    password: str = Field(max_length=200)


@router.post("/api/auth/login", auth=False, body=LoginIn)
async def login(ctx: Ctx) -> Any:
    from ..platform import mfa

    body: LoginIn = ctx.body
    uid = login_user(ctx.engine, email=body.email, password=body.password,
                     client_host=ctx.request.client.host if ctx.request.client else "-")
    user = ctx.engine.db.one("SELECT * FROM users WHERE id = ?", (uid,))
    if user and user.get("mfa_enabled_at"):
        # the password alone opens no session: a short-lived challenge is answered with the authenticator code
        return {"mfa_required": True, "challenge": mfa.new_challenge(ctx.engine, uid)}
    return _start_session(ctx, uid)


class MfaVerifyIn(BaseModel):
    challenge: str = Field(min_length=20, max_length=200)
    code: str = Field(min_length=6, max_length=8)


@router.post("/api/auth/mfa/verify", auth=False, body=MfaVerifyIn)
async def mfa_verify(ctx: Ctx) -> Any:
    from ..platform import mfa

    _rate_limit(ctx, f"mfa-ip:{ctx.request.client.host if ctx.request.client else '-'}", limit=30)
    uid = mfa.redeem_challenge(ctx.engine, ctx.body.challenge, ctx.body.code)
    ctx.engine.audit(None, uid, "user.login.mfa", uid, None, resource_type="session", decision="allowed", result="ok")
    resp = _start_session(ctx, uid)
    row = ctx.engine.db.one("SELECT id FROM sessions WHERE user_id = ? ORDER BY created_at DESC LIMIT 1", (uid,))
    if row:
        ctx.engine.db.execute("UPDATE sessions SET mfa_at = ? WHERE id = ?", (now(), row["id"]))
    return resp


@router.post("/api/auth/mfa/begin")
async def mfa_begin(ctx: Ctx) -> dict[str, Any]:
    from ..platform import mfa

    return mfa.begin_enrollment(ctx.engine, ctx.user)


class MfaCodeIn(BaseModel):
    code: str = Field(min_length=6, max_length=8)


@router.post("/api/auth/mfa/confirm", body=MfaCodeIn)
async def mfa_confirm(ctx: Ctx) -> dict[str, Any]:
    from ..platform import mfa

    _rate_limit(ctx, f"mfa-confirm:{ctx.user_id}", limit=10)
    mfa.confirm_enrollment(ctx.engine, ctx.user, ctx.body.code)
    return {"ok": True}


@router.post("/api/auth/mfa/disable", body=MfaCodeIn)
async def mfa_disable(ctx: Ctx) -> dict[str, Any]:
    from ..platform import mfa

    _rate_limit(ctx, f"mfa-confirm:{ctx.user_id}", limit=10)
    if mfa.must_enroll(ctx.engine, {**ctx.user, "mfa_enabled_at": None}):
        raise Forbidden("Two-factor authentication is required for your account by policy.", code="mfa_required")
    mfa.disable(ctx.engine, ctx.user, code=ctx.body.code, by=ctx.user_id)
    return {"ok": True}


@router.get("/api/auth/verify-email/{token}", auth=False)
async def verify_email(ctx: Ctx) -> dict[str, Any]:
    from ..platform.notifications import owner_alert

    row = ctx.engine.db.one("SELECT * FROM email_verifications WHERE token_hash = ? AND used_at IS NULL AND expires_at > ?",
                            (token_hash(ctx.request.path_params["token"]), now()))
    if row is None:
        raise Invalid("This verification link is not valid or has expired.", code="bad_verification")
    with ctx.engine.db.tx() as c:
        c.execute("UPDATE email_verifications SET used_at = ? WHERE token_hash = ?", (now(), row["token_hash"]))
        c.execute("UPDATE users SET email_verified_at = COALESCE(email_verified_at, ?) WHERE id = ?", (now(), row["user_id"]))
    ctx.engine.audit(None, row["user_id"], "user.email_verified", row["user_id"], None, resource_type="user", decision="allowed", result="ok")
    user = ctx.engine.db.one("SELECT email FROM users WHERE id = ?", (row["user_id"],))
    owner_alert(ctx.engine, "email_verified", f"{user['email'] if user else row['user_id']} verified their e-mail", category="account")
    return {"ok": True}


@router.post("/api/auth/verify-email/resend")
async def verify_email_resend(ctx: Ctx) -> dict[str, Any]:
    if ctx.user.get("email_verified_at"):
        return {"ok": True, "already": True}
    _rate_limit(ctx, f"verify:{ctx.user_id}", limit=3, window=900)
    send_verification(ctx.engine, ctx.user)
    from ..platform.email import mailer

    return {"ok": True, "email_configured": mailer(ctx.engine).configured}


def _session_state(ctx: Ctx) -> dict[str, Any]:
    """How long this session has left: until the idle sign-out and until its absolute end. The page shows the warning
    and the countdown from these numbers, never from its own clock alone."""
    from ..web import session_lifetime, session_row

    row = session_row(ctx.engine, ctx.request)
    idle = ctx.engine.settings.session_idle_minutes * 60
    if row is None or ctx.engine.settings.is_desktop:
        return {"ok": True, "idle_minutes": 0, "idle_seconds_left": None, "lifetime_seconds_left": None, "warn_seconds": 120,
                "fresh_seconds_left": None}
    life_end = min(row["expires_at"], row["created_at"] + session_lifetime(ctx.engine, row["user_id"]))
    fresh = ctx.engine.settings.reauth_minutes * 60
    return {"ok": True, "idle_minutes": ctx.engine.settings.session_idle_minutes,
            "idle_seconds_left": max(0, int(row["last_seen"] + idle - now())) if idle else None,
            "lifetime_seconds_left": max(0, int(life_end - now())),
            "fresh_seconds_left": max(0, int((row.get("reauth_at") or 0) + fresh - now())),
            "warn_seconds": 120}


@router.post("/api/session/ping")
async def session_ping(ctx: Ctx) -> dict[str, Any]:
    """Sent by the page after the person has actually done something (and by "Stay signed in"), to keep the session
    open. Reaching here has already refreshed the session (``web.authenticate``: a POST counts as activity, a page's
    polling GETs never do); a session that has already ended is refused with 401 before this runs, so an expired
    session cannot be revived by pinging. The limit is well above the page's own rate (one ping per minute)."""
    assert ctx.user is not None
    _rate_limit(ctx, f"ping:{ctx.user['id']}", limit=120, window=300)
    return _session_state(ctx)


@router.get("/api/session/status")
async def session_status(ctx: Ctx) -> dict[str, Any]:
    """The session's remaining time without extending it: a GET is not activity. Used after a tab wakes up, comes
    back online or becomes visible, and to keep several tabs' countdowns in step."""
    return _session_state(ctx)


class ReauthIn(BaseModel):
    password: str = Field(max_length=200)


@router.post("/api/auth/reauth", body=ReauthIn)
async def reauth(ctx: Ctx) -> dict[str, Any]:
    """Step-up: the person re-enters their password on an existing session before a high-risk action. Rate-limited
    like a sign-in; a Google-only account has no password and signs in again instead (which also counts)."""
    from ..web import session_row

    assert ctx.user is not None
    if ctx.user["is_local"]:
        return {"ok": True, "fresh_until": None}
    _rate_limit(ctx.engine, f"login:{ctx.user['email'].lower()}")
    if not ctx.user["password_hash"]:
        raise Forbidden("This account signs in with Google. Sign in again to confirm.", code="reauth_google")
    if not verify_password(ctx.body.password, ctx.user["password_hash"]):
        ctx.engine.audit(None, ctx.user_id, "user.reauth.failed", None, {"reason": "bad_credentials"},
                         resource_type="session", decision="denied", result="refused")
        raise Unauthorized("Password is wrong.", code="bad_credentials")
    row = session_row(ctx.engine, ctx.request)
    ts = now()
    if row:
        ctx.engine.db.execute("UPDATE sessions SET reauth_at = ? WHERE id = ?", (ts, row["id"]))
    ctx.engine.audit(None, ctx.user_id, "user.reauth", None, None, resource_type="session", decision="allowed", result="ok")
    return {"ok": True, "fresh_until": ts + ctx.engine.settings.reauth_minutes * 60}


@router.get("/api/auth/sessions")
async def list_sessions(ctx: Ctx) -> list[dict[str, Any]]:
    """The person's own sessions (devices), the current one marked; ids are opaque row ids, never tokens."""
    from ..web import session_row

    current = session_row(ctx.engine, ctx.request)
    rows = ctx.engine.db.all("SELECT id, created_at, last_seen, expires_at, user_agent FROM sessions WHERE user_id = ? "
                             "ORDER BY last_seen DESC", (ctx.user_id,))
    return [{"id": r["id"][:16], "created_at": r["created_at"], "last_seen": r["last_seen"], "user_agent": r["user_agent"],
             "current": bool(current and current["id"] == r["id"])} for r in rows]


@router.delete("/api/auth/sessions/{sid}")
async def revoke_session(ctx: Ctx) -> None:
    """Ends one of the person's own sessions (another device) by its listed id."""
    sid = ctx.request.path_params["sid"]
    with ctx.engine.db.tx() as c:
        n = c.execute("DELETE FROM sessions WHERE user_id = ? AND substr(id, 1, 16) = ?", (ctx.user_id, sid[:16])).rowcount
    if not n:
        raise NotFound("Session not found.")
    ctx.engine.audit(None, ctx.user_id, "session.revoked", sid[:16], {"by": "self"}, resource_type="session",
                     decision="allowed", result="signed_out")


@router.post("/api/auth/sign-out-everywhere")
async def sign_out_everywhere(ctx: Ctx) -> dict[str, Any]:
    """Ends every other session of the person (a lost phone, a shared computer)."""
    from ..web import session_row

    current = session_row(ctx.engine, ctx.request)
    with ctx.engine.db.tx() as c:
        n = c.execute("DELETE FROM sessions WHERE user_id = ? AND id != ?", (ctx.user_id, current["id"] if current else "")).rowcount
    ctx.engine.audit(None, ctx.user_id, "session.revoked", None, {"by": "self", "count": n}, resource_type="session",
                     decision="allowed", result="signed_out")
    return {"revoked": n}


@router.post("/api/admin/users/{user_id}/sign-out", fresh=True)
async def admin_sign_out(ctx: Ctx) -> dict[str, Any]:
    """The instance administrator terminates every session of an account (after confirming their own password)."""
    if not is_instance_admin(ctx):
        raise Forbidden("Only the instance administrator can do this.")
    target = ctx.request.path_params["user_id"]
    if ctx.engine.db.one("SELECT 1 FROM users WHERE id = ?", (target,)) is None:
        raise NotFound("User not found.")
    with ctx.engine.db.tx() as c:
        n = c.execute("DELETE FROM sessions WHERE user_id = ?", (target,)).rowcount
    ctx.engine.audit(None, ctx.user_id, "session.revoked", target, {"by": "instance_admin", "count": n},
                     resource_type="session", decision="allowed", result="signed_out")
    return {"revoked": n}


@router.post("/api/auth/logout")
async def logout(ctx: Ctx) -> Any:
    token = ctx.request.cookies.get(SESSION_COOKIE) or ctx.request.headers.get("authorization", "")[7:]
    if token:
        ctx.engine.db.execute("DELETE FROM sessions WHERE id = ?", (token_hash(token),))
    ctx.engine.audit(None, ctx.user["id"] if ctx.user else None, "session.logout", None,
                     {"reason": ctx.request.query_params.get("reason", "user")[:20]},
                     resource_type="session", decision="allowed", result="signed_out")
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(SESSION_COOKIE, path="/")
    return resp


class PasswordIn(BaseModel):
    current: str
    new: str


@router.post("/api/auth/password", body=PasswordIn)
async def change_password(ctx: Ctx) -> Any:
    if ctx.user["is_local"]:
        raise Forbidden("The desktop user has no password.")
    if not verify_password(ctx.body.current, ctx.user["password_hash"]):
        raise Invalid("Current password is wrong.", code="bad_credentials")
    check_password_policy(ctx.body.new)
    ctx.engine.db.execute("UPDATE users SET password_hash = ?, must_reset_password = 0 WHERE id = ?", (hash_password(ctx.body.new), ctx.user_id))
    ctx.engine.db.execute("DELETE FROM sessions WHERE user_id = ?", (ctx.user_id,))
    ctx.engine.audit(None, ctx.user_id, "user.password_changed", ctx.user_id, None, resource_type="user", decision="allowed", result="ok")
    return _start_session(ctx, ctx.user_id)


class ProfileIn(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    language: Literal["en", "ar", "es", "fr"] | None = None


@router.patch("/api/me", body=ProfileIn)
async def update_profile(ctx: Ctx) -> Any:
    """Your name and interface language. The assistant answers in your language unless you write in another one."""
    b: ProfileIn = ctx.body
    changes = {k: v for k, v in b.model_dump().items() if v is not None}
    if changes:
        ctx.engine.db.update("users", ctx.user_id, changes)
    user = ctx.engine.db.one("SELECT * FROM users WHERE id = ?", (ctx.user_id,))
    return public_user(user or {})


@router.get("/api/invites/{token}", auth=False)
async def invite_info(ctx: Ctx) -> dict[str, Any]:
    invite = ctx.engine.db.one("SELECT i.email, i.role, w.name AS workspace FROM invites i JOIN workspaces w ON "
                               "w.id = i.workspace_id WHERE i.token_hash = ? AND i.accepted_at IS NULL AND i.expires_at > ?",
                               (token_hash(ctx.request.path_params["token"]), now()))
    if invite is None:
        raise NotFound("This invitation is not valid or has expired.")
    return invite


@router.post("/api/invites/{token}/accept")
async def accept_invite(ctx: Ctx) -> dict[str, Any]:
    invite = ctx.engine.db.one("SELECT * FROM invites WHERE token_hash = ? AND accepted_at IS NULL AND expires_at > ?",
                               (token_hash(ctx.request.path_params["token"]), now()))
    if invite is None:
        raise NotFound("This invitation is not valid or has expired.")
    if invite["email"].lower() != ctx.user["email"].lower():
        raise Forbidden("This invitation was sent to another e-mail address.")
    _accept(ctx.engine, invite, ctx.user_id)
    return {"workspace_id": invite["workspace_id"]}


def _accept(engine: Any, invite: dict[str, Any], user_id: str) -> None:
    with engine.db.tx() as c:
        c.execute("INSERT OR IGNORE INTO members (workspace_id, user_id, role, created_at) VALUES (?,?,?,?)",
                  (invite["workspace_id"], user_id, invite["role"], now()))
        c.execute("UPDATE invites SET accepted_at = ? WHERE id = ?", (now(), invite["id"]))
    engine.audit(invite["workspace_id"], user_id, "member.joined", user_id, {"role": invite["role"]})


# --- password reset ----------------------------------------------------------------------------------------


class ForgotIn(BaseModel):
    email: Email


@router.post("/api/auth/forgot", auth=False, body=ForgotIn)
async def forgot(ctx: Ctx) -> dict[str, Any]:
    """Always answers the same way, so it cannot be used to find out which e-mails have accounts."""
    from ..mail import reset_text

    engine = ctx.engine
    if engine.settings.is_desktop:
        raise Forbidden("The desktop app has no passwords.")
    _rate_limit(ctx, f"forgot:{ctx.body.email.lower()}", limit=3, window=900)
    _rate_limit(ctx, f"forgot-ip:{ctx.request.client.host if ctx.request.client else '-'}", limit=20, window=900)
    from ..platform.email import enqueue, mailer

    m = mailer(engine)
    configured = m.configured
    user = engine.db.one("SELECT * FROM users WHERE email = ? AND is_local = 0 AND disabled = 0", (ctx.body.email,))
    if user and configured:
        token = new_token(32)
        engine.db.insert("password_resets", {"token_hash": token_hash(token), "user_id": user["id"], "created_at": now(),
                                             "expires_at": now() + 3600})
        link = f"{engine.settings.public_url.rstrip('/')}/#/reset/{token}"
        if m is engine.mailer:   # the environment's SMTP: sent at once, as before
            subject, text = reset_text(link)
            await m.send(user["email"], subject, text)
        else:                    # the owner's provider: through the queue, with the editable template
            enqueue(engine, to=user["email"], template_key="password_reset", lang=user.get("language") or "en",
                    vars={"name": user["name"], "link": link}, category="security")
        engine.audit(None, user["id"], "user.password_reset_requested", user["id"])
    return {"ok": True, "email_configured": configured,
            "message": "If an account exists for this e-mail, a reset link is on its way." if configured
            else "E-mail is not set up on this server. Ask the administrator to reset your password."}


class ResetIn(BaseModel):
    token: str = Field(min_length=20, max_length=200)
    password: str = Field(min_length=1, max_length=200)


@router.post("/api/auth/reset", auth=False, body=ResetIn)
async def reset(ctx: Ctx) -> Any:
    _rate_limit(ctx, f"reset-ip:{ctx.request.client.host if ctx.request.client else '-'}", limit=20)
    row = ctx.engine.db.one("SELECT * FROM password_resets WHERE token_hash = ? AND used_at IS NULL AND expires_at > ?",
                            (token_hash(ctx.body.token), now()))
    if row is None:
        raise Invalid("This reset link is not valid or has expired. Ask for a new one.", code="bad_reset")
    check_password_policy(ctx.body.password)
    with ctx.engine.db.tx() as c:
        c.execute("UPDATE password_resets SET used_at = ? WHERE token_hash = ?", (now(), row["token_hash"]))
        c.execute("UPDATE users SET password_hash = ?, must_reset_password = 0 WHERE id = ?", (hash_password(ctx.body.password), row["user_id"]))
        c.execute("DELETE FROM sessions WHERE user_id = ?", (row["user_id"],))
    ctx.engine.audit(None, row["user_id"], "user.password_reset", row["user_id"])
    return _start_session(ctx, row["user_id"])


@router.post("/api/admin/users/{user_id}/reset-link", fresh=True)
async def admin_reset_link(ctx: Ctx) -> dict[str, Any]:
    """For servers without e-mail: the instance administrator creates a one-hour reset link to pass on."""
    if not is_instance_admin(ctx):
        raise Forbidden("Only the administrator of this installation can do this.")
    target = ctx.engine.db.one("SELECT id FROM users WHERE id = ? AND is_local = 0", (ctx.request.path_params["user_id"],))
    if target is None:
        raise NotFound("User not found.")
    token = new_token(32)
    ctx.engine.db.insert("password_resets", {"token_hash": token_hash(token), "user_id": target["id"], "created_at": now(),
                                             "expires_at": now() + 3600})
    ctx.engine.audit(None, ctx.user_id, "user.reset_link", target["id"])
    return {"link": f"{ctx.engine.settings.public_url.rstrip('/')}/#/reset/{token}", "expires_in": 3600}


# --- sign in with Google -----------------------------------------------------------------------------------

GOOGLE_AUTH = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_USERINFO = "https://openidconnect.googleapis.com/v1/userinfo"


def google_login_available(engine: Any) -> bool:
    if engine.settings.is_desktop:
        return False
    from ..connectors.oauth import app_credentials
    from ..errors import AppError
    from ..platform.settings import get_setting

    if not get_setting(engine, "google.enabled") or "google" not in (get_setting(engine, "security.oauth_providers") or []):
        return False
    try:
        app_credentials(engine, "google")
        return True
    except AppError:
        return False


def google_start_url(engine: Any, *, invite_token: str | None = None, desktop_challenge: str | None = None) -> str:
    from urllib.parse import urlencode

    from ..connectors.oauth import app_credentials
    from ..security import pkce_pair

    if not google_login_available(engine):
        raise Invalid("Sign-in with Google is not set up on this server.", code="google_login_off")
    if desktop_challenge is not None and not (isinstance(desktop_challenge, str)
                                              and re.fullmatch(r"[A-Za-z0-9_-]{43}", desktop_challenge)):
        raise Invalid("Bad desktop challenge.")
    client_id, _, _ = app_credentials(engine, "google")
    verifier, challenge = pkce_pair()
    state = new_token(24)
    engine.db.execute("DELETE FROM login_states WHERE created_at < ?", (now() - 900,))
    engine.db.insert("login_states", {"state": state, "code_verifier": verifier,
                                      "invite_token": str(invite_token)[:200] if invite_token else None,
                                      "desktop_challenge": desktop_challenge, "created_at": now()})
    params = {"client_id": client_id, "redirect_uri": engine.settings.oauth_redirect_uri, "response_type": "code",
              "scope": "openid email profile", "state": state, "code_challenge": challenge,
              "code_challenge_method": "S256", "prompt": "select_account"}
    return f"{GOOGLE_AUTH}?{urlencode(params)}"


@router.post("/api/auth/google/start", auth=False)
async def google_start(ctx: Ctx) -> dict[str, Any]:
    try:
        body = await ctx.request.json()
    except ValueError:
        body = {}
    invite = (body or {}).get("invite_token") if isinstance(body, dict) else None
    # the desktop app opens Google in the system browser and proves it started the sign-in with this challenge
    desktop = (body or {}).get("desktop_challenge") if isinstance(body, dict) else None
    return {"url": google_start_url(ctx.engine, invite_token=invite, desktop_challenge=desktop)}


def _allowed_domain(engine: Any, email: str) -> bool:
    import os

    from ..platform.settings import get_setting

    allowed = [d.strip().lower() for d in os.environ.get("DESK_GOOGLE_ALLOWED_DOMAINS", "").split(",") if d.strip()]
    allowed += list(get_setting(engine, "google.allowed_domains") or [])
    return not allowed or email.rsplit("@", 1)[-1].lower() in allowed


async def google_callback(engine: Any, request: Any, params: dict[str, str]) -> tuple[str | None, str]:
    """Completes Google sign-in. Returns (session token or None, message for the page)."""
    import httpx2 as httpx

    from ..connectors.oauth import exchange_code

    state = engine.db.one("SELECT * FROM login_states WHERE state = ? AND created_at > ?",
                          (params.get("state", ""), now() - 900))
    if state is None:
        return None, "This sign-in link has expired. Start again."
    engine.db.execute("DELETE FROM login_states WHERE state = ?", (state["state"],))
    if params.get("error") or not params.get("code"):
        return None, "Google sign-in was cancelled."
    tokens = await exchange_code(engine, "google", params["code"], state["code_verifier"])
    async with httpx.AsyncClient(timeout=20, transport=engine.http_transport) as client:
        resp = await client.get(GOOGLE_USERINFO, headers={"Authorization": f"Bearer {tokens['access_token']}"})
    if resp.status_code >= 400:
        return None, "Google did not return your profile."
    info = resp.json()
    email, sub = str(info.get("email") or "").lower(), str(info.get("sub") or "")
    if not email or not sub or info.get("email_verified") not in (True, "true"):
        return None, "Your Google account has no verified e-mail address."
    if not _allowed_domain(engine, email):
        return None, "Google sign-in on this server is limited to the organisation's e-mail domains."
    from ..platform.settings import get_setting

    user = engine.db.one("SELECT * FROM users WHERE google_sub = ?", (sub,))
    linked_by_email = False
    if user is None:
        by_email = engine.db.one("SELECT * FROM users WHERE email = ? AND is_local = 0", (email,))
        if by_email is not None:
            # account linking: only a *verified* Google address may claim an existing account, and only when the policy
            # allows it; otherwise the person signs in with their password and links from Settings
            if get_setting(engine, "google.link_existing") != "verified_email":
                engine.audit(None, by_email["id"], "user.login.failed", None, {"reason": "google_link_refused"},
                             resource_type="session", decision="denied", result="refused")
                return None, "An account with this e-mail exists. Sign in with your password first."
            user, linked_by_email = by_email, True
    invite = None
    if state["invite_token"]:
        invite = engine.db.one("SELECT * FROM invites WHERE token_hash = ? AND accepted_at IS NULL AND expires_at > ?",
                               (token_hash(state["invite_token"]), now()))
        if invite and invite["email"].lower() != email:
            return None, f"The invitation was sent to {invite['email']}; sign in with that Google account."
    pending = engine.db.all("SELECT * FROM invites WHERE lower(email) = ? AND accepted_at IS NULL AND expires_at > ?",
                            (email, now()))
    if user is None:
        first = engine.db.scalar("SELECT COUNT(*) FROM users WHERE is_local = 0") == 0
        policy = get_setting(engine, "google.registration")
        open_google = {"follow": registration_open(engine), "open": True, "invite_only": False, "disabled": False}[policy]
        if policy == "disabled" and not first:
            return None, "New accounts cannot be created with Google on this server."
        if not (first or pending or open_google):
            return None, "There is no account for this Google address. Ask a workspace administrator to invite you."
        if not first and not pending:
            try:
                check_registration_domain(engine, email)
            except Forbidden as exc:
                return None, exc.message
        uid = new_id("u_")
        engine.db.insert("users", {"id": uid, "email": email, "name": str(info.get("name") or email.split("@")[0])[:120],
                                   "password_hash": None, "google_sub": sub, "created_at": now(), "signup_method": "google",
                                   "email_verified_at": now()})
        if first:
            with engine.db.tx() as c:
                c.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('instance_admin', ?)", (dumps(uid),))
        engine.audit(None, uid, "user.signup", uid, {"method": "google", "first": first})
        after_signup(engine, uid, method="google", client_host=request.client.host if request.client else "-")
        user = engine.db.one("SELECT * FROM users WHERE id = ?", (uid,))
    else:
        if user["disabled"]:
            return None, "This account is disabled."
        if not user.get("google_sub"):
            engine.db.execute("UPDATE users SET google_sub = ?, email_verified_at = COALESCE(email_verified_at, ?) WHERE id = ?",
                              (sub, now(), user["id"]))
            engine.audit(None, user["id"], "user.google_linked", user["id"], {"by_verified_email": linked_by_email},
                         resource_type="user", decision="allowed", result="ok")
    for inv in pending:
        _accept(engine, inv, user["id"])
    if state.get("desktop_challenge"):  # signed in in the system browser for the desktop app: hand it over
        code = new_token(32)
        engine.db.insert("desktop_handoffs", {"code_hash": token_hash(code), "user_id": user["id"],
                                              "challenge": state["desktop_challenge"], "created_at": now()})
        engine.audit(None, user["id"], "user.login.desktop_handoff", user["id"], {"method": "google"})
        return None, "handoff:" + code
    if user.get("mfa_enabled_at"):
        from ..platform import mfa
        return None, "mfa:" + mfa.new_challenge(engine, user["id"])
    token = new_token(32)
    ts = now()
    forget_dead_sessions(engine, user["id"])
    engine.db.insert("sessions", {"id": token_hash(token), "user_id": user["id"], "created_at": ts,
                                  "expires_at": session_expiry(engine), "last_seen": ts, "reauth_at": ts,
                                  "user_agent": request.headers.get("user-agent", "")[:200]})
    engine.db.execute("UPDATE users SET last_login_at = ? WHERE id = ?", (ts, user["id"]))
    engine.audit(None, user["id"], "user.login", user["id"], {"method": "google"})
    return token, "Signed in."


HANDOFF_SECONDS = 120


@router.get("/api/auth/desktop-handoff", auth=False)
async def desktop_handoff(ctx: Ctx) -> Any:
    """The desktop app redeems the one-time code from the browser, proving with the verifier that it started the
    sign-in. Single use, valid for two minutes; the session cookie is set in the app's own window."""
    import base64
    import hashlib

    from starlette.responses import RedirectResponse

    q = ctx.request.query_params
    code, verifier = q.get("code", ""), q.get("verifier", "")
    row = ctx.engine.db.one("SELECT * FROM desktop_handoffs WHERE code_hash = ?", (token_hash(code),)) if code else None
    expected = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    if (row is None or row["used_at"] or row["created_at"] < now() - HANDOFF_SECONDS
            or not verifier or not hmac.compare_digest(expected, row["challenge"])):
        raise Unauthorized("This sign-in link has expired or was not started from this app. Sign in again.",
                           code="handoff_invalid")
    ctx.engine.db.execute("UPDATE desktop_handoffs SET used_at = ? WHERE code_hash = ?", (now(), row["code_hash"]))
    ctx.engine.db.execute("DELETE FROM desktop_handoffs WHERE created_at < ?", (now() - 3600,))
    session = _start_session(ctx, row["user_id"])
    resp = RedirectResponse("/#/", status_code=303)
    for key, value in session.headers.items():
        if key.lower() == "set-cookie":
            resp.headers.append("set-cookie", value)
    return resp
