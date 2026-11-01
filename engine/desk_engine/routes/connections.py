"""Connections: accounting systems, Google, Telegram, WhatsApp; OAuth app credentials."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from ..connectors.accounting import ACCOUNTING_PROVIDERS, sync_connection
from ..connectors.oauth import PROVIDERS, app_status, authorization_url, save_app_credentials
from ..db import dumps, loads
from ..errors import Forbidden, Invalid, NotFound
from ..web import Ctx, Router
from .auth import is_instance_admin

router = Router()
SAFE_CONFIG_KEYS = {"realm_id", "environment", "organization_id", "organizations", "api_domain", "tenant_id",
                    "tenant_name", "tenants", "email", "scopes", "bot_username", "mode", "phone_number_id",
                    "display_number", "template_name", "template_language", "company", "base_url",
                    "include_other_sources", "kb_id", "kb_name", "send", "database", "username", "api", "company_id", "companies"}


def _out(ctx: Ctx, row: dict[str, Any]) -> dict[str, Any]:
    config = {k: v for k, v in loads(row["config"], {}).items() if k in SAFE_CONFIG_KEYS}
    links = ctx.engine.db.all("SELECT l.id, l.display_name, l.user_id, u.name AS user_name, l.created_at FROM channel_links l "
                              "JOIN users u ON u.id = l.user_id WHERE l.connection_id = ?", (row["id"],)) \
        if row["provider"] in {"telegram", "whatsapp"} else []
    if ctx.role not in {"admin", "owner"}:
        links = [x for x in links if x["user_id"] == ctx.user_id]
    out = {k: row[k] for k in ("id", "provider", "label", "status", "created_at", "last_sync_at", "last_error")}
    out["config"] = config
    out["links"] = links
    if row["provider"] == "custom":
        out["ingest_url"] = f"{ctx.engine.settings.public_url.rstrip('/')}/api/ingest/v1/accounting/{{kind}}"
        out["ingest_key_prefix"] = ctx.engine.db.scalar("SELECT prefix FROM ingest_keys WHERE connection_id = ? "
                                                        "ORDER BY created_at DESC LIMIT 1", (row["id"],))
    if row["provider"] == "whatsapp":
        out["webhook_url"] = f"{ctx.engine.settings.public_url.rstrip('/')}/hooks/whatsapp/{row['id']}"
    return out


def _conn(ctx: Ctx) -> dict[str, Any]:
    row = ctx.engine.db.one("SELECT * FROM connections WHERE id = ? AND workspace_id = ?",
                            (ctx.request.path_params["cid"], ctx.ws_id))
    if row is None:
        raise NotFound("Connection not found.")
    return row


@router.get("/api/w/{ws}/connections", role="viewer")
async def list_connections(ctx: Ctx) -> dict[str, Any]:
    rows = ctx.engine.db.all("SELECT * FROM connections WHERE workspace_id = ? ORDER BY created_at", (ctx.ws_id,))
    apps = {a["provider"]: a["configured"] for a in app_status(ctx.engine)}
    return {"connections": [_out(ctx, r) for r in rows], "oauth_apps": apps,
            "redirect_uri": ctx.engine.settings.oauth_redirect_uri}


# --- OAuth app credentials (installation level) -------------------------------------------------------


@router.get("/api/oauth-apps")
async def oauth_apps(ctx: Ctx) -> list[dict[str, Any]]:
    return app_status(ctx.engine)


class OAuthAppIn(BaseModel):
    client_id: str = Field(min_length=3, max_length=300)
    client_secret: str | None = Field(default=None, max_length=500)
    environment: Literal["production", "sandbox"] | None = None
    scopes: list[str] | None = None


@router.put("/api/oauth-apps/{provider}", body=OAuthAppIn, fresh=True)
async def set_oauth_app(ctx: Ctx) -> dict[str, Any]:
    if not is_instance_admin(ctx):
        raise Forbidden("Only the administrator of this installation can set OAuth apps.")
    provider = ctx.request.path_params["provider"]
    if provider not in PROVIDERS:
        raise NotFound("Unknown provider.")
    b: OAuthAppIn = ctx.body
    options: dict[str, Any] = {}
    if b.environment:
        options["environment"] = b.environment
    if b.scopes:
        options["scopes"] = [s.strip() for s in b.scopes if s.strip()][:40]
    save_app_credentials(ctx.engine, provider, b.client_id, b.client_secret, options)
    ctx.engine.audit(None, ctx.user_id, "oauth_app.set", provider)
    return next(a for a in app_status(ctx.engine) if a["provider"] == provider)


# --- OAuth connections --------------------------------------------------------------------------------


class AuthorizeIn(BaseModel):
    connection_id: str | None = None
    # the app page to come back to after the provider's sign-in, e.g. "/connections" (an app route, never a URL)
    return_to: str | None = Field(default=None, max_length=120, pattern=r"^/[A-Za-z0-9_\-/]*$")


@router.post("/api/w/{ws}/connections/{provider}/authorize", role="admin", body=AuthorizeIn, fresh=True)
async def authorize(ctx: Ctx) -> dict[str, Any]:
    provider = ctx.request.path_params["provider"]
    if provider not in PROVIDERS:
        raise NotFound("Unknown connector.")
    extra = {}
    if ctx.body.connection_id:
        existing = ctx.engine.db.one("SELECT provider FROM connections WHERE id = ? AND workspace_id = ?",
                                     (ctx.body.connection_id, ctx.ws_id))
        if existing is None or existing["provider"] != provider:
            raise NotFound("Connection not found.")
        extra["connection_id"] = ctx.body.connection_id
    extra["return_to"] = ctx.body.return_to or "/connections"
    url = authorization_url(ctx.engine, provider, ctx.ws_id, ctx.user_id, extra)
    return {"url": url}


@router.post("/api/w/{ws}/connections/{cid}/sync", role="editor")
async def sync(ctx: Ctx) -> dict[str, Any]:
    conn = _conn(ctx)
    if conn["provider"] not in ACCOUNTING_PROVIDERS:
        raise Invalid("Only accounting connections are synced.")
    from ..connectors.accounting import is_push

    if is_push(conn):
        raise Invalid("This connection receives data from your system (push); there is nothing to fetch.")
    result = await sync_connection(ctx.engine, conn, user_id=ctx.user_id)
    ctx.audit("connection.sync", conn["id"], {"errors": result["errors"]})
    return result


class ConnectionPatch(BaseModel):
    label: str | None = Field(default=None, max_length=120)
    organization_id: str | None = None
    tenant_id: str | None = None


@router.patch("/api/w/{ws}/connections/{cid}", role="admin", body=ConnectionPatch)
async def patch_connection(ctx: Ctx) -> dict[str, Any]:
    conn = _conn(ctx)
    config = loads(conn["config"], {})
    b: ConnectionPatch = ctx.body
    values: dict[str, Any] = {}
    if b.label:
        values["label"] = b.label.strip()
    if b.organization_id:
        if b.organization_id not in [o["id"] for o in config.get("organizations", [])]:
            raise Invalid("Unknown organization for this connection.")
        config["organization_id"] = b.organization_id
    if b.tenant_id:
        if b.tenant_id not in [t["id"] for t in config.get("tenants", [])]:
            raise Invalid("Unknown Xero organisation for this connection.")
        config["tenant_id"] = b.tenant_id
        config["tenant_name"] = next(t["name"] for t in config["tenants"] if t["id"] == b.tenant_id)
    values["config"] = dumps(config)
    ctx.engine.db.update("connections", conn["id"], values, workspace_id=ctx.ws_id)
    ctx.audit("connection.update", conn["id"])
    return _out(ctx, _conn(ctx))


@router.delete("/api/w/{ws}/connections/{cid}", role="admin", fresh=True)
async def delete_connection(ctx: Ctx) -> None:
    conn = _conn(ctx)
    if conn["provider"] == "telegram" and loads(conn["config"], {}).get("mode") == "webhook":
        from ..channels.service import Telegram

        try:
            secret = ctx.engine.secrets.get(conn["secret_id"], workspace_id=ctx.ws_id)
            await Telegram(ctx.engine, secret["bot_token"]).call("deleteWebhook")
        except Exception:  # removing locally matters more than the remote clean-up
            pass
    ctx.engine.secrets.delete(conn["secret_id"])
    ctx.engine.db.execute("DELETE FROM connections WHERE id = ? AND workspace_id = ?", (conn["id"], ctx.ws_id))
    ctx.audit("connection.delete", conn["id"], {"provider": conn["provider"]})


# --- API-key accounting connections: Odoo and the Open Accounting Connector ------------------------------


def _check_base_url(ctx: Ctx, url: str) -> str:
    from ..security import check_outbound_url

    url = url.strip().rstrip("/")
    check_outbound_url(url, allow_private=ctx.engine.settings.allow_private_network_urls,
                       allow_http=ctx.engine.settings.is_desktop)
    return url


def _start_first_sync(ctx: Ctx, cid: str) -> None:
    import asyncio

    from ..connectors.flows import _first_sync

    asyncio.get_running_loop().create_task(_first_sync(ctx.engine, ctx.ws_id, cid, ctx.user_id))


def _save_key_connection(ctx: Ctx, *, provider: str, label: str, config: dict[str, Any], secret: dict[str, Any],
                         connection_id: str | None) -> str:
    from ..db import new_id, now

    if connection_id:
        existing = ctx.engine.db.one("SELECT * FROM connections WHERE id = ? AND workspace_id = ? AND provider = ?",
                                     (connection_id, ctx.ws_id, provider))
        if existing is None:
            raise NotFound("Connection not found.")
    cid = connection_id or new_id("cn_")
    secret_id = ctx.engine.secrets.put("connection", secret, workspace_id=ctx.ws_id, secret_id=f"sec_{cid}")
    if connection_id:
        ctx.engine.db.update("connections", cid, {"label": label, "config": dumps(config), "secret_id": secret_id,
                                                  "status": "connected", "last_error": None}, workspace_id=ctx.ws_id)
    else:
        ctx.engine.db.insert("connections", {"id": cid, "workspace_id": ctx.ws_id, "provider": provider, "label": label,
                                             "status": "connected", "config": dumps(config), "secret_id": secret_id,
                                             "created_by": ctx.user_id, "created_at": now()})
    ctx.audit("connection.connected", cid, {"provider": provider})
    return cid


class OdooIn(BaseModel):
    base_url: str = Field(min_length=8, max_length=300, description="e.g. https://mycompany.odoo.com")
    api_key: str = Field(min_length=10, max_length=200)
    database: str | None = Field(default=None, max_length=120)
    username: str | None = Field(default=None, max_length=200, description="needed for Odoo 17 and 18")
    company_id: int | None = None
    connection_id: str | None = None


@router.post("/api/w/{ws}/connections/odoo", role="admin", body=OdooIn, fresh=True)
async def connect_odoo(ctx: Ctx) -> dict[str, Any]:
    from ..connectors.odoo import OdooClient

    b: OdooIn = ctx.body
    url = _check_base_url(ctx, b.base_url)
    client = OdooClient(ctx.engine, base_url=url, database=(b.database or "").strip() or None,
                        username=(b.username or "").strip() or None, api_key=b.api_key.strip(), company_id=b.company_id)
    info = await client.company()  # proves the URL, key and access rights
    config = {"base_url": url, "database": client.database, "username": client.username, "api": client.api,
              "company_id": b.company_id or info["id"], "companies": info["companies"],
              "company": {"name": info["name"], "currency": info.get("currency")}}
    label = f"Odoo: {info['name']}"
    cid = _save_key_connection(ctx, provider="odoo", label=label, config=config, secret={"api_key": b.api_key.strip()},
                               connection_id=b.connection_id)
    _start_first_sync(ctx, cid)
    return _out(ctx, _conn_by_id(ctx, cid))


class CustomIn(BaseModel):
    label: str = Field(min_length=1, max_length=80, description="the system's name, e.g. 'Tally' or 'Our ERP'")
    mode: Literal["pull", "push"]
    base_url: str | None = Field(default=None, max_length=300)
    api_key: str | None = Field(default=None, max_length=500)
    connection_id: str | None = None


@router.post("/api/w/{ws}/connections/custom", role="admin", body=CustomIn, fresh=True)
async def connect_custom(ctx: Ctx) -> dict[str, Any]:
    b: CustomIn = ctx.body
    if b.mode == "pull":
        from ..connectors.open_api import PullClient

        if not b.base_url or not b.api_key:
            raise Invalid("A pull connector needs the adapter's address and key.")
        url = _check_base_url(ctx, b.base_url)
        info = await PullClient(ctx.engine, base_url=url, api_key=b.api_key.strip()).company()
        cid = _save_key_connection(ctx, provider="custom", label=b.label.strip(),
                                   config={"mode": "pull", "base_url": url, "company": info},
                                   secret={"api_key": b.api_key.strip()}, connection_id=b.connection_id)
        _start_first_sync(ctx, cid)
        return _out(ctx, _conn_by_id(ctx, cid))
    cid = _save_key_connection(ctx, provider="custom", label=b.label.strip(), config={"mode": "push"}, secret={},
                               connection_id=b.connection_id)
    out = _out(ctx, _conn_by_id(ctx, cid))
    out["ingest_key"] = _new_ingest_key(ctx, cid)
    return out


def _new_ingest_key(ctx: Ctx, cid: str) -> str:
    from ..db import new_id, now
    from ..security import new_token, token_hash

    key = "odk_" + new_token(30)
    with ctx.engine.db.tx() as c:
        c.execute("DELETE FROM ingest_keys WHERE connection_id = ?", (cid,))
        c.execute("INSERT INTO ingest_keys (id, workspace_id, connection_id, key_hash, prefix, created_by, created_at) "
                  "VALUES (?,?,?,?,?,?,?)", (new_id("ik_"), ctx.ws_id, cid, token_hash(key), key[:10], ctx.user_id,
                                              now()))
    ctx.audit("connection.ingest_key", cid)
    return key


@router.post("/api/w/{ws}/connections/{cid}/ingest-key", role="admin", fresh=True)
async def rotate_ingest_key(ctx: Ctx) -> dict[str, Any]:
    conn = _conn(ctx)
    if conn["provider"] != "custom" or loads(conn["config"], {}).get("mode") != "push":
        raise Invalid("Only push connections have an ingest key.")
    return {"ingest_key": _new_ingest_key(ctx, conn["id"])}


def _conn_by_id(ctx: Ctx, cid: str) -> dict[str, Any]:
    row = ctx.engine.db.one("SELECT * FROM connections WHERE id = ? AND workspace_id = ?", (cid, ctx.ws_id))
    if row is None:
        raise NotFound("Connection not found.")
    return row


# --- messaging channels -------------------------------------------------------------------------------


class TelegramIn(BaseModel):
    bot_token: str = Field(min_length=20, max_length=200)


@router.post("/api/w/{ws}/connections/telegram", role="admin", body=TelegramIn, fresh=True)
async def connect_telegram(ctx: Ctx) -> dict[str, Any]:
    result = await ctx.engine.channels.connect_telegram(ctx.ws_id, ctx.user_id, ctx.body.bot_token)
    ctx.audit("connection.connected", result["id"], {"provider": "telegram"})
    return result


class WhatsAppIn(BaseModel):
    phone_number_id: str = Field(min_length=5, max_length=40)
    display_number: str = Field(min_length=5, max_length=30)
    access_token: str = Field(min_length=20, max_length=1000)
    app_secret: str = Field(min_length=10, max_length=200)
    template_name: str | None = Field(default=None, max_length=200)
    template_language: str | None = Field(default=None, max_length=20)


@router.post("/api/w/{ws}/connections/whatsapp", role="admin", body=WhatsAppIn, fresh=True)
async def connect_whatsapp(ctx: Ctx) -> dict[str, Any]:
    b: WhatsAppIn = ctx.body
    result = ctx.engine.channels.connect_whatsapp(
        ctx.ws_id, ctx.user_id, phone_number_id=b.phone_number_id.strip(), display_number=b.display_number,
        access_token=b.access_token, app_secret=b.app_secret, template_name=b.template_name,
        template_language=b.template_language)
    ctx.audit("connection.connected", result["id"], {"provider": "whatsapp"})
    return result


@router.post("/api/w/{ws}/connections/{cid}/pair", role="viewer")
async def pair(ctx: Ctx) -> dict[str, Any]:
    conn = _conn(ctx)
    return ctx.engine.channels.pairing_code(ctx.ws_id, conn["id"], ctx.user_id)


@router.delete("/api/w/{ws}/connections/{cid}/links/{link_id}", role="viewer")
async def unlink(ctx: Ctx) -> None:
    conn = _conn(ctx)
    link = ctx.engine.db.one("SELECT * FROM channel_links WHERE id = ? AND connection_id = ?",
                             (ctx.request.path_params["link_id"], conn["id"]))
    if link is None:
        raise NotFound("Link not found.")
    if link["user_id"] != ctx.user_id:
        ctx.require("admin")
    ctx.engine.db.execute("DELETE FROM channel_links WHERE id = ?", (link["id"],))
    ctx.audit("channel.unlinked", conn["id"], {"user_id": link["user_id"]})
