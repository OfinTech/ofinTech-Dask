"""MCP client: connects the agent to Model Context Protocol servers.

* ``http`` servers (Streamable HTTP) — a URL plus optional headers (e.g. an API key) kept encrypted.
* ``stdio`` servers — a local command. Allowed only in desktop mode (or when the Team Server operator
  sets ``DESK_ALLOW_STDIO_MCP=true``); the command is shown to the user before it is saved and runs
  with the user's own permissions on their machine, with a minimal environment.

Every MCP tool gets a policy: ``ask`` (default — a person approves each call), ``auto`` (runs without
asking; offered for tools the server marks read-only) or ``off``. Each server runs in its own task
with a persistent session that closes after 10 idle minutes.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import re
import shlex
from typing import TYPE_CHECKING, Any

from .agent.tools import Tool, ToolContext, ToolOutput
from .db import dumps, loads
from .errors import Forbidden, Invalid, NotFound, UpstreamError
from .security import check_outbound_url

if TYPE_CHECKING:
    from .app import Engine

log = logging.getLogger("desk.mcp")
IDLE_SECONDS = 600
CALL_TIMEOUT = 120.0
SAFE_ENV = ("PATH", "HOME", "USER", "LANG", "LC_ALL", "TMPDIR", "TEMP", "TMP", "SYSTEMROOT", "APPDATA",
            "LOCALAPPDATA", "PROGRAMFILES", "USERPROFILE")


def _attr(obj: Any, *names: str, default: Any = None) -> Any:
    for n in names:
        if isinstance(obj, dict) and n in obj:
            return obj[n]
        if hasattr(obj, n):
            return getattr(obj, n)
    return default


def tool_prefix(server_name: str) -> str:
    return "mcp_" + re.sub(r"[^a-z0-9]+", "_", server_name.lower()).strip("_")[:20] + "__"


class _Worker:
    """Owns one MCP session in a dedicated task (the SDK's context managers must be entered and
    exited in the same task)."""

    def __init__(self, manager: MCPManager, server: dict[str, Any]) -> None:
        self.manager = manager
        self.server = server
        self.queue: asyncio.Queue[tuple[str, dict[str, Any], asyncio.Future[Any]]] = asyncio.Queue()
        self.task = asyncio.get_running_loop().create_task(self._main())
        self.ready: asyncio.Future[None] = asyncio.get_running_loop().create_future()

    async def request(self, op: str, args: dict[str, Any]) -> Any:
        fut: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        await self.queue.put((op, args, fut))
        return await asyncio.wait_for(fut, CALL_TIMEOUT)

    @contextlib.asynccontextmanager
    async def _transport(self):  # type: ignore[no-untyped-def]
        engine = self.manager.engine
        server = self.server
        secret = {}
        if server["secret_id"]:
            secret = engine.secrets.get(server["secret_id"], workspace_id=server["workspace_id"])
        if server["transport"] == "http":
            import httpx2 as httpx
            from mcp.client.streamable_http import streamable_http_client

            check_outbound_url(server["url"], allow_private=engine.settings.allow_private_network_urls)
            headers = {str(k): str(v) for k, v in (secret.get("headers") or {}).items()}
            async with httpx.AsyncClient(headers=headers, timeout=httpx.Timeout(60, read=300),
                                         transport=engine.mcp_http_transport) as client:
                async with streamable_http_client(server["url"], http_client=client) as streams:
                    yield streams[0], streams[1]
        else:
            if not engine.settings.allow_stdio_mcp:
                raise Forbidden("Local (stdio) MCP servers are disabled on this server.")
            from mcp.client.stdio import StdioServerParameters, stdio_client

            env = {k: os.environ[k] for k in SAFE_ENV if k in os.environ}
            env.update({str(k): str(v) for k, v in (secret.get("env") or {}).items()})
            params = StdioServerParameters(command=server["command"], args=loads(server["args"], []), env=env)
            with open(os.devnull, "w") as devnull:
                async with stdio_client(params, errlog=devnull) as (read, write):
                    yield read, write

    async def _main(self) -> None:
        from mcp.client.session import ClientSession

        try:
            async with self._transport() as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    if not self.ready.done():
                        self.ready.set_result(None)
                    while True:
                        try:
                            op, args, fut = await asyncio.wait_for(self.queue.get(), IDLE_SECONDS)
                        except TimeoutError:
                            return
                        if op == "stop":
                            fut.set_result(None)
                            return
                        try:
                            if op == "list":
                                result = await session.list_tools()
                            else:
                                result = await session.call_tool(args["name"], args.get("arguments") or {})
                            if not fut.done():
                                fut.set_result(result)
                        except Exception as exc:  # noqa: BLE001 - reported to the caller
                            if not fut.done():
                                fut.set_exception(exc)
        except BaseException as exc:  # noqa: BLE001
            if not self.ready.done():
                self.ready.set_exception(exc if isinstance(exc, Exception) else RuntimeError(str(exc)))
            while not self.queue.empty():
                _, _, fut = self.queue.get_nowait()
                if not fut.done():
                    fut.set_exception(UpstreamError("The MCP server connection closed."))
            if isinstance(exc, (asyncio.CancelledError, KeyboardInterrupt)):
                raise
            log.warning("MCP server %s stopped: %s", self.server["name"], exc)
        finally:
            self.manager.workers.pop(self.server["id"], None)


class MCPManager:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine
        self.workers: dict[str, _Worker] = {}

    def _server(self, ws_id: str, server_id: str) -> dict[str, Any]:
        row = self.engine.db.one("SELECT * FROM mcp_servers WHERE id = ? AND workspace_id = ?", (server_id, ws_id))
        if row is None:
            raise NotFound("MCP server not found.")
        return row

    async def _worker(self, server: dict[str, Any]) -> _Worker:
        w = self.workers.get(server["id"])
        if w is None or w.task.done():
            w = _Worker(self, server)
            self.workers[server["id"]] = w
        try:
            await asyncio.wait_for(asyncio.shield(w.ready), 45)
        except TimeoutError as exc:
            w.task.cancel()
            raise UpstreamError(f"MCP server '{server['name']}' did not start within 45 seconds.") from exc
        except Exception as exc:
            raise UpstreamError(f"MCP server '{server['name']}' failed to start: {_describe(exc)}") from exc
        return w

    async def stop(self, server_id: str) -> None:
        w = self.workers.pop(server_id, None)
        if w and not w.task.done():
            w.task.cancel()
            with contextlib.suppress(BaseException):
                await w.task

    async def shutdown(self) -> None:
        for sid in list(self.workers):
            await self.stop(sid)

    async def refresh_tools(self, ws_id: str, server_id: str) -> list[dict[str, Any]]:
        server = self._server(ws_id, server_id)
        try:
            w = await self._worker(server)
            result = await w.request("list", {})
        except Exception as exc:
            self.engine.db.update("mcp_servers", server_id, {"last_error": _describe(exc)[:500]}, workspace_id=ws_id)
            if isinstance(exc, UpstreamError):
                raise
            raise UpstreamError(f"Could not list tools: {_describe(exc)}") from exc
        tools = []
        for t in _attr(result, "tools", default=[]) or []:
            ann = _attr(t, "annotations")
            tools.append({
                "name": _attr(t, "name"),
                "description": (_attr(t, "description") or "")[:1000],
                "input_schema": _attr(t, "input_schema", "inputSchema", default={"type": "object"}) or {"type": "object"},
                "read_only": bool(_attr(ann, "read_only_hint", "readOnlyHint", default=False)) if ann else False,
                "destructive": bool(_attr(ann, "destructive_hint", "destructiveHint", default=False)) if ann else False,
            })
        self.engine.db.update("mcp_servers", server_id, {"tools_cache": dumps(tools), "last_error": None},
                              workspace_id=ws_id)
        return tools

    async def call(self, ws_id: str, server_id: str, name: str, arguments: dict[str, Any]) -> ToolOutput:
        server = self._server(ws_id, server_id)
        w = await self._worker(server)
        try:
            result = await w.request("call", {"name": name, "arguments": arguments})
        except TimeoutError as exc:
            raise UpstreamError(f"MCP tool {name} timed out.") from exc
        except UpstreamError:
            raise
        except Exception as exc:
            raise UpstreamError(f"MCP tool {name} failed: {_describe(exc)}") from exc
        parts: list[str] = []
        for block in _attr(result, "content", default=[]) or []:
            btype = _attr(block, "type")
            if btype == "text":
                parts.append(str(_attr(block, "text", default="")))
            elif btype == "resource":
                res = _attr(block, "resource")
                parts.append(str(_attr(res, "text", default=f"[resource {_attr(res, 'uri')}]")))
            else:
                parts.append(f"[{btype} content not shown]")
        structured = _attr(result, "structured_content", "structuredContent")
        if structured is not None and not parts:
            parts.append(json.dumps(structured, default=str)[:20000])
        text = "\n".join(parts) or "(no content)"
        if _attr(result, "is_error", "isError", default=False):
            text = "Error from MCP tool: " + text
        return ToolOutput(text[:50000], [], structured)

    # -- agent integration -----------------------------------------------------------------------
    async def tools_for(self, ctx: ToolContext) -> list[Tool]:
        out: list[Tool] = []
        for server in self.engine.db.all("SELECT * FROM mcp_servers WHERE workspace_id = ? AND enabled = 1",
                                         (ctx.ws_id,)):
            prefix = tool_prefix(server["name"])
            policies = loads(server["tool_policy"], {})
            for t in loads(server["tools_cache"], []):
                policy = policies.get(t["name"], server["default_policy"])
                if policy == "off":
                    continue
                if policy == "auto" and not t.get("read_only"):
                    policy = "ask"  # only tools the server marks read-only may run unattended
                out.append(self._make_tool(server, prefix, t, policy))
        return out

    def _make_tool(self, server: dict[str, Any], prefix: str, t: dict[str, Any], policy: str) -> Tool:
        server_id, tool_name = server["id"], t["name"]

        async def handler(ctx: ToolContext, args: dict[str, Any]) -> ToolOutput:
            return await self.call(ctx.ws_id, server_id, tool_name, args)

        schema = t.get("input_schema") or {"type": "object"}
        if schema.get("type") != "object":
            schema = {"type": "object", "properties": {}}
        return Tool(name=prefix + tool_name, description=f"[MCP · {server['name']}] {t.get('description') or tool_name}",
                    parameters=schema, handler=handler, effect="write" if policy != "auto" else "read",
                    category="mcp", policy=policy,
                    summarize=lambda a, s=server["name"], n=tool_name: f"MCP {s} → {n}({json.dumps(a, default=str)[:400]})")


def _describe(exc: BaseException) -> str:
    if isinstance(exc, BaseExceptionGroup):
        return "; ".join(_describe(e) for e in exc.exceptions)[:500]
    return f"{type(exc).__name__}: {exc}"[:500]


def validate_server_input(engine: Engine, transport: str, command: str | None, args: list[str] | None,
                          url: str | None) -> None:
    if transport == "http":
        if not url:
            raise Invalid("Enter the MCP server URL.")
        check_outbound_url(url, allow_private=engine.settings.allow_private_network_urls,
                           allow_http=engine.settings.is_desktop)
    elif transport == "stdio":
        if not engine.settings.allow_stdio_mcp:
            raise Forbidden("Local (stdio) MCP servers are only available in the desktop app.")
        if not command or not command.strip():
            raise Invalid("Enter the command that starts the server, e.g. npx or uvx.")
        if any(ch in command for ch in ";|&`$<>\n"):
            raise Invalid("Enter only the program name in Command and put its arguments in Arguments.")
        for a in args or []:
            if "\n" in a:
                raise Invalid("Arguments cannot contain line breaks.")
    else:
        raise Invalid("Transport must be http or stdio.")


def command_preview(command: str, args: list[str]) -> str:
    return " ".join(shlex.quote(x) for x in [command, *args])

