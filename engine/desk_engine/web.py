"""A small routing layer on Starlette: authentication, workspace roles, body validation and
uniform JSON errors for every endpoint."""

from __future__ import annotations

import hmac
import inspect
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
# Requests a page makes by itself (count polling, event streams) carry this header. They prove the tab
# is open, not that anyone is at it, so they never postpone the idle sign-out.
BACKGROUND_HEADER = "x-desk-background"
ACTIVITY_WRITE_SECONDS = 60  # how often an active session writes last_seen


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
                engine: Engine = request.app.state.engine
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
                    return JSONResponse(exc.to_dict(), status_code=exc.status)
                except Exception:  # pragma: no cover - logged, generic message to the client
                    log.exception("unhandled error on %s %s", method, path)
                    return JSONResponse({"error": "internal", "message": "Something went wrong. "
                                         "The details are in the engine log."}, status_code=500)
                if isinstance(result, Response):
                    return result
                if result is None:
                    return Response(status_code=204)
                return JSONResponse(result)

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


async def parse_body(request: Request, model: type[BaseModel]) -> BaseModel:
    raw = await request.body()
    if len(raw) > 2 * 1024 * 1024:
        raise Invalid("Request body is too large.")
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
    if session is None or session["expires_at"] < now():
        raise Unauthorized("Your session has ended. Sign in again.")
    idle_limit = settings.session_idle_minutes * 60
    if idle_limit and now() - session["last_seen"] > idle_limit:
        engine.db.execute("DELETE FROM sessions WHERE id = ?", (session["id"],))
        raise Unauthorized("You were signed out because nothing happened for a while. Sign in again.",
                           code="session_idle")
    user = engine.db.one("SELECT * FROM users WHERE id = ? AND disabled = 0", (session["user_id"],))
    if user is None:
        raise Unauthorized("Your account is disabled.")
    if not request.headers.get(BACKGROUND_HEADER) and now() - session["last_seen"] > ACTIVITY_WRITE_SECONDS:
        engine.db.execute("UPDATE sessions SET last_seen = ? WHERE id = ?", (now(), session["id"]))
    return user


def check_origin(engine: Engine, request: Request) -> None:
    """Cookie-authenticated writes must come from the Team Server's own origin (CSRF guard)."""
    origin = request.headers.get("origin") or request.headers.get("referer") or ""
    expected = engine.settings.public_url.rstrip("/")
    if not origin.startswith(expected):
        raise Forbidden("Request origin is not allowed.", code="bad_origin")


def load_workspace(ctx: Ctx, ws_id: str, role: str) -> None:
    assert ctx.user is not None
    row = ctx.engine.db.one(
        "SELECT w.*, m.role AS member_role FROM workspaces w JOIN members m ON m.workspace_id = w.id "
        "WHERE w.id = ? AND m.user_id = ?",
        (ws_id, ctx.user["id"]),
    )
    if row is None:
        raise NotFound("Workspace not found.")
    ctx.role = row.pop("member_role")
    ctx.workspace = row
    ctx.require(role)


def public_user(user: dict[str, Any]) -> dict[str, Any]:
    return {"id": user["id"], "email": user["email"], "name": user["name"], "language": user.get("language")}
