"""HTTP surface of the embedded gateway.

Two routers: ``admin`` (``/api/w/{ws}/embedded/...``) uses the standard ``web.Router`` — a signed-in admin with a
fresh session manages integrations; ``public`` (``/api/embedded/...``) authenticates partner backends by
integration key and partner web pages by session token, with CORS for the integration's allowed origins only.
"""

from __future__ import annotations

import inspect
import logging
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import Route

from ..db import loads
from ..errors import AppError, Forbidden, Invalid
from ..web import Ctx, Router, parse_body
from . import agui
from .agents import AGENTS, CAPABILITIES
from .auth import EmbeddedPrincipal, cors_headers
from .model import DEFAULT_LIMITS, SESSION_TTL_DEFAULT, SESSION_TTL_MAX, SESSION_TTL_MIN, integration_out

if TYPE_CHECKING:
    from ..app import Engine
    from .gateway import Embedded

log = logging.getLogger("desk.embedded")

# ======================================================================================================================
# Admin API (inside Desk)
# ======================================================================================================================

admin = Router()


def _svc(ctx: Ctx) -> Embedded:
    return ctx.engine.embedded


def _not_sealed(ctx: Ctx) -> None:
    from ..egress import workspace_sealed
    if workspace_sealed(ctx.engine, ctx.ws_id):
        raise Forbidden("This workspace keeps its data inside the organization: embedded integrations are off.",
                        code="egress_blocked")


@admin.get("/api/w/{ws}/embedded/catalog", role="viewer")
async def catalog(ctx: Ctx) -> dict[str, Any]:
    return {"agents": [a.out() for a in AGENTS.values()], "capabilities": CAPABILITIES, "default_limits": DEFAULT_LIMITS,
            "endpoint": f"{(ctx.engine.settings.public_url or '').rstrip('/')}/api/embedded"}


@admin.get("/api/w/{ws}/embedded/integrations", role="admin")
async def list_integrations(ctx: Ctx) -> list[dict[str, Any]]:
    return [integration_out(r) for r in _svc(ctx).integrations.list(ctx.ws_id)]


class IntegrationIn(BaseModel):
    name: str = Field(min_length=1, max_length=60, pattern=r"^[\w .\-؀-ۿ]+$")
    allowed_origins: list[str] = Field(min_length=1, max_length=20)
    capabilities: list[str] = Field(min_length=1, max_length=10)
    limits: dict[str, int] | None = None


@admin.post("/api/w/{ws}/embedded/integrations", role="admin", body=IntegrationIn, fresh=True)
async def create_integration(ctx: Ctx) -> dict[str, Any]:
    _not_sealed(ctx)
    b: IntegrationIn = ctx.body
    row, key = _svc(ctx).integrations.create(ctx.ws_id, name=b.name.strip(), origins=b.allowed_origins,
                                             capabilities=b.capabilities, limits=b.limits, created_by=ctx.user_id)
    ctx.audit("embedded.integration.create", row["id"], {"name": row["name"], "origins": loads(row["allowed_origins"], []),
                                                          "capabilities": loads(row["capabilities"], []),
                                                          "service_user_id": row["service_user_id"]})
    return {**integration_out(row), "key": key, "note": "Copy the integration key now; it is not shown again. Keep it on "
                                                        "your server only — never in a web page or a mobile app."}


class IntegrationPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=60, pattern=r"^[\w .\-؀-ۿ]+$")
    status: str | None = Field(default=None, pattern=r"^(active|suspended)$")
    allowed_origins: list[str] | None = Field(default=None, min_length=1, max_length=20)
    capabilities: list[str] | None = Field(default=None, min_length=1, max_length=10)
    limits: dict[str, int] | None = None
    # no `role` field on purpose: the service user is a viewer and stays one


@admin.patch("/api/w/{ws}/embedded/integrations/{iid}", role="admin", body=IntegrationPatch, fresh=True)
async def patch_integration(ctx: Ctx) -> dict[str, Any]:
    b: IntegrationPatch = ctx.body
    iid = ctx.request.path_params["iid"]
    row = _svc(ctx).integrations.patch(ctx.ws_id, iid, name=b.name and b.name.strip(), status=b.status,
                                       origins=b.allowed_origins, capabilities=b.capabilities, limits=b.limits)
    ctx.audit("embedded.integration.update", iid, b.model_dump(exclude_none=True))
    return integration_out(row)


@admin.post("/api/w/{ws}/embedded/integrations/{iid}/rotate", role="admin", fresh=True)
async def rotate_integration(ctx: Ctx) -> dict[str, Any]:
    iid = ctx.request.path_params["iid"]
    row, key = _svc(ctx).integrations.rotate(ctx.ws_id, iid)
    ctx.audit("embedded.integration.rotate", iid)
    return {**integration_out(row), "key": key, "note": "Copy the new key now; sessions issued with the old key are closed."}


@admin.delete("/api/w/{ws}/embedded/integrations/{iid}", role="admin", fresh=True)
async def delete_integration(ctx: Ctx) -> None:
    iid = ctx.request.path_params["iid"]
    _svc(ctx).integrations.revoke(ctx.ws_id, iid)
    ctx.audit("embedded.integration.revoke", iid)


@admin.get("/api/w/{ws}/embedded/integrations/{iid}/sessions", role="admin")
async def list_sessions(ctx: Ctx) -> list[dict[str, Any]]:
    return _svc(ctx).integrations.sessions(ctx.ws_id, ctx.request.path_params["iid"])


@admin.delete("/api/w/{ws}/embedded/sessions/{sid}", role="admin")
async def revoke_session(ctx: Ctx) -> None:
    sid = ctx.request.path_params["sid"]
    _svc(ctx).integrations.revoke_session(ctx.ws_id, sid)
    ctx.audit("embedded.session.revoke", sid)


@admin.get("/api/w/{ws}/embedded/integrations/{iid}/usage", role="admin")
async def usage(ctx: Ctx) -> dict[str, Any]:
    iid = ctx.request.path_params["iid"]
    _svc(ctx).integrations.get(ctx.ws_id, iid)
    try:
        days = max(1, min(int(ctx.request.query_params.get("days", "30")), 365))
    except ValueError:
        raise Invalid("days must be a number.") from None
    return _svc(ctx).metering.summary(iid, days=days)


# ======================================================================================================================
# Public API (partner backend and partner web page)
# ======================================================================================================================

PublicHandler = Callable[[Request, "Engine"], Awaitable[Response] | Response]


def _public(engine: Engine, fn: PublicHandler) -> Callable[[Request], Awaitable[Response]]:
    async def endpoint(request: Request) -> Response:
        from ..audit import current_request_id, new_request_id

        request_id = new_request_id()
        request.state.request_id = request_id
        reset = current_request_id.set(request_id)
        origin = request.headers.get("origin")
        try:
            result = fn(request, engine)
            if inspect.isawaitable(result):
                result = await result
        except AppError as exc:
            result = JSONResponse(exc.to_dict(), status_code=exc.status)
            if exc.status == 401:
                result.headers["www-authenticate"] = "Bearer"
        except Exception:  # pragma: no cover - logged, generic message to the client
            log.exception("unhandled error on embedded %s %s", request.method, request.url.path)
            result = JSONResponse({"error": "internal", "message": "Something went wrong."}, status_code=500)
        finally:
            current_request_id.reset(reset)
        result.headers["x-request-id"] = request_id
        result.headers["cache-control"] = "no-store"
        # CORS only for an origin that some active integration allows (a failed request still needs the header for
        # the browser to show the error to the page's own code)
        if origin and engine.embedded.auth.preflight_origin(request):
            for k, v in cors_headers(origin.strip().rstrip("/").lower()).items():
                result.headers[k] = v
        return result

    endpoint.__name__ = fn.__name__
    return endpoint


async def _preflight(request: Request, engine: Engine) -> Response:
    origin = engine.embedded.auth.preflight_origin(request)
    if origin is None:
        return Response(status_code=403)
    return Response(status_code=204, headers=cors_headers(origin))


class SessionIn(BaseModel):
    subject: str = Field(min_length=1, max_length=200)
    label: str | None = Field(default=None, max_length=80)
    locale: str = Field(default="en", pattern=r"^(en|ar)$")
    ttl_seconds: int | None = Field(default=None, ge=SESSION_TTL_MIN, le=SESSION_TTL_MAX)


async def _issue_session(request: Request, engine: Engine) -> Response:
    svc = engine.embedded
    integration = svc.auth.integration_from_key(request)
    body: SessionIn = await parse_body(request, SessionIn)
    session, token = svc.integrations.issue_session(integration, subject=body.subject, label=body.label, locale=body.locale,
                                                    ttl_seconds=body.ttl_seconds)
    engine.audit(integration["workspace_id"], integration["service_user_id"], "embedded.session.issue", session["id"],
                 {"integration_id": integration["id"], "subject_hash": session["subject_hash"][:16],
                  "ttl_seconds": int(session["expires_at"] - session["created_at"])},
                 resource_type="embedded", decision="allowed", result="issued")
    return JSONResponse({"token": token, "expires_at": agui.iso(session["expires_at"]), "session_id": session["id"],
                         "agents": [a.out() for a in AGENTS.values() if a.capability in loads(integration["capabilities"], [])],
                         "protocol": {"name": "ag-ui", "ofintech_version": agui.PROTOCOL_VERSION}})


async def _agents(request: Request, engine: Engine) -> Response:
    principal = engine.embedded.auth.principal(request)
    return JSONResponse({"agents": [a.out() for a in AGENTS.values() if a.capability in principal.capabilities],
                         "locale": principal.session["locale"], "expires_at": agui.iso(principal.session["expires_at"])})


def _stream_response(body: Any, principal: EmbeddedPrincipal) -> StreamingResponse:
    headers = {"content-type": "text/event-stream", "cache-control": "no-store", "x-accel-buffering": "no",
               **cors_headers(principal.origin)}
    return StreamingResponse(body, headers=headers)


async def _run(request: Request, engine: Engine) -> Response:
    svc = engine.embedded
    principal = svc.auth.principal(request)
    agent = svc.agent_for(principal, request.path_params["agent_id"])
    if int(request.headers.get("content-length") or 0) > agui.MAX_BODY_BYTES:
        raise Invalid("The request body is too large.", code="body_too_large")
    inp: agui.RunAgentInput = await parse_body(request, agui.RunAgentInput)
    body = await svc.run(principal, agent, inp, getattr(request.state, "request_id", None))
    return _stream_response(body, principal)


async def _attach(request: Request, engine: Engine) -> Response:
    svc = engine.embedded
    principal = svc.auth.principal(request)
    q = request.query_params
    body = await svc.attach(principal, request.path_params["agent_id"], request.path_params["run_id"],
                            client_thread_id=(q.get("threadId") or "thread")[:200], client_run_id=(q.get("runId") or "run")[:200])
    return _stream_response(body, principal)


async def _cancel(request: Request, engine: Engine) -> Response:
    svc = engine.embedded
    principal = svc.auth.principal(request)
    svc.agent_for(principal, request.path_params["agent_id"])
    svc.cancel_run(principal, request.path_params["run_id"])
    return Response(status_code=204, headers=cors_headers(principal.origin))


def public_routes(engine: Engine) -> list[Route]:
    def r(path: str, fn: PublicHandler, methods: list[str]) -> Route:
        return Route(path, _public(engine, fn), methods=methods)

    return [
        r("/api/embedded/sessions", _issue_session, ["POST"]),
        r("/api/embedded/sessions", _preflight, ["OPTIONS"]),
        r("/api/embedded/agents", _agents, ["GET"]),
        r("/api/embedded/agents", _preflight, ["OPTIONS"]),
        r("/api/embedded/agents/{agent_id}/run", _run, ["POST"]),
        r("/api/embedded/agents/{agent_id}/run", _preflight, ["OPTIONS"]),
        r("/api/embedded/agents/{agent_id}/runs/{run_id}/events", _attach, ["GET"]),
        r("/api/embedded/agents/{agent_id}/runs/{run_id}/events", _preflight, ["OPTIONS"]),
        r("/api/embedded/agents/{agent_id}/runs/{run_id}/cancel", _cancel, ["POST"]),
        r("/api/embedded/agents/{agent_id}/runs/{run_id}/cancel", _preflight, ["OPTIONS"]),
    ]
