"""Authentication of embedded requests, the origin allow-list and rate limits.

Every refusal becomes an ``embedded.denied`` audit entry and a response that does not say *why* the token was
refused (unknown, expired and revoked tokens all get the same 401), so the surface gives an attacker nothing to
enumerate. Workspace, acting user and role are read from the integration row — never from the request.
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from starlette.requests import Request

from ..db import loads
from ..errors import Forbidden, Unauthorized
from ..security import token_hash
from .model import DEFAULT_LIMITS, Integrations

if TYPE_CHECKING:
    from ..app import Engine

UNAUTH_RATE_PER_MINUTE = 60     # per client address, requests without a valid token
SESSIONS_RATE_PER_MINUTE = 60   # per integration key, session issuance

CORS_ALLOW_HEADERS = "authorization, content-type, accept, x-ofintech-sdk"
CORS_EXPOSE_HEADERS = "x-request-id"


@dataclass
class EmbeddedPrincipal:
    integration: dict[str, Any]
    session: dict[str, Any]
    workspace: dict[str, Any]
    user: dict[str, Any]          # the service user
    origin: str | None

    @property
    def limits(self) -> dict[str, int]:
        return {**DEFAULT_LIMITS, **loads(self.integration["limits"], {})}

    @property
    def capabilities(self) -> set[str]:
        return set(loads(self.integration["capabilities"], []))

    @property
    def origins(self) -> list[str]:
        return loads(self.integration["allowed_origins"], [])


class RateLimiter:
    """Sliding one-minute window per key, in memory (one engine process; see AR-1)."""

    def __init__(self) -> None:
        self._hits: dict[str, deque[float]] = {}

    def limited(self, key: str, per_minute: int) -> bool:
        if per_minute <= 0:
            return True
        q = self._hits.setdefault(key, deque())
        t = time.monotonic()
        while q and t - q[0] > 60:
            q.popleft()
        if len(q) >= per_minute:
            return True
        q.append(t)
        if len(self._hits) > 50_000:   # forget idle keys so the map cannot grow without bound
            for k in [k for k, v in self._hits.items() if not v or t - v[-1] > 60][:10_000]:
                self._hits.pop(k, None)
        return False


def _bearer(request: Request) -> str:
    header = request.headers.get("authorization", "")
    return header[7:].strip() if header.lower().startswith("bearer ") else ""


def _client(request: Request) -> str:
    return (request.client.host if request.client else None) or "-"


def cors_headers(origin: str | None) -> dict[str, str]:
    if not origin:
        return {}
    return {"access-control-allow-origin": origin, "vary": "Origin",
            "access-control-allow-headers": CORS_ALLOW_HEADERS, "access-control-allow-methods": "GET, POST, OPTIONS",
            "access-control-expose-headers": CORS_EXPOSE_HEADERS, "access-control-max-age": "600"}


class EmbeddedAuth:
    def __init__(self, engine: Engine, integrations: Integrations) -> None:
        self.engine = engine
        self.integrations = integrations
        self.limiter = RateLimiter()

    # -- audit -------------------------------------------------------------------------------------------------
    def denied(self, request: Request, reason: str, *, integration: dict[str, Any] | None = None,
               extra: dict[str, Any] | None = None) -> None:
        token = _bearer(request)
        detail = {"reason": reason, "path": request.url.path, "client": _client(request),
                  "origin": request.headers.get("origin"), **(extra or {})}
        if token:
            detail["token_hash_prefix"] = token_hash(token)[:12]
        self.engine.audit(integration["workspace_id"] if integration else None,
                          integration["service_user_id"] if integration else None,
                          "embedded.denied", integration["id"] if integration else None, detail,
                          resource_type="embedded", decision="denied", result="refused")

    # -- the partner backend: integration key ------------------------------------------------------------------
    def integration_from_key(self, request: Request) -> dict[str, Any]:
        if self.limiter.limited(f"anon:{_client(request)}", UNAUTH_RATE_PER_MINUTE):
            raise Unauthorized("Too many requests.", code="rate_limited", status=429)
        key = _bearer(request)
        row = self.integrations.by_key(key) if key else None
        if row is None:
            self.denied(request, "bad_integration_key")
            raise Unauthorized("A valid integration key is required.", code="unauthorized")
        if row["status"] != "active":
            self.denied(request, "integration_" + row["status"], integration=row)
            raise Forbidden("This integration is suspended.", code="integration_suspended")
        if self.limiter.limited(f"key:{row['id']}", SESSIONS_RATE_PER_MINUTE):
            raise Unauthorized("Too many session requests for this integration.", code="rate_limited", status=429)
        self._check_workspace(request, row)
        return row

    # -- the partner web page: session token -------------------------------------------------------------------
    def principal(self, request: Request, *, require_origin: bool = True) -> EmbeddedPrincipal:
        if self.limiter.limited(f"anon:{_client(request)}", UNAUTH_RATE_PER_MINUTE):
            raise Unauthorized("Too many requests.", code="rate_limited", status=429)
        token = _bearer(request)
        session = self.integrations.session_by_token(token) if token else None
        if session is None:
            self.denied(request, "bad_session_token")
            raise Unauthorized("A valid embedded session token is required.", code="unauthorized")
        integration = self.engine.db.one("SELECT * FROM embedded_integrations WHERE id = ?", (session["integration_id"],))
        if integration is None or integration["revoked_at"] is not None or integration["status"] != "active":
            self.denied(request, "integration_inactive", integration=integration)
            raise Unauthorized("A valid embedded session token is required.", code="unauthorized")
        origin = self.check_origin(request, integration, required=require_origin)
        workspace, user = self._check_workspace(request, integration)
        if self.limiter.limited(f"session:{session['id']}", int({**DEFAULT_LIMITS, **loads(integration["limits"], {})}["runs_per_minute_session"]) * 3):
            raise Unauthorized("Too many requests on this session.", code="rate_limited", status=429)
        # the limiter above is a hard ceiling per session; run-specific limits are applied by the gateway
        return EmbeddedPrincipal(integration, session, workspace, user, origin)

    def check_origin(self, request: Request, integration: dict[str, Any], *, required: bool) -> str | None:
        origin = request.headers.get("origin")
        allowed = loads(integration["allowed_origins"], [])
        if not origin:
            if required:
                self.denied(request, "origin_missing", integration=integration)
                raise Forbidden("Requests from a web page must carry an Origin header.", code="origin_required")
            return None
        if origin.strip().rstrip("/").lower() not in allowed:
            self.denied(request, "origin_not_allowed", integration=integration)
            raise Forbidden("This origin is not allowed for the integration.", code="origin_not_allowed")
        return origin.strip().rstrip("/").lower()

    def preflight_origin(self, request: Request) -> str | None:
        """For CORS preflights (no token yet): an origin that at least one active integration allows."""
        origin = (request.headers.get("origin") or "").strip().rstrip("/").lower()
        if not origin:
            return None
        for row in self.engine.db.all("SELECT allowed_origins FROM embedded_integrations WHERE status = 'active' AND revoked_at IS NULL"):
            if origin in loads(row["allowed_origins"], []):
                return origin
        return None

    def _check_workspace(self, request: Request, integration: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        from ..egress import workspace_sealed

        workspace = self.engine.db.one("SELECT * FROM workspaces WHERE id = ?", (integration["workspace_id"],))
        user = self.engine.db.one(
            "SELECT u.* FROM users u JOIN members m ON m.user_id = u.id WHERE u.id = ? AND m.workspace_id = ? AND u.disabled = 0",
            (integration["service_user_id"], integration["workspace_id"]))
        if workspace is None or user is None:
            self.denied(request, "service_user_missing", integration=integration)
            raise Forbidden("This integration can no longer act in its workspace.", code="integration_unbound")
        if workspace_sealed(self.engine, workspace):
            self.denied(request, "workspace_sealed", integration=integration)
            raise Forbidden("This workspace keeps its data inside the organization: embedded access is off.",
                            code="egress_blocked")
        return workspace, user
