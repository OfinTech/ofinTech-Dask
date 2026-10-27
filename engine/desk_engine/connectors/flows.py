"""Completes OAuth callbacks per provider: exchange the code, discover the company/tenant/account,
store the connection, start the first sync."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

import httpx2 as httpx

from ..db import loads
from ..errors import Invalid, UpstreamError
from .oauth import app_credentials, consume_state, exchange_code, store_connection

if TYPE_CHECKING:
    from ..app import Engine

log = logging.getLogger("desk.connectors")

ZOHO_ACCOUNTS = {"us": "https://accounts.zoho.com", "eu": "https://accounts.zoho.eu", "in": "https://accounts.zoho.in",
                 "au": "https://accounts.zoho.com.au", "jp": "https://accounts.zoho.jp", "ca": "https://accounts.zohocloud.ca",
                 "sa": "https://accounts.zoho.sa", "uk": "https://accounts.zoho.uk", "cn": "https://accounts.zoho.com.cn"}


async def _get_json(engine: Engine, url: str, token: str, headers: dict[str, str] | None = None) -> Any:
    async with httpx.AsyncClient(timeout=30, transport=engine.http_transport) as client:
        resp = await client.get(url, headers={"Authorization": f"Bearer {token}", "Accept": "application/json",
                                              **(headers or {})})
    if resp.status_code >= 400:
        raise UpstreamError(f"HTTP {resp.status_code} from {url.split('/')[2]}: {resp.text[:200]}")
    return resp.json()


async def complete_callback(engine: Engine, params: dict[str, str]) -> dict[str, Any]:
    if params.get("error"):
        raise Invalid(f"The provider returned: {params.get('error_description') or params['error']}",
                      code="oauth_denied")
    state = consume_state(engine, params.get("state", ""))
    code = params.get("code")
    if not code:
        raise Invalid("The provider did not return an authorization code.", code="oauth_no_code")
    provider = state["provider"]
    ws_id, user_id = state["workspace_id"], state["user_id"]
    reconnect_id = state["extra"].get("connection_id")
    _, _, options = app_credentials(engine, provider)

    if provider == "quickbooks":
        realm = params.get("realmId")
        if not realm or not realm.isdigit():
            raise Invalid("QuickBooks did not return a company id (realmId).")
        tokens = await exchange_code(engine, provider, code, state["code_verifier"])
        config = {"realm_id": realm, "environment": options.get("environment", "production")}
        cid = store_connection(engine, workspace_id=ws_id, user_id=user_id, provider=provider,
                               label="QuickBooks Online", config=config, tokens=tokens, connection_id=reconnect_id)
        await _label_from_company(engine, ws_id, cid, "QuickBooks")

    elif provider == "zoho_books":
        accounts = params.get("accounts-server") or ZOHO_ACCOUNTS.get(params.get("location", "us"), ZOHO_ACCOUNTS["us"])
        if not accounts.startswith("https://accounts.zoho") and not accounts.startswith("https://accounts.zohocloud"):
            raise Invalid("Unexpected Zoho accounts server.")
        token_url = f"{accounts}/oauth/v2/token"
        tokens = await exchange_code(engine, provider, code, state["code_verifier"], token_url=token_url)
        api_domain = tokens.get("api_domain") or "https://www.zohoapis.com"
        async with httpx.AsyncClient(timeout=30, transport=engine.http_transport) as client:
            resp = await client.get(f"{api_domain}/books/v3/organizations",
                                    headers={"Authorization": f"Zoho-oauthtoken {tokens['access_token']}"})
        if resp.status_code >= 400:
            raise UpstreamError(f"Zoho Books returned HTTP {resp.status_code}: {resp.text[:200]}")
        orgs = resp.json().get("organizations", [])
        if not orgs:
            raise Invalid("This Zoho account has no Zoho Books organization.")
        org = next((o for o in orgs if o.get("is_default_org")), orgs[0])
        config = {"api_domain": api_domain, "token_url": token_url, "accounts_server": accounts,
                  "organization_id": str(org["organization_id"]),
                  "organizations": [{"id": str(o["organization_id"]), "name": o.get("name")} for o in orgs]}
        store_connection(engine, workspace_id=ws_id, user_id=user_id, provider=provider,
                         label=f"Zoho Books: {org.get('name')}", config=config, tokens=tokens,
                         connection_id=reconnect_id)
        cid = reconnect_id or engine.db.scalar(
            "SELECT id FROM connections WHERE workspace_id = ? AND provider = ? ORDER BY created_at DESC LIMIT 1",
            (ws_id, provider))

    elif provider == "xero":
        tokens = await exchange_code(engine, provider, code, state["code_verifier"])
        tenants = await _get_json(engine, "https://api.xero.com/connections", tokens["access_token"])
        orgs = [t for t in tenants if t.get("tenantType", "ORGANISATION") == "ORGANISATION"]
        if not orgs:
            raise Invalid("No Xero organisation was authorised.")
        config = {"tenant_id": orgs[0]["tenantId"], "tenant_name": orgs[0].get("tenantName"),
                  "tenants": [{"id": t["tenantId"], "name": t.get("tenantName")} for t in orgs]}
        cid = store_connection(engine, workspace_id=ws_id, user_id=user_id, provider=provider,
                               label=f"Xero: {orgs[0].get('tenantName')}", config=config, tokens=tokens,
                               connection_id=reconnect_id)

    elif provider == "google":
        tokens = await exchange_code(engine, provider, code, state["code_verifier"])
        info = await _get_json(engine, "https://openidconnect.googleapis.com/v1/userinfo", tokens["access_token"])
        config = {"email": info.get("email"), "scopes": str(tokens.get("scope", "")).split()}
        cid = store_connection(engine, workspace_id=ws_id, user_id=user_id, provider=provider,
                               label=f"Google: {info.get('email')}", config=config, tokens=tokens,
                               connection_id=reconnect_id)
    else:
        raise Invalid("Unknown connector.")

    engine.audit(ws_id, user_id, "connection.connected", cid, {"provider": provider})
    if provider in {"quickbooks", "zoho_books", "xero"}:
        asyncio.get_running_loop().create_task(_first_sync(engine, ws_id, cid, user_id))
    return {"workspace_id": ws_id, "connection_id": cid, "provider": provider}


async def _label_from_company(engine: Engine, ws_id: str, cid: str, prefix: str) -> None:
    from .accounting import client_for

    conn = engine.db.one("SELECT * FROM connections WHERE id = ?", (cid,))
    try:
        info = await client_for(engine, conn).company()
        if info.get("name"):
            engine.db.update("connections", cid, {"label": f"{prefix}: {info['name']}"}, workspace_id=ws_id)
            cfg = loads(conn["config"], {}) | {"company": info}
            from ..db import dumps
            engine.db.update("connections", cid, {"config": dumps(cfg)}, workspace_id=ws_id)
    except UpstreamError as exc:
        log.warning("could not read company info: %s", exc.message)


async def _first_sync(engine: Engine, ws_id: str, cid: str, user_id: str) -> None:
    from .accounting import sync_connection

    conn = engine.db.one("SELECT * FROM connections WHERE id = ? AND workspace_id = ?", (cid, ws_id))
    if conn is None:
        return
    try:
        await sync_connection(engine, conn, user_id=user_id)
        engine.notify(ws_id, user_id, "sync", f"{conn['label']} synced: datasets are ready for dashboards.",
                      "/data")
    except Exception as exc:  # pragma: no cover - logged and shown on the connection
        log.exception("first sync failed")
        engine.db.update("connections", cid, {"last_error": str(exc)[:300]}, workspace_id=ws_id)
