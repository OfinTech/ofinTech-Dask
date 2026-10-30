"""OfinTech Desk as an MCP server (Streamable HTTP at ``/mcp``).

Other MCP clients (Claude Desktop, IDEs, other agents) can use the workspace's read-only finance
tools: calculators, datasets, dashboards data and accounting reports. Write tools are never
exposed here, so nothing outside the app can send messages or change records through this door.

Authentication: ``Authorization: Bearer <access key>``; keys are created per workspace member under
Settings → MCP access and are stored hashed.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from .agent.tools import ToolContext
from .config import VERSION
from .db import now
from .security import token_hash

if TYPE_CHECKING:
    from .app import Engine

EXPOSED_CATEGORIES = {"finance", "data", "accounting", "knowledge"}
# tools that change workspace state are not offered to outside clients
NOT_EXPOSED = {"create_dashboard", "sync_accounting"}


def resolve(engine: Engine, authorization: str) -> ToolContext | None:
    """Maps an access key to the member and workspace it belongs to."""
    key = authorization[7:].strip() if authorization.lower().startswith("bearer ") else ""
    if not key:
        return None
    row = engine.db.one("SELECT * FROM mcp_keys WHERE token_hash = ?", (token_hash(key),))
    if row is None:
        return None
    member = engine.db.one(
        "SELECT w.* FROM workspaces w JOIN members m ON m.workspace_id = w.id JOIN users u ON u.id = m.user_id "
        "WHERE w.id = ? AND m.user_id = ? AND u.disabled = 0", (row["workspace_id"], row["user_id"]))
    if member is None:
        return None
    if now() - (row["last_used_at"] or 0) > 300:
        engine.db.execute("UPDATE mcp_keys SET last_used_at = ? WHERE id = ?", (now(), row["id"]))
    return ToolContext(engine, member, row["user_id"], None, None, "mcp")


def _ctx_from_request(engine: Engine, ctx: Any) -> ToolContext:
    request = getattr(ctx, "request", None)
    header = request.headers.get("authorization", "") if request is not None else ""
    tctx = resolve(engine, header)
    if tctx is None:
        raise PermissionError("A valid OfinTech Desk MCP access key is required.")
    return tctx


def build_mcp_app(engine: Engine) -> ASGIApp:
    import mcp_types as types
    from mcp.server.lowlevel import Server
    from mcp.server.transport_security import TransportSecuritySettings

    from .egress import workspace_sealed

    async def list_tools(ctx: Any, params: Any) -> Any:
        tctx = _ctx_from_request(engine, ctx)
        if workspace_sealed(engine, tctx.workspace):   # an outside client must not read a sealed workspace
            return types.ListToolsResult(tools=[])
        tools = await engine.tools.for_context(tctx, categories=EXPOSED_CATEGORIES)
        return types.ListToolsResult(tools=[
            types.Tool(name=t.name, description=t.description, input_schema=t.parameters,
                       annotations=types.ToolAnnotations(read_only_hint=True))
            for t in tools.values() if t.effect == "read" and t.policy is None and t.name not in NOT_EXPOSED
        ])

    async def call_tool(ctx: Any, params: Any) -> Any:
        tctx = _ctx_from_request(engine, ctx)
        if workspace_sealed(engine, tctx.workspace):
            return types.CallToolResult(content=[types.TextContent(type="text", text="This workspace keeps its data "
                                        "inside the organization; its tools are not offered to outside clients.")],
                                        is_error=True)
        tools = await engine.tools.for_context(tctx, categories=EXPOSED_CATEGORIES)
        tool = tools.get(params.name)
        if tool is None or tool.effect != "read" or tool.policy is not None or tool.name in NOT_EXPOSED:
            return types.CallToolResult(content=[types.TextContent(type="text", text=f"Unknown tool {params.name}")],
                                        is_error=True)
        out = await tool.run(tctx, dict(params.arguments or {}))
        engine.audit(tctx.ws_id, tctx.user_id, "mcp_server.call", params.name, None)
        return types.CallToolResult(content=[types.TextContent(type="text", text=out.for_model(50000))],
                                    is_error=out.text.startswith("Error:"))

    server = Server("ofintech-desk", version=VERSION,
                    instructions="Finance and accounting tools over this OfinTech Desk workspace (read-only).",
                    on_list_tools=list_tools, on_call_tool=call_tool)
    hosts = ["127.0.0.1", "localhost", "127.0.0.1:*", "localhost:*"]
    origins = ["http://127.0.0.1", "http://localhost", "http://127.0.0.1:*", "http://localhost:*"]
    if engine.settings.public_url:
        from urllib.parse import urlparse

        u = urlparse(engine.settings.public_url)
        hosts.append(u.netloc)
        origins.append(f"{u.scheme}://{u.netloc}")
    inner = server.streamable_http_app(
        streamable_http_path="/mcp", stateless_http=True, json_response=True,
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=True, allowed_hosts=hosts,
                                                     allowed_origins=origins))
    engine.mcp_server_lifespan = inner.router.lifespan_context(inner)
    return _AuthWrapper(engine, inner)


class _AuthWrapper:
    def __init__(self, engine: Engine, app: ASGIApp) -> None:
        self.engine = engine
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
        if resolve(self.engine, headers.get("authorization", "")) is None:
            resp = JSONResponse({"error": "unauthorized", "message": "A valid OfinTech Desk MCP access key is required."},
                                status_code=401, headers={"WWW-Authenticate": "Bearer"})
            await resp(scope, receive, send)
            return
        await self.app(scope, receive, send)
