"""Two-factor authentication with time-based one-time passwords (RFC 6238 / RFC 4226), standard library only.

Enrolment: the engine creates a 20-byte secret, keeps it encrypted in the ``secrets`` table and shows the person the
``otpauth://`` URI (and the base32 key) once; the enrolment is confirmed with a first valid code, so a mistyped
secret can never lock anyone out. Sign-in: a correct password answers with a short-lived *challenge* instead of a
session; the code redeems the challenge (``POST /api/auth/mfa/verify``), rate-limited and single-use. Recovery is
by the platform administrator (``reset``), who confirms their own identity first and whose action is audited.

The platform policy ``security.mfa_policy`` (none | admins | all) and the per-account restriction ``mfa_required``
decide who must enrol: such a person signs in but every request except enrolment is refused with 403
``mfa_enrollment_required`` until they have (``web.authenticate``).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct
import time
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

from ..db import loads, now
from ..errors import Forbidden, Invalid, Unauthorized
from ..security import new_token, token_hash

if TYPE_CHECKING:
    from ..app import Engine

STEP = 30
DIGITS = 6
WINDOW = 1          # one step either way covers clock drift
CHALLENGE_SECONDS = 300
MAX_ATTEMPTS = 5


def hotp(secret: bytes, counter: int) -> str:
    digest = hmac.new(secret, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    code = (struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF) % (10 ** DIGITS)
    return f"{code:0{DIGITS}d}"


def totp(secret: bytes, at: float | None = None) -> str:
    return hotp(secret, int((at if at is not None else time.time()) // STEP))


def verify_code(secret: bytes, code: str, at: float | None = None) -> bool:
    code = (code or "").strip().replace(" ", "")
    if not code.isdigit() or len(code) != DIGITS:
        return False
    counter = int((at if at is not None else time.time()) // STEP)
    return any(hmac.compare_digest(hotp(secret, counter + d), code) for d in range(-WINDOW, WINDOW + 1))


def otpauth_uri(secret: bytes, account: str, issuer: str) -> str:
    key = base64.b32encode(secret).decode().rstrip("=")
    return (f"otpauth://totp/{quote(issuer)}:{quote(account)}?secret={key}&issuer={quote(issuer)}"
            f"&algorithm=SHA1&digits={DIGITS}&period={STEP}")


# --- enrolment ------------------------------------------------------------------------------------------------------

def begin_enrollment(engine: Engine, user: dict[str, Any]) -> dict[str, Any]:
    """A fresh secret, stored but not yet enabled; the person confirms it with a first code."""
    from .settings import get_setting

    if user.get("mfa_enabled_at"):
        raise Invalid("Two-factor authentication is already on. Turn it off before enrolling again.", code="mfa_enabled")
    secret = secrets.token_bytes(20)
    sid = engine.secrets.put("mfa", {"secret": base64.b64encode(secret).decode()}, workspace_id=None,
                             secret_id=user.get("mfa_secret_id"))
    engine.db.update("users", user["id"], {"mfa_secret_id": sid})
    issuer = get_setting(engine, "platform.name") or "OfinTech Desk"
    return {"otpauth": otpauth_uri(secret, user["email"], issuer),
            "key": base64.b32encode(secret).decode().rstrip("="), "issuer": issuer}


def _secret_for(engine: Engine, user: dict[str, Any]) -> bytes | None:
    if not user.get("mfa_secret_id"):
        return None
    try:
        return base64.b64decode(engine.secrets.get(user["mfa_secret_id"], workspace_id=None)["secret"])
    except Exception:
        return None


def confirm_enrollment(engine: Engine, user: dict[str, Any], code: str) -> None:
    secret = _secret_for(engine, user)
    if secret is None:
        raise Invalid("Start enrolment first.", code="mfa_not_started")
    if not verify_code(secret, code):
        engine.audit(None, user["id"], "user.mfa.enroll.failed", None, None, resource_type="session", decision="denied",
                     result="refused")
        raise Invalid("That code is not right. Codes change every 30 seconds; try the current one.", code="bad_code")
    engine.db.update("users", user["id"], {"mfa_enabled_at": now()})
    engine.audit(None, user["id"], "user.mfa.enabled", user["id"], None, resource_type="user", decision="allowed", result="ok")


def disable(engine: Engine, user: dict[str, Any], *, code: str | None, by: str, admin: bool = False) -> None:
    if not admin:
        secret = _secret_for(engine, user)
        if secret is None or not verify_code(secret, code or ""):
            raise Invalid("Enter a current code to turn two-factor authentication off.", code="bad_code")
    with engine.db.tx() as c:
        c.execute("UPDATE users SET mfa_enabled_at = NULL, mfa_secret_id = NULL WHERE id = ?", (user["id"],))
        if user.get("mfa_secret_id"):
            c.execute("DELETE FROM secrets WHERE id = ?", (user["mfa_secret_id"],))
    engine.audit(None, by, "user.mfa.disabled", user["id"], {"by_admin": admin}, resource_type="user", decision="allowed",
                 result="ok")


# --- sign-in --------------------------------------------------------------------------------------------------------

def new_challenge(engine: Engine, user_id: str) -> str:
    token = new_token(32)
    engine.db.execute("DELETE FROM mfa_challenges WHERE created_at < ?", (now() - CHALLENGE_SECONDS,))
    engine.db.insert("mfa_challenges", {"token_hash": token_hash(token), "user_id": user_id, "created_at": now()})
    return token


def redeem_challenge(engine: Engine, challenge: str, code: str) -> str:
    """Returns the user id when the code is right; the challenge is consumed either way after too many tries."""
    row = engine.db.one("SELECT * FROM mfa_challenges WHERE token_hash = ? AND created_at > ?",
                        (token_hash(challenge or ""), now() - CHALLENGE_SECONDS))
    if row is None:
        raise Unauthorized("This sign-in has expired. Enter your password again.", code="mfa_expired")
    user = engine.db.one("SELECT * FROM users WHERE id = ? AND disabled = 0", (row["user_id"],))
    secret = _secret_for(engine, user) if user else None
    if user is None or secret is None or not verify_code(secret, code):
        attempts = row["attempts"] + 1
        if attempts >= MAX_ATTEMPTS:
            engine.db.execute("DELETE FROM mfa_challenges WHERE token_hash = ?", (row["token_hash"],))
        else:
            engine.db.execute("UPDATE mfa_challenges SET attempts = ? WHERE token_hash = ?", (attempts, row["token_hash"]))
        engine.audit(None, row["user_id"], "user.login.failed", None, {"reason": "bad_mfa_code"}, resource_type="session",
                     decision="denied", result="refused")
        raise Unauthorized("That code is not right.", code="bad_code")
    engine.db.execute("DELETE FROM mfa_challenges WHERE token_hash = ?", (row["token_hash"],))
    return user["id"]


def must_enroll(engine: Engine, user: dict[str, Any]) -> bool:
    """Whether policy requires this person to have two-factor authentication and they have not enrolled."""
    from .owner import platform_role
    from .settings import get_setting

    if user.get("mfa_enabled_at") or user.get("is_local"):
        return False
    if loads(user.get("restrictions"), {}).get("mfa_required"):
        return True
    policy = get_setting(engine, "security.mfa_policy")
    if policy == "all":
        return True
    if policy == "admins":
        if platform_role(engine, user["id"]):
            return True
        return bool(engine.db.one("SELECT 1 FROM members WHERE user_id = ? AND role IN ('admin','owner') LIMIT 1", (user["id"],)))
    return False


ENROLLMENT_PATHS = ("/api/session", "/api/auth/mfa", "/api/auth/logout", "/api/me", "/api/session/ping",
                    "/api/session/status", "/api/auth/reauth")


def gate(engine: Engine, user: dict[str, Any], path: str) -> None:
    """Refuse everything but enrolment while enrolment is required."""
    if path.startswith(ENROLLMENT_PATHS):
        return
    if must_enroll(engine, user):
        raise Forbidden("Set up two-factor authentication to continue.", code="mfa_enrollment_required")


def admin_reset(engine: Engine, *, user_id: str, by: str) -> None:
    user = engine.db.one("SELECT * FROM users WHERE id = ?", (user_id,))
    if user is None:
        raise Invalid("User not found.")
    disable(engine, user, code=None, by=by, admin=True)
    engine.db.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))

