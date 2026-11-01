"""The security audit log: one writer for API routes, agent tools, approvals, sessions and background jobs.

Every entry carries an unpredictable event id, the UTC timestamp, a correlation id (the API request, the agent run
or the workflow run that caused it), the user and workspace, the action, the resource it touched, the authorization
decision, the approval it rests on, the outcome and a sanitized detail. Entries are append-only: migration 19 adds
database triggers that abort any UPDATE or DELETE, and no route exposes either. Reading needs the admin role of the
workspace (``GET /api/w/{ws}/audit``). Retention is the operator's: the log is part of the database and of every
encrypted backup; nothing in the engine prunes it.

Nothing secret is written. ``sanitize`` drops any key that looks like a credential, strips query strings from URLs,
masks bank identifiers, keeps only the first lines of an error and caps every string, so that a detail can be shown
in the Audit page as text without carrying a token, a password, a document or a payload large enough to hide one.
"""

from __future__ import annotations

import contextvars
import re
import secrets
from typing import Any

DECISIONS = ("allowed", "denied", "needs_approval", "approved", "rejected", "expired", "replayed", "error")

# keys whose values never reach the log, whatever the caller passed
_SECRET_FRAGMENTS = ("secret", "token", "password", "passwd", "api_key", "apikey", "authorization", "credential",
                     "cookie", "private", "session", "bearer", "signature")
_MAX_STRING = 500
_MAX_ITEMS = 40
_MAX_DEPTH = 4
_URL_QUERY = re.compile(r"(https?://[^\s?#]+)[?#][^\s]*")

# the request, agent run or workflow run currently being served; set by the router and the harness
current_request_id: contextvars.ContextVar[str | None] = contextvars.ContextVar("desk_request_id", default=None)


def new_event_id() -> str:
    return "ev_" + secrets.token_hex(12)


def new_request_id() -> str:
    return "req_" + secrets.token_hex(8)


def _secret_key(key: str) -> bool:
    k = key.lower().replace("-", "_")
    return any(f in k for f in _SECRET_FRAGMENTS)


def sanitize(value: Any, depth: int = 0) -> Any:
    """A copy of ``value`` fit for the audit log (see the module docstring)."""
    from .egress import mask_bank_identifiers

    if depth > _MAX_DEPTH:
        return "…"
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for i, (k, v) in enumerate(value.items()):
            if i >= _MAX_ITEMS:
                out["…"] = f"{len(value) - _MAX_ITEMS} more"
                break
            key = str(k)[:80]
            if _secret_key(key):
                continue
            out[key] = sanitize(v, depth + 1)
        return out
    if isinstance(value, (list, tuple, set)):
        items = list(value)
        out_list = [sanitize(v, depth + 1) for v in items[:_MAX_ITEMS]]
        if len(items) > _MAX_ITEMS:
            out_list.append(f"… {len(items) - _MAX_ITEMS} more")
        return out_list
    if isinstance(value, str):
        text = _URL_QUERY.sub(r"\1", value)
        text = mask_bank_identifiers(text)
        if len(text) > _MAX_STRING:
            text = text[:_MAX_STRING] + "…"
        return text
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return sanitize(str(value), depth)


def error_summary(exc: BaseException | str | None) -> str | None:
    """The first line of an error, type and message only: never a traceback, never a payload."""
    if exc is None:
        return None
    text = exc if isinstance(exc, str) else f"{type(exc).__name__}: {getattr(exc, 'message', None) or exc}"
    return sanitize(text.splitlines()[0] if text else "")
