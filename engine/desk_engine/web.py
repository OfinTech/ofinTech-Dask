"""A small routing layer on Starlette: authentication, workspace roles, body validation and
uniform JSON errors for every endpoint."""

from __future__ import annotations

import hmac
import inspect
import re
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Annotated, Any

from pydantic import BaseModel, Field, ValidationError
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from .db import now
from .errors import AppError, Forbidden, Invalid, NotFound, Unauthorized
from .security import token_hash

if TYPE_CHECKING:
    from .app import Engine

log = logging.getLogger("desk.web")

ROLES = ["viewer", "editor", "admin", "owner"]
Email = Annotated[str, Field(pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$", max_length=254)]
SESSION_COOKIE = "desk_session"
# A session with no absolute cap gets this expiry (the year 33658), so one comparison covers both cases.
NO_EXPIRY = 1e12
ACTIVITY_WRITE_SECONDS = 60  # at most how often an active session writes last_seen


def is_activity(request: Request) -> bool:
    """Whether this request postpones the idle sign-out.

    Only a request that changes something counts, which includes the page's own heartbeat
    (``POST /api/session/ping``). Reading never counts: pages poll while a document is processed, a
    file review runs or a workflow run is open, and a tab left alone must still reach its sign-out.
    Deciding by method keeps that true for polls added later, which naming each one would not.
    """
    return request.method not in {"GET", "HEAD", "OPTIONS"}


def activity_write_seconds(idle_limit: float) -> float:
    """Short idle windows need last_seen written more often than once a minute to stay reachable."""
    return min(ACTIVITY_WRITE_SECONDS, idle_limit / 4) if idle_limit else ACTIVITY_WRITE_SECONDS


def session_expiry(engine: Engine) -> float:
    """When a session ends whatever the person does. ``session_days = 0`` means never."""
    days = engine.settings.session_days
    return now() + days * 86400 if days > 0 else NO_EXPIRY


def session_cookie_max_age(engine: Engine) -> int:
    """Longer than the idle window on purpose: the engine decides when a session ends, not the browser."""
    days = engine.settings.session_days
    return days * 86400 if days > 0 else 400 * 86400  # 400 days is the longest a browser keeps a cookie


def role_at_least(role: str, required: str) -> bool:
    return ROLES.index(role) >= ROLES.index(required)


_ID_SHAPE = re.compile(r"^[A-Za-z0-9_-]{1,40}$")


def _audit_denied(engine: Engine, request: Request, user: dict[str, Any] | None, role: str | None,
                  exc: AppError) -> None:
    """A 403 on any route: recorded against the workspace in the path when its id looks like one (a made-up id is
    not written into the log), with the route, the role it needed and the user who asked."""
    ws = request.path_params.get("ws") if role is not None else None
    ws_id = ws if ws and _ID_SHAPE.match(ws) else None
    try:
        engine.audit(ws_id, user["id"] if user else None, "access.denied", f"{request.method} {request.url.path}"[:200],
                     {"needed": role, "reason": getattr(exc, "code", None) or exc.message}, resource_type="route",
                     decision="denied", result="refused")
    except Exception:  # pragma: no cover - the refusal itself must still reach the client
        log.exception("could not audit a refusal")


@dataclass
class Ctx:
    request: Request
    engine: Engine
    user: dict[str, Any] | None
    workspace: dict[str, Any] | None = None
    role: str | None = None
    body: Any = None

    @property
    def ws_id(self) -> str:
        assert self.workspace is not None
        return self.workspace["id"]

    @property
    def user_id(self) -> str:
        assert self.user is not None
        return self.user["id"]

    def require(self, role: str) -> None:
        if self.role is None or not role_at_least(self.role, role):
            raise Forbidden(f"This needs the {role} role or higher.")

    def audit(self, action: str, target: str | None = None, detail: Any = None) -> None:
        self.engine.audit(self.workspace["id"] if self.workspace else None,
                          self.user["id"] if self.user else None, action, target, detail)


Handler = Callable[[Ctx], Awaitable[Any] | Any]


class Router:
    def __init__(self) -> None:
        self.routes: list[Route] = []
        self.engine: Engine | None = None

    def bind(self, engine: Engine) -> None:
        self.engine = engine

    def route(self, method: str, path: str, *, auth: bool = True, role: str | None = None,
              body: type[BaseModel] | None = None) -> Callable[[Handler], Handler]:
        """``role`` set means the path contains ``{ws}`` and the caller must be a member with at
        least that role."""

        def decorator(fn: Handler) -> Handler:
            async def endpoint(request: Request) -> Response:
                from .audit import current_request_id, new_request_id

                engine: Engine = request.app.state.engine
                request_id = new_request_id()
                request.state.request_id = request_id
                reset = current_request_id.set(request_id)
                user = None
                try:
                    user = authenticate(engine, request) if auth else optional_user(engine, request)
                    ctx = Ctx(request=request, engine=engine, user=user)
                    if role is not None:
                        load_workspace(ctx, request.path_params["ws"], role)
                    if body is not None:
                        ctx.body = await parse_body(request, body)
                    result = fn(ctx)
                    if inspect.isawaitable(result):
                        result = await result
                except AppError as exc:
                    if exc.status == 403:
                        # every refusal of a protected operation is a security event: who, where, what, why
                        _audit_denied(engine, request, user, role, exc)
                    return JSONResponse(exc.to_dict(), status_code=exc.status)
                except Exception:  # pragma: no cover - logged, generic message to the client
                    log.exception("unhandled error on %s %s", method, path)
                    return JSONResponse({"error": "internal", "message": "Something went wrong. "
                                         "The details are in the engine log."}, status_code=500)
                finally:
                    current_request_id.reset(reset)
                if isinstance(result, Response):
                    resp = result
                elif result is None:
                    resp = Response(status_code=204)
                else:
                    resp = JSONResponse(result)
                resp.headers["x-request-id"] = request_id
                return resp

            endpoint.__name__ = fn.__name__
            self.routes.append(Route(path, endpoint, methods=[method]))
            return fn

        return decorator

    def get(self, path: str, **kw: Any) -> Callable[[Handler], Handler]:
        return self.route("GET", path, **kw)

    def post(self, path: str, **kw: Any) -> Callable[[Handler], Handler]:
        return self.route("POST", path, **kw)

    def put(self, path: str, **kw: Any) -> Callable[[Handler], Handler]:
        return self.route("PUT", path, **kw)

    def patch(self, path: str, **kw: Any) -> Callable[[Handler], Handler]:
        return self.route("PATCH", path, **kw)

    def delete(self, path: str, **kw: Any) -> Callable[[Handler], Handler]:
        return self.route("DELETE", path, **kw)


class BodyTooLarge(Exception):
    """Raised by ``read_body`` when a request body exceeds its limit."""


async def read_body(request: Request, limit: int) -> bytes:
    """The whole request body, read in chunks and refused the moment it exceeds ``limit`` bytes.

    A ``Content-Length`` header is only a claim: a chunked request has none, and a wrong one is not checked by the
    server before the body is handed over. Reading through ``request.stream()`` keeps the limit true whatever the
    headers say, and stops reading as soon as it is passed, so a hostile body never sits in memory in full.
    """
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > limit:
        raise BodyTooLarge()
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > limit:
            raise BodyTooLarge()
        chunks.append(chunk)
    return b"".join(chunks)


MAX_JSON_BODY = 2 * 1024 * 1024


async def parse_body(request: Request, model: type[BaseModel]) -> BaseModel:
    try:
        raw = await read_body(request, MAX_JSON_BODY)
    except BodyTooLarge:
        raise Invalid("Request body is too large.") from None
    try:
        data = json.loads(raw or b"{}")
    except json.JSONDecodeError as exc:
        raise Invalid("Request body is not valid JSON.") from exc
    try:
        return model.model_validate(data)
    except ValidationError as exc:
        issues = [
            {"field": ".".join(str(p) for p in err["loc"]), "message": err["msg"]}
            for err in exc.errors(include_url=False)
        ]
        raise Invalid("Some fields are not valid.", details=issues) from exc


# --- authentication ---------------------------------------------------------------------------


def _bearer(request: Request) -> str | None:
    header = request.headers.get("authorization", "")
    if header.lower().startswith("bearer "):
        return header[7:].strip()
    return None


def optional_user(engine: Engine, request: Request) -> dict[str, Any] | None:
    try:
        return authenticate(engine, request)
    except Unauthorized:
        return None


def authenticate(engine: Engine, request: Request) -> dict[str, Any]:
    settings = engine.settings
    if settings.is_desktop:
        token = _bearer(request)
        if token and hmac.compare_digest(token, settings.local_token):
            user = engine.db.one("SELECT * FROM users WHERE is_local = 1 LIMIT 1")
            if user:
                return user
        raise Unauthorized("Not signed in.")

    token = _bearer(request)
    from_cookie = False
    if not token:
        token = request.cookies.get(SESSION_COOKIE)
        from_cookie = bool(token)
    if not token:
        raise Unauthorized("Not signed in.")
    if from_cookie and request.method not in {"GET", "HEAD", "OPTIONS"}:
        check_origin(engine, request)
    session = engine.db.one("SELECT * FROM sessions WHERE id = ?", (token_hash(token),))
    if session is None:
        raise Unauthorized("Your session has ended. Sign in again.")
    # A session that is over is deleted, not merely refused: nothing is left to steal or to reuse.
    idle_limit = settings.session_idle_minutes * 60
    idle = bool(idle_limit) and now() - session["last_seen"] > idle_limit
    if idle or session["expires_at"] < now():
        engine.db.execute("DELETE FROM sessions WHERE id = ?", (session["id"],))
        engine.audit(None, session["user_id"], "session.expired", None,
                     {"reason": "idle" if idle else "lifetime", "idle_minutes": settings.session_idle_minutes},
                     resource_type="session", decision="denied", result="signed_out")
        if idle:
            raise Unauthorized("You were signed out because nothing happened for a while. Sign in again.",
                               code="session_idle")
        raise Unauthorized("Your session has ended. Sign in again.")
    user = engine.db.one("SELECT * FROM users WHERE id = ? AND disabled = 0", (session["user_id"],))
    if user is None:
        raise Unauthorized("Your account is disabled.")
    if is_activity(request) and now() - session["last_seen"] > activity_write_seconds(idle_limit):
        engine.db.execute("UPDATE sessions SET last_seen = ? WHERE id = ?", (now(), session["id"]))
    return user


def session_row(engine: Engine, request: Request) -> dict[str, Any] | None:
    """The session row behind this request (cookie or bearer), or None in the desktop app."""
    token = request.cookies.get(SESSION_COOKIE) or request.headers.get("authorization", "")[7:]
    if not token or token == engine.settings.local_token:
        return None
    return engine.db.one("SELECT * FROM sessions WHERE id = ?", (token_hash(token),))


def request_origin(request: Request) -> str:
    """``scheme://host[:port]`` of the page that sent the request, from Origin or, failing that, Referer; empty when
    neither is present or parseable."""
    from urllib.parse import urlsplit

    for header in ("origin", "referer"):
        raw = request.headers.get(header) or ""
        if not raw or raw == "null":
            continue
        parts = urlsplit(raw)
        if parts.scheme in {"http", "https"} and parts.netloc:
            return f"{parts.scheme}://{parts.netloc}".lower()
    return ""


def check_origin(engine: Engine, request: Request) -> None:
    """Cookie-authenticated writes must come from the Team Server's own origin (CSRF guard).

    The comparison is on the whole origin, scheme, host and port, never on a prefix: ``https://desk.example.com``
    must not accept ``https://desk.example.com.attacker.net``.
    """
    from urllib.parse import urlsplit

    public = urlsplit(engine.settings.public_url)
    expected = f"{public.scheme}://{public.netloc}".lower()
    if not expected.endswith("://") and request_origin(request) == expected:
        return
    raise Forbidden("Request origin is not allowed.", code="bad_origin")


def load_workspace(ctx: Ctx, ws_id: str, role: str) -> None:
    assert ctx.user is not None
    row = ctx.engine.db.one(
        "SELECT w.*, m.role AS member_role FROM workspaces w JOIN members m ON m.workspace_id = w.id "
        "WHERE w.id = ? AND m.user_id = ?",
        (ws_id, ctx.user["id"]),
    )
    if row is None:
        if _ID_SHAPE.match(ws_id) and ctx.engine.db.one("SELECT 1 FROM workspaces WHERE id = ?", (ws_id,)):
            # a real workspace the caller is not a member of: a cross-tenant attempt, recorded against that tenant
            ctx.engine.audit(ws_id, ctx.user["id"], "access.denied", f"{ctx.request.method} {ctx.request.url.path}"[:200],
                             {"reason": "not_a_member"}, resource_type="workspace", decision="denied", result="refused")
        raise NotFound("Workspace not found.")
    ctx.role = row.pop("member_role")
    ctx.workspace = row
    ctx.require(role)


def public_user(user: dict[str, Any]) -> dict[str, Any]:
    return {"id": user["id"], "email": user["email"], "name": user["name"], "language": user.get("language")}
