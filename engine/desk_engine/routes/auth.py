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
                         "w.accounting_framework, w.timezone, m.role FROM workspaces w JOIN members m ON "
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
            "idle_minutes": 0 if engine.settings.is_desktop else engine.settings.session_idle_minutes,
            "site_url": engine.settings.site_url,
            "features": {"stdio_mcp": engine.settings.allow_stdio_mcp,
                         "whatsapp": engine.settings.public_url.startswith("https://"),
                         "team": not engine.settings.is_desktop,
                         "google_login": google_login_available(engine),
                         "email": engine.mailer.configured}}
    if user is None:
        return {**base, "user": None, "workspaces": []}
    return {**base, "user": public_user(user) | {"instance_admin": instance_admin(engine) == user["id"]},
            "workspaces": workspaces_for(engine, user["id"])}


class SignupIn(BaseModel):
    email: Email
    name: str = Field(min_length=1, max_length=120)
    password: str = Field(min_length=1, max_length=200)
    invite_token: str | None = None


def create_session(engine: Any, request: Any, user_id: str) -> str:
    token = new_token(32)
    ts = now()
    engine.db.insert("sessions", {"id": token_hash(token), "user_id": user_id, "created_at": ts,
                                  "expires_at": session_expiry(engine), "last_seen": ts,
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
    elif not (first or engine.settings.open_signup):
        raise Forbidden("Sign-up is by invitation. Ask a workspace administrator to invite you.", code="invitation_only")
    check_password_policy(password)
    if engine.db.one("SELECT 1 FROM users WHERE email = ?", (email.lower(),)):
        raise Conflict("An account with this e-mail already exists. Sign in instead.", code="exists")
    uid = new_id("u_")
    engine.db.insert("users", {"id": uid, "email": email.lower(), "name": name.strip(),
                               "password_hash": hash_password(password), "created_at": now()})
    if first:
        with engine.db.tx() as c:
            c.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('instance_admin', ?)", (dumps(uid),))
    if invite:
        _accept(engine, invite, uid)
    engine.audit(invite["workspace_id"] if invite else None, uid, "user.signup", uid, {"first": first})
    return uid


def login_user(engine: Any, *, email: str, password: str, client_host: str) -> str:
    """Checks the password (the JSON API and the website's form share this) and returns the user's id."""
    if engine.settings.is_desktop:
        raise Forbidden("The desktop app signs you in automatically.")
    _rate_limit(engine, f"login:{email.lower()}")
    _rate_limit(engine, f"login-ip:{client_host}", limit=30)
    user = engine.db.one("SELECT * FROM users WHERE email = ? AND is_local = 0", (email.lower(),))
    if not verify_password(password, user["password_hash"] if user else None) or user is None:
        raise Unauthorized("E-mail or password is wrong.", code="bad_credentials")
    if user["disabled"]:
        raise Forbidden("This account is disabled.", code="disabled")
    engine.audit(None, user["id"], "user.login", user["id"])
    return user["id"]


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
    body: LoginIn = ctx.body
    uid = login_user(ctx.engine, email=body.email, password=body.password,
                     client_host=ctx.request.client.host if ctx.request.client else "-")
    return _start_session(ctx, uid)


@router.post("/api/session/ping")
async def session_ping(ctx: Ctx) -> dict[str, Any]:
    """Sent by the page after the person has actually done something, to keep the session open."""
    return {"ok": True}


@router.post("/api/auth/logout")
async def logout(ctx: Ctx) -> Any:
    token = ctx.request.cookies.get(SESSION_COOKIE) or ctx.request.headers.get("authorization", "")[7:]
    if token:
        ctx.engine.db.execute("DELETE FROM sessions WHERE id = ?", (token_hash(token),))
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
    ctx.engine.db.execute("UPDATE users SET password_hash = ? WHERE id = ?", (hash_password(ctx.body.new), ctx.user_id))
    ctx.engine.db.execute("DELETE FROM sessions WHERE user_id = ?", (ctx.user_id,))
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
    user = engine.db.one("SELECT * FROM users WHERE email = ? AND is_local = 0 AND disabled = 0", (ctx.body.email,))
    if user and engine.mailer.configured:
        token = new_token(32)
        engine.db.insert("password_resets", {"token_hash": token_hash(token), "user_id": user["id"], "created_at": now(),
                                             "expires_at": now() + 3600})
        subject, text = reset_text(f"{engine.settings.public_url.rstrip('/')}/#/reset/{token}")
        await engine.mailer.send(user["email"], subject, text)
        engine.audit(None, user["id"], "user.password_reset_requested", user["id"])
    return {"ok": True, "email_configured": engine.mailer.configured,
            "message": "If an account exists for this e-mail, a reset link is on its way." if engine.mailer.configured
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
        c.execute("UPDATE users SET password_hash = ? WHERE id = ?", (hash_password(ctx.body.password), row["user_id"]))
        c.execute("DELETE FROM sessions WHERE user_id = ?", (row["user_id"],))
    ctx.engine.audit(None, row["user_id"], "user.password_reset", row["user_id"])
    return _start_session(ctx, row["user_id"])


@router.post("/api/admin/users/{user_id}/reset-link")
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


def _allowed_domain(email: str) -> bool:
    import os

    allowed = [d.strip().lower() for d in os.environ.get("DESK_GOOGLE_ALLOWED_DOMAINS", "").split(",") if d.strip()]
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
    if not _allowed_domain(email):
        return None, "Google sign-in on this server is limited to the organisation's e-mail domains."
    user = engine.db.one("SELECT * FROM users WHERE google_sub = ?", (sub,)) or \
        engine.db.one("SELECT * FROM users WHERE email = ? AND is_local = 0", (email,))
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
        if not (first or pending or engine.settings.open_signup):
            return None, "There is no account for this Google address. Ask a workspace administrator to invite you."
        uid = new_id("u_")
        engine.db.insert("users", {"id": uid, "email": email, "name": str(info.get("name") or email.split("@")[0])[:120],
                                   "password_hash": None, "google_sub": sub, "created_at": now()})
        if first:
            with engine.db.tx() as c:
                c.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('instance_admin', ?)", (dumps(uid),))
        engine.audit(None, uid, "user.signup", uid, {"method": "google", "first": first})
        user = engine.db.one("SELECT * FROM users WHERE id = ?", (uid,))
    else:
        if user["disabled"]:
            return None, "This account is disabled."
        if not user.get("google_sub"):
            engine.db.execute("UPDATE users SET google_sub = ? WHERE id = ?", (sub, user["id"]))
    for inv in pending:
        _accept(engine, inv, user["id"])
    if state.get("desktop_challenge"):  # signed in in the system browser for the desktop app: hand it over
        code = new_token(32)
        engine.db.insert("desktop_handoffs", {"code_hash": token_hash(code), "user_id": user["id"],
                                              "challenge": state["desktop_challenge"], "created_at": now()})
        engine.audit(None, user["id"], "user.login.desktop_handoff", user["id"], {"method": "google"})
        return None, "handoff:" + code
    token = new_token(32)
    ts = now()
    engine.db.insert("sessions", {"id": token_hash(token), "user_id": user["id"], "created_at": ts,
                                  "expires_at": session_expiry(engine), "last_seen": ts,
                                  "user_agent": request.headers.get("user-agent", "")[:200]})
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
