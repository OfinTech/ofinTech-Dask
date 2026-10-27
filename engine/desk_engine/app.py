"""Engine wiring and the ASGI application."""

from __future__ import annotations

import asyncio
import contextlib
import getpass
import logging
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, PlainTextResponse, Response
from starlette.routing import Route
from starlette.types import ASGIApp, Receive, Scope, Send

from . import agent as _agent_pkg  # noqa: F401  (registers tools)
from .agent.harness import Agent
from .agent.tools import ToolContext, registry
from .channels.service import Channels
from .config import VERSION, Settings
from .db import Database, dumps, new_id, now
from .events import EventHub
from .files.service import Files
from .knowledge.base import Knowledge
from .mail import Mailer
from .memory import Memory
from .llm.base import Usage
from .mcp_client import MCPManager
from .security import SecretBox, SecretStore
from .storage import build_storage
from .web import ROLES
from .workflows.engine import Workflows

log = logging.getLogger("desk")


async def _mcp_tools(ctx: ToolContext) -> list[Any]:
    return await ctx.engine.mcp.tools_for(ctx)


registry.provider(_mcp_tools)


class Engine:
    def __init__(self, settings: Settings, *, http_transport: Any = None, mcp_http_transport: Any = None) -> None:
        self.settings = settings
        self.db = Database(settings.db_path)
        self.secrets = SecretStore(self.db, SecretBox(settings.master_key))
        self.events = EventHub()
        self.tools = registry
        self.http_transport = http_transport
        self.mcp_http_transport = mcp_http_transport
        self.agent = Agent(self)
        self.workflows = Workflows(self)
        self.channels = Channels(self)
        self.mcp = MCPManager(self)
        self.knowledge = Knowledge(self)
        self.storage = build_storage(settings, transport=http_transport)
        self.files = Files(self)
        self.mailer = Mailer.from_env()
        self.memory = Memory(self)
        self.mcp_server_lifespan: Any = None
        self._background: list[asyncio.Task[None]] = []

    # -- start-up ------------------------------------------------------------------------------
    def bootstrap(self) -> None:
        self.settings.files_dir.mkdir(parents=True, exist_ok=True)
        self.db.migrate()
        if self.settings.is_desktop and not self.db.one("SELECT id FROM users WHERE is_local = 1"):
            try:
                name = getpass.getuser().title()
            except Exception:  # pragma: no cover
                name = "Me"
            self.db.insert("users", {"id": new_id("u_"), "email": "me@this-computer.local", "name": name or "Me",
                                     "password_hash": None, "is_local": 1, "created_at": now()})
        # runs that were mid-flight when the engine stopped
        self.db.execute("UPDATE agent_runs SET status = 'error', error = 'Interrupted by a restart.' "
                        "WHERE status = 'running'")

    async def start_background(self) -> None:
        info = await self.storage.check()
        log.info("file storage: %s %s", self.storage.describe(), info)
        await self.workflows.resume_interrupted()
        await self.knowledge.resume()
        self._background.append(asyncio.get_running_loop().create_task(self._scheduler()))

    async def stop_background(self) -> None:
        for t in self._background:
            t.cancel()
        for t in self._background:
            with contextlib.suppress(BaseException):
                await t
        await self.mcp.shutdown()
        self.db.close()

    async def _scheduler(self) -> None:
        tick = 0
        while True:
            try:
                await self.channels.poll_telegram_once()
                if tick % 5 == 0:
                    await self.workflows.tick()
                if tick % 100 == 0:
                    await self.workflows.poll_email()
                if tick % 1200 == 600:
                    await self._daily_syncs()
                if tick % 200 == 100:
                    await self.knowledge.sync_all_drive()
            except asyncio.CancelledError:
                raise
            except Exception:  # pragma: no cover - keep the scheduler alive
                log.exception("scheduler tick failed")
            tick += 1
            await asyncio.sleep(3)

    async def _daily_syncs(self) -> None:
        from .connectors.accounting import ACCOUNTING_PROVIDERS, is_push, sync_connection

        cutoff = now() - 24 * 3600
        for conn in self.db.all("SELECT * FROM connections WHERE status = 'connected'"):
            if conn["provider"] in ACCOUNTING_PROVIDERS and not is_push(conn) and (conn["last_sync_at"] or 0) < cutoff:
                try:
                    await sync_connection(self, conn)
                except Exception:  # pragma: no cover
                    log.exception("scheduled sync failed for %s", conn["id"])

    # -- shared services -----------------------------------------------------------------------
    def audit(self, workspace_id: str | None, user_id: str | None, action: str, target: str | None = None,
              detail: Any = None) -> None:
        self.db.insert("audit_log", {"workspace_id": workspace_id, "user_id": user_id, "action": action,
                                     "target": target, "detail": dumps(detail) if detail is not None else None,
                                     "created_at": now()})

    def record_usage(self, ws_id: str, provider_id: str, model: str, usage: Usage, source: str) -> None:
        self.db.insert("usage", {"workspace_id": ws_id, "provider_id": provider_id, "model": model,
                                 "input_tokens": usage.input_tokens, "output_tokens": usage.output_tokens,
                                 "cache_read": usage.cache_read, "cache_write": usage.cache_write,
                                 "source": source, "created_at": now()})

    def notify(self, ws_id: str, user_id: str | None, kind: str, title: str, link: str | None = None) -> None:
        if not user_id:
            return
        self.db.insert("notifications", {"id": new_id("n_"), "workspace_id": ws_id, "user_id": user_id, "kind": kind,
                                         "title": title[:300], "link": link, "created_at": now()})
        self.events.publish(f"user:{user_id}", {"type": "notification", "title": title[:300], "link": link})

    def notify_role(self, ws_id: str, min_role: str, kind: str, title: str, link: str | None = None,
                    exclude: str | None = None) -> None:
        allowed = ROLES[ROLES.index(min_role):]
        marks = ",".join("?" for _ in allowed)
        for m in self.db.all(f"SELECT user_id FROM members WHERE workspace_id = ? AND role IN ({marks})",
                             (ws_id, *allowed)):
            if m["user_id"] != exclude:
                self.notify(ws_id, m["user_id"], kind, title, link)

    async def on_agent_run_update(self, run: dict[str, Any]) -> None:
        from .db import loads

        parent = loads(run["state"], {}).get("parent")
        if not parent:
            return
        if parent.get("type") == "workflow_run":
            await self.workflows.on_agent_done(run, parent)
        elif parent.get("type") == "channel":
            await self.channels.on_agent_done(run, parent)


# --- HTTP layer ---------------------------------------------------------------------------------------


class SecurityHeaders:
    """Strict headers for every response; DNS-rebinding guard for the local desktop engine."""

    def __init__(self, app: ASGIApp, engine: Engine) -> None:
        self.app = app
        self.engine = engine

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        if self.engine.settings.is_desktop:
            host = dict(scope.get("headers", [])).get(b"host", b"").decode().split(":")[0]
            if host not in {"127.0.0.1", "localhost"}:
                await PlainTextResponse("Forbidden host", status_code=403)(scope, receive, send)
                return

        async def send_wrapper(message: dict[str, Any]) -> None:
            if message["type"] == "http.response.start":
                headers = message.setdefault("headers", [])
                extra = {
                    b"x-content-type-options": b"nosniff",
                    b"referrer-policy": b"no-referrer",
                    b"x-frame-options": b"DENY",
                    b"cross-origin-opener-policy": b"same-origin",
                    b"permissions-policy": b"camera=(), microphone=(), geolocation=()",
                    b"content-security-policy": (b"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
                                                 b"img-src 'self' data:; font-src 'self'; connect-src 'self'; "
                                                 b"frame-ancestors 'none'; base-uri 'none'; form-action 'self'; "
                                                 b"object-src 'none'"),
                }
                if self.engine.settings.public_url.startswith("https://"):
                    extra[b"strict-transport-security"] = b"max-age=31536000"
                present = {k.lower() for k, _ in headers}
                headers.extend((k, v) for k, v in extra.items() if k not in present)
            await send(message)

        await self.app(scope, receive, send_wrapper)


def create_app(settings: Settings, *, engine: Engine | None = None) -> Starlette:
    from .mcp_server import build_mcp_app
    from .routes import all_routes, public_routes

    engine = engine or Engine(settings)
    engine.bootstrap()
    mcp_app = build_mcp_app(engine)

    @contextlib.asynccontextmanager
    async def lifespan(app: Starlette) -> AsyncIterator[None]:
        async with contextlib.AsyncExitStack() as stack:
            if engine.mcp_server_lifespan is not None:
                await stack.enter_async_context(engine.mcp_server_lifespan)
            await engine.start_background()
            try:
                yield
            finally:
                await engine.stop_background()

    async def health(request: Request) -> Response:
        return JSONResponse({"status": "ok", "version": VERSION, "mode": settings.mode})

    routes: list[Any] = [Route("/health", health), *public_routes(), *all_routes(engine), Route("/mcp", endpoint=mcp_app)]
    ui_dir = settings.ui_dir
    if ui_dir and (ui_dir / "index.html").exists():
        routes.append(Route("/{path:path}", _ui_handler(ui_dir)))
    app = Starlette(routes=routes, lifespan=lifespan, middleware=[Middleware(SecurityHeaders, engine=engine)])
    app.state.engine = engine
    return app


def _ui_handler(ui_dir: Path) -> Any:
    root = ui_dir.resolve()

    async def serve(request: Request) -> Response:
        rel = request.path_params.get("path", "")
        if rel.startswith("api/"):
            return JSONResponse({"error": "not_found", "message": "Unknown API path."}, status_code=404)
        candidate = (root / rel).resolve()
        if rel and candidate.is_file() and root in candidate.parents:
            cache = "public, max-age=31536000, immutable" if "/assets/" in f"/{rel}" else "no-cache"
            return FileResponse(candidate, headers={"cache-control": cache})
        return FileResponse(root / "index.html", headers={"cache-control": "no-cache"})

    return serve
