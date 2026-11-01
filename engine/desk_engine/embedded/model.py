"""Tables of the embedded gateway: integrations, sessions, threads, usage. Keys and tokens are generated here and
stored as SHA-256 hashes only; the plain value is returned once to the caller and never written anywhere."""

from __future__ import annotations

import hashlib
import json
import re
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

from ..db import dumps, loads, new_id, now
from ..errors import Conflict, Invalid, NotFound
from ..security import new_token, token_hash
from .agents import CAPABILITIES

if TYPE_CHECKING:
    from ..app import Engine

KEY_PREFIX = "oei_"       # integration key (partner backend)
SESSION_PREFIX = "oes_"   # end-user session token (partner web page)

SESSION_TTL_DEFAULT = 15 * 60
SESSION_TTL_MAX = 60 * 60
SESSION_TTL_MIN = 60

MAX_INTEGRATIONS_PER_WORKSPACE = 20
MAX_ORIGINS = 20

DEFAULT_LIMITS: dict[str, int] = {
    "runs_per_minute": 20,       # per integration
    "runs_per_minute_session": 6,
    "runs_per_day": 2000,
    "tokens_per_day": 2_000_000,
    "max_input_chars": 8000,
}

_ORIGIN_RE = re.compile(r"^https?://[A-Za-z0-9.-]+(:\d{1,5})?$")


def subject_hash(integration_id: str, subject: str) -> str:
    """Partner subjects are opaque identifiers; only a salted hash is stored (salted by the integration so the
    same person at two partners is not linkable)."""
    return hashlib.sha256(f"{integration_id}|{subject}".encode()).hexdigest()


def normalize_origin(raw: str) -> str:
    value = raw.strip().rstrip("/")
    if not _ORIGIN_RE.match(value):
        raise Invalid(f"Origin must look like https://app.example.com (got {value[:60]!r}).", code="bad_origin")
    parts = urlsplit(value)
    if parts.scheme != "https" and parts.hostname not in {"localhost", "127.0.0.1"}:
        raise Invalid("Origins must use https (http is allowed for localhost only).", code="bad_origin")
    return f"{parts.scheme}://{parts.hostname}{':' + str(parts.port) if parts.port else ''}".lower()


def check_capabilities(caps: list[str]) -> list[str]:
    unknown = [c for c in caps if c not in CAPABILITIES]
    if unknown:
        raise Invalid("Unknown capabilities: " + ", ".join(unknown) + ". Known: " + ", ".join(CAPABILITIES), code="bad_capability")
    return sorted(set(caps))


def check_limits(raw: dict[str, Any] | None) -> dict[str, int]:
    out = dict(DEFAULT_LIMITS)
    for key, value in (raw or {}).items():
        if key not in DEFAULT_LIMITS:
            raise Invalid(f"Unknown limit {key}. Known: " + ", ".join(DEFAULT_LIMITS), code="bad_limit")
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise Invalid(f"Limit {key} must be a non-negative integer.", code="bad_limit")
        out[key] = value
    # the input cap can be lowered per integration, never raised above the gateway's own ceiling
    out["max_input_chars"] = min(out["max_input_chars"], DEFAULT_LIMITS["max_input_chars"]) or DEFAULT_LIMITS["max_input_chars"]
    return out


def integration_out(row: dict[str, Any]) -> dict[str, Any]:
    return {"id": row["id"], "workspace_id": row["workspace_id"], "name": row["name"], "status": row["status"],
            "service_user_id": row["service_user_id"], "key_prefix": row["key_prefix"],
            "allowed_origins": loads(row["allowed_origins"], []), "capabilities": loads(row["capabilities"], []),
            "limits": loads(row["limits"], {}), "created_by": row["created_by"], "created_at": row["created_at"],
            "last_used_at": row["last_used_at"], "rotated_at": row["rotated_at"]}


class Integrations:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    # -- registration (admin API) -------------------------------------------------------------------------------
    def create(self, workspace_id: str, *, name: str, origins: list[str], capabilities: list[str],
               limits: dict[str, Any] | None, created_by: str) -> tuple[dict[str, Any], str]:
        db = self.engine.db
        if db.scalar("SELECT COUNT(*) FROM embedded_integrations WHERE workspace_id = ? AND revoked_at IS NULL",
                     (workspace_id,)) >= MAX_INTEGRATIONS_PER_WORKSPACE:
            raise Invalid(f"This workspace has {MAX_INTEGRATIONS_PER_WORKSPACE} integrations; remove one first.")
        if db.one("SELECT 1 FROM embedded_integrations WHERE workspace_id = ? AND name = ? AND revoked_at IS NULL",
                  (workspace_id, name)):
            raise Conflict("An integration with this name exists.")
        if len(origins) > MAX_ORIGINS:
            raise Invalid(f"At most {MAX_ORIGINS} origins.")
        clean_origins = sorted({normalize_origin(o) for o in origins})
        if not clean_origins:
            raise Invalid("At least one allowed origin is required.", code="bad_origin")
        caps = check_capabilities(capabilities)
        if not caps:
            raise Invalid("At least one capability is required.", code="bad_capability")
        lim = check_limits(limits)
        iid = new_id("int_")
        key = KEY_PREFIX + new_token(32)
        ts = now()
        service_user = new_id("u_")
        with db.tx() as c:
            # the service user: a member of this one workspace with the least role; it cannot sign in (no password,
            # no Google identity) and exists so that every tool call, audit entry and approval names the integration
            c.execute("INSERT INTO users (id, email, name, password_hash, is_local, created_at) VALUES (?,?,?,?,?,?)",
                      (service_user, f"embedded+{iid}@integrations.ofintech.local", f"Integration: {name}"[:80], None, 0, ts))
            c.execute("INSERT INTO members (workspace_id, user_id, role, created_at) VALUES (?,?,?,?)",
                      (workspace_id, service_user, "viewer", ts))
            c.execute("INSERT INTO embedded_integrations (id, workspace_id, name, status, service_user_id, key_hash, "
                      "key_prefix, allowed_origins, capabilities, limits, created_by, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                      (iid, workspace_id, name, "active", service_user, token_hash(key), key[:12], dumps(clean_origins),
                       dumps(caps), dumps(lim), created_by, ts))
        return self.get(workspace_id, iid), key

    def get(self, workspace_id: str, iid: str) -> dict[str, Any]:
        row = self.engine.db.one("SELECT * FROM embedded_integrations WHERE id = ? AND workspace_id = ? AND revoked_at IS NULL",
                                 (iid, workspace_id))
        if row is None:
            raise NotFound("Integration not found.")
        return row

    def list(self, workspace_id: str) -> list[dict[str, Any]]:
        return self.engine.db.all("SELECT * FROM embedded_integrations WHERE workspace_id = ? AND revoked_at IS NULL "
                                  "ORDER BY created_at DESC", (workspace_id,))

    def patch(self, workspace_id: str, iid: str, *, name: str | None = None, status: str | None = None,
              origins: list[str] | None = None, capabilities: list[str] | None = None,
              limits: dict[str, Any] | None = None) -> dict[str, Any]:
        row = self.get(workspace_id, iid)
        values: dict[str, Any] = {}
        if name is not None:
            values["name"] = name
        if status is not None:
            if status not in {"active", "suspended"}:
                raise Invalid("status must be active or suspended.")
            values["status"] = status
        if origins is not None:
            if len(origins) > MAX_ORIGINS:
                raise Invalid(f"At most {MAX_ORIGINS} origins.")
            clean = sorted({normalize_origin(o) for o in origins})
            if not clean:
                raise Invalid("At least one allowed origin is required.", code="bad_origin")
            values["allowed_origins"] = dumps(clean)
        if capabilities is not None:
            caps = check_capabilities(capabilities)
            if not caps:
                raise Invalid("At least one capability is required.", code="bad_capability")
            values["capabilities"] = dumps(caps)
        if limits is not None:
            values["limits"] = dumps(check_limits({**loads(row["limits"], {}), **limits}))
        if values:
            self.engine.db.update("embedded_integrations", iid, values, workspace_id=workspace_id)
        if status == "suspended":
            self.engine.db.execute("UPDATE embedded_sessions SET revoked_at = ? WHERE integration_id = ? AND revoked_at IS NULL",
                                   (now(), iid))
        return self.get(workspace_id, iid)

    def rotate(self, workspace_id: str, iid: str) -> tuple[dict[str, Any], str]:
        self.get(workspace_id, iid)
        key = KEY_PREFIX + new_token(32)
        ts = now()
        with self.engine.db.tx() as c:
            c.execute("UPDATE embedded_integrations SET key_hash = ?, key_prefix = ?, rotated_at = ? WHERE id = ?",
                      (token_hash(key), key[:12], ts, iid))
            # sessions minted with the old key stop too: a rotation is what an operator does after a suspected leak
            c.execute("UPDATE embedded_sessions SET revoked_at = ? WHERE integration_id = ? AND revoked_at IS NULL", (ts, iid))
        return self.get(workspace_id, iid), key

    def revoke(self, workspace_id: str, iid: str) -> None:
        row = self.get(workspace_id, iid)
        ts = now()
        with self.engine.db.tx() as c:
            c.execute("UPDATE embedded_integrations SET status = 'revoked', revoked_at = ?, key_hash = ? WHERE id = ?",
                      (ts, f"revoked:{iid}:{ts}", iid))
            c.execute("UPDATE embedded_sessions SET revoked_at = ? WHERE integration_id = ? AND revoked_at IS NULL", (ts, iid))
            # the service user keeps its row (audit entries point at it) but leaves the workspace
            c.execute("DELETE FROM members WHERE workspace_id = ? AND user_id = ?", (workspace_id, row["service_user_id"]))
            c.execute("UPDATE users SET disabled = 1 WHERE id = ?", (row["service_user_id"],))

    # -- resolution (public API) ----------------------------------------------------------------------------------
    def by_key(self, key: str) -> dict[str, Any] | None:
        if not key.startswith(KEY_PREFIX):
            return None
        row = self.engine.db.one("SELECT * FROM embedded_integrations WHERE key_hash = ?", (token_hash(key),))
        if row is None or row["revoked_at"] is not None:
            return None
        return row

    # -- sessions ------------------------------------------------------------------------------------------------
    def issue_session(self, integration: dict[str, Any], *, subject: str, label: str | None, locale: str,
                      ttl_seconds: int | None) -> tuple[dict[str, Any], str]:
        if not subject or len(subject) > 200:
            raise Invalid("subject is required (1–200 characters).", code="bad_subject")
        ttl = SESSION_TTL_DEFAULT if ttl_seconds is None else int(ttl_seconds)
        if ttl < SESSION_TTL_MIN or ttl > SESSION_TTL_MAX:
            raise Invalid(f"ttl_seconds must be between {SESSION_TTL_MIN} and {SESSION_TTL_MAX}.", code="bad_ttl")
        if locale not in {"en", "ar"}:
            raise Invalid("locale must be en or ar.", code="bad_locale")
        token = SESSION_PREFIX + new_token(32)
        sid = new_id("es_")
        ts = now()
        self.engine.db.insert("embedded_sessions", {
            "id": sid, "integration_id": integration["id"], "token_hash": token_hash(token),
            "subject_hash": subject_hash(integration["id"], subject),
            "subject_label": (label or "")[:80] or None, "locale": locale, "created_at": ts, "expires_at": ts + ttl})
        self.engine.db.execute("UPDATE embedded_integrations SET last_used_at = ? WHERE id = ?", (ts, integration["id"]))
        return self.engine.db.one("SELECT * FROM embedded_sessions WHERE id = ?", (sid,)), token

    def session_by_token(self, token: str) -> dict[str, Any] | None:
        """The live session behind a bearer token, or None for anything that is not a live session (unknown,
        expired, revoked). Callers must not tell these apart to the client."""
        if not token.startswith(SESSION_PREFIX):
            return None
        row = self.engine.db.one("SELECT * FROM embedded_sessions WHERE token_hash = ?", (token_hash(token),))
        if row is None or row["revoked_at"] is not None or row["expires_at"] < now():
            return None
        if now() - (row["last_used_at"] or 0) > 60:
            self.engine.db.execute("UPDATE embedded_sessions SET last_used_at = ? WHERE id = ?", (now(), row["id"]))
        return row

    def revoke_session(self, workspace_id: str, session_id: str) -> None:
        row = self.engine.db.one("SELECT s.id FROM embedded_sessions s JOIN embedded_integrations i ON i.id = s.integration_id "
                                 "WHERE s.id = ? AND i.workspace_id = ?", (session_id, workspace_id))
        if row is None:
            raise NotFound("Session not found.")
        self.engine.db.execute("UPDATE embedded_sessions SET revoked_at = ? WHERE id = ? AND revoked_at IS NULL", (now(), session_id))

    def sessions(self, workspace_id: str, iid: str) -> list[dict[str, Any]]:
        self.get(workspace_id, iid)
        return self.engine.db.all("SELECT id, subject_hash, subject_label, locale, created_at, expires_at, revoked_at, "
                                  "last_used_at FROM embedded_sessions WHERE integration_id = ? ORDER BY created_at DESC LIMIT 200",
                                  (iid,))

    # -- threads -------------------------------------------------------------------------------------------------
    def thread_for(self, integration: dict[str, Any], session: dict[str, Any], client_thread_id: str) -> dict[str, Any]:
        """The Desk thread behind a partner thread id, created on first use. Keyed by integration and subject, so a
        client thread id can never address a thread of another tenant or another end user."""
        db = self.engine.db
        key = (integration["id"], session["subject_hash"], client_thread_id)
        link = db.one("SELECT thread_id FROM embedded_threads WHERE integration_id = ? AND subject_hash = ? AND client_thread_id = ?", key)
        if link:
            t = db.one("SELECT * FROM threads WHERE id = ? AND workspace_id = ?", (link["thread_id"], integration["workspace_id"]))
            if t is not None:
                return t
        tid = new_id("t_")
        ts = now()
        with db.tx() as c:
            c.execute("INSERT INTO threads (id, workspace_id, title, created_by, visibility, skills, channel, created_at, updated_at) "
                      "VALUES (?,?,?,?,?,?,?,?,?)",
                      (tid, integration["workspace_id"], f"Embedded · {integration['name']}"[:80], integration["service_user_id"],
                       "private", "[]", json.dumps({"type": "embedded", "integration_id": integration["id"],
                                                    "subject_hash": session["subject_hash"][:16]}), ts, ts))
            c.execute("INSERT OR REPLACE INTO embedded_threads (integration_id, subject_hash, client_thread_id, thread_id, created_at) "
                      "VALUES (?,?,?,?,?)", (*key, tid, ts))
        return db.one("SELECT * FROM threads WHERE id = ?", (tid,))

    def owns_run(self, integration: dict[str, Any], session: dict[str, Any], run_id: str) -> dict[str, Any] | None:
        """The agent run, only if it belongs to a thread of this integration *and* this subject."""
        return self.engine.db.one(
            "SELECT r.* FROM agent_runs r JOIN embedded_threads e ON e.thread_id = r.thread_id "
            "WHERE r.id = ? AND e.integration_id = ? AND e.subject_hash = ? AND r.workspace_id = ?",
            (run_id, integration["id"], session["subject_hash"], integration["workspace_id"]))
