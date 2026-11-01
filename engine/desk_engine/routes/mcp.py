"""MCP servers the agent may use, and access keys for using this workspace as an MCP server."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from ..db import dumps, loads, new_id, now
from ..errors import Conflict, Forbidden, Invalid, NotFound
from ..mcp_client import command_preview, tool_prefix, validate_server_input
from ..security import new_token, token_hash
from ..web import Ctx, Router

router = Router()


def public_url(url: str | None) -> str | None:
    """A URL as it may appear in the audit log and in outgoing webhooks: scheme, host and path only. Query strings
    sometimes carry access tokens, and the audit log is readable by every admin and forwarded to webhook receivers."""
    from urllib.parse import urlsplit

    if not url:
        return url
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.hostname or ''}{':' + str(parts.port) if parts.port else ''}{parts.path}"


def _out(row: dict[str, Any]) -> dict[str, Any]:
    out = {k: row[k] for k in ("id", "name", "transport", "command", "url", "default_policy", "last_error", "created_at")}
    out["enabled"] = bool(row["enabled"])
    out["args"] = loads(row["args"], [])
    out["tool_policy"] = loads(row["tool_policy"], {})
    out["tools"] = loads(row["tools_cache"], [])
    out["has_secret"] = bool(row["secret_id"])
    out["tool_prefix"] = tool_prefix(row["name"])
    if row["transport"] == "stdio":
        out["command_preview"] = command_preview(row["command"] or "", out["args"])
    return out


def _server(ctx: Ctx) -> dict[str, Any]:
    row = ctx.engine.db.one("SELECT * FROM mcp_servers WHERE id = ? AND workspace_id = ?",
                            (ctx.request.path_params["sid"], ctx.ws_id))
    if row is None:
        raise NotFound("MCP server not found.")
    return row


def _not_sealed(ctx: Ctx) -> None:
    from ..egress import workspace_sealed
    if workspace_sealed(ctx.engine, ctx.ws_id):
        raise Forbidden("This workspace keeps its data inside the organization: MCP servers and access keys for "
                        "outside clients are off.", code="egress_blocked")


@router.get("/api/w/{ws}/mcp-servers", role="viewer")
async def list_servers(ctx: Ctx) -> dict[str, Any]:
    rows = ctx.engine.db.all("SELECT * FROM mcp_servers WHERE workspace_id = ? ORDER BY name", (ctx.ws_id,))
    return {"servers": [_out(r) for r in rows], "stdio_allowed": ctx.engine.settings.allow_stdio_mcp,
            "endpoint": f"{(ctx.engine.settings.public_url or '').rstrip('/')}/mcp"}


class ServerIn(BaseModel):
    name: str = Field(min_length=1, max_length=40, pattern=r"^[A-Za-z0-9 _.-]+$")
    transport: Literal["http", "stdio"]
    url: str | None = Field(default=None, max_length=500)
    command: str | None = Field(default=None, max_length=300)
    args: list[str] = Field(default_factory=list, max_length=40)
    headers: dict[str, str] = Field(default_factory=dict)
    env: dict[str, str] = Field(default_factory=dict)
    default_policy: Literal["ask", "auto", "off"] = "ask"


@router.post("/api/w/{ws}/mcp-servers", role="admin", body=ServerIn)
async def add_server(ctx: Ctx) -> dict[str, Any]:
    _not_sealed(ctx)
    b: ServerIn = ctx.body
    validate_server_input(ctx.engine, b.transport, b.command, b.args, b.url)
    if ctx.engine.db.one("SELECT 1 FROM mcp_servers WHERE workspace_id = ? AND name = ?", (ctx.ws_id, b.name)):
        raise Conflict("An MCP server with this name exists.")
    sid = new_id("mcp_")
    secret_id = None
    if b.headers or b.env:
        secret_id = ctx.engine.secrets.put("mcp", {"headers": b.headers, "env": b.env}, workspace_id=ctx.ws_id,
                                           secret_id=f"sec_{sid}")
    ctx.engine.db.insert("mcp_servers", {"id": sid, "workspace_id": ctx.ws_id, "name": b.name.strip(),
                                         "transport": b.transport, "command": b.command, "args": dumps(b.args),
                                         "url": b.url, "secret_id": secret_id, "default_policy": b.default_policy,
                                         "created_by": ctx.user_id, "created_at": now()})
    ctx.audit("mcp.add", sid, {"transport": b.transport, "url": public_url(b.url),
                               "command": command_preview(b.command, b.args) if b.command else None})
    try:
        await ctx.engine.mcp.refresh_tools(ctx.ws_id, sid)
    except Exception:  # saved anyway; the error is shown on the server card
        pass
    return _out(_server_by_id(ctx, sid))


def _server_by_id(ctx: Ctx, sid: str) -> dict[str, Any]:
    row = ctx.engine.db.one("SELECT * FROM mcp_servers WHERE id = ? AND workspace_id = ?", (sid, ctx.ws_id))
    if row is None:
        raise NotFound("MCP server not found.")
    return row


class ServerPatch(BaseModel):
    enabled: bool | None = None
    default_policy: Literal["ask", "auto", "off"] | None = None
    tool_policy: dict[str, Literal["ask", "auto", "off"]] | None = None
    headers: dict[str, str] | None = None
    env: dict[str, str] | None = None


@router.patch("/api/w/{ws}/mcp-servers/{sid}", role="admin", body=ServerPatch)
async def patch_server(ctx: Ctx) -> dict[str, Any]:
    row = _server(ctx)
    b: ServerPatch = ctx.body
    values: dict[str, Any] = {}
    if b.enabled is not None:
        values["enabled"] = int(b.enabled)
    if b.default_policy:
        values["default_policy"] = b.default_policy
    if b.tool_policy is not None:
        values["tool_policy"] = dumps(b.tool_policy)
    if b.headers is not None or b.env is not None:
        current = ctx.engine.secrets.get(row["secret_id"], workspace_id=ctx.ws_id) if row["secret_id"] else {}
        values["secret_id"] = ctx.engine.secrets.put("mcp", {"headers": b.headers if b.headers is not None else current.get("headers", {}),
                                                             "env": b.env if b.env is not None else current.get("env", {})},
                                                     workspace_id=ctx.ws_id, secret_id=row["secret_id"] or f"sec_{row['id']}")
        await ctx.engine.mcp.stop(row["id"])
    if values:
        ctx.engine.db.update("mcp_servers", row["id"], values, workspace_id=ctx.ws_id)
        ctx.audit("mcp.update", row["id"], {k: v for k, v in values.items() if k != "secret_id"})
    return _out(_server(ctx))


@router.post("/api/w/{ws}/mcp-servers/{sid}/refresh", role="admin")
async def refresh(ctx: Ctx) -> dict[str, Any]:
    _not_sealed(ctx)
    row = _server(ctx)
    await ctx.engine.mcp.stop(row["id"])
    await ctx.engine.mcp.refresh_tools(ctx.ws_id, row["id"])
    return _out(_server(ctx))


@router.delete("/api/w/{ws}/mcp-servers/{sid}", role="admin")
async def delete_server(ctx: Ctx) -> None:
    row = _server(ctx)
    await ctx.engine.mcp.stop(row["id"])
    ctx.engine.secrets.delete(row["secret_id"])
    ctx.engine.db.execute("DELETE FROM mcp_servers WHERE id = ? AND workspace_id = ?", (row["id"], ctx.ws_id))
    ctx.audit("mcp.delete", row["id"])


# --- access keys for the built-in MCP server -------------------------------------------------------------


@router.get("/api/w/{ws}/mcp-keys", role="viewer")
async def keys(ctx: Ctx) -> list[dict[str, Any]]:
    return ctx.engine.db.all("SELECT id, name, created_at, last_used_at FROM mcp_keys WHERE workspace_id = ? AND "
                             "user_id = ? ORDER BY created_at DESC", (ctx.ws_id, ctx.user_id))


class KeyIn(BaseModel):
    name: str = Field(min_length=1, max_length=60)


@router.post("/api/w/{ws}/mcp-keys", role="viewer", body=KeyIn)
async def create_key(ctx: Ctx) -> dict[str, Any]:
    _not_sealed(ctx)
    count = ctx.engine.db.scalar("SELECT COUNT(*) FROM mcp_keys WHERE workspace_id = ? AND user_id = ?",
                                 (ctx.ws_id, ctx.user_id))
    if count >= 20:
        raise Invalid("You have 20 keys; delete one first.")
    token = "odk_" + new_token(32)
    kid = new_id("key_")
    ctx.engine.db.insert("mcp_keys", {"id": kid, "workspace_id": ctx.ws_id, "user_id": ctx.user_id,
                                      "name": ctx.body.name.strip(), "token_hash": token_hash(token), "created_at": now()})
    ctx.audit("mcp_key.create", kid)
    endpoint = f"{(ctx.engine.settings.public_url or '').rstrip('/')}/mcp"
    return {"id": kid, "name": ctx.body.name, "key": token, "endpoint": endpoint,
            "note": "Copy the key now; it is not shown again."}


@router.delete("/api/w/{ws}/mcp-keys/{kid}", role="viewer")
async def delete_key(ctx: Ctx) -> None:
    ctx.engine.db.execute("DELETE FROM mcp_keys WHERE id = ? AND workspace_id = ? AND user_id = ?",
                          (ctx.request.path_params["kid"], ctx.ws_id, ctx.user_id))
