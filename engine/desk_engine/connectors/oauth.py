"""OAuth 2.0 authorization-code flow shared by every connector (QuickBooks Online, Zoho Books,
Xero, Google).

Client credentials ("OAuth apps") come from the environment (``DESK_<PROVIDER>_CLIENT_ID`` /
``_CLIENT_SECRET``) or are entered by an administrator under Connections → OAuth apps. Tokens are
encrypted with the engine master key. The ``state`` value is single-use and expires after 15 minutes.
"""

from __future__ import annotations

import base64
import os
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any
from urllib.parse import urlencode

import httpx2 as httpx

from ..db import dumps, loads, new_id, now
from ..errors import Invalid, NotConfigured, NotFound, UpstreamError
from ..security import new_token, pkce_pair

if TYPE_CHECKING:
    from ..app import Engine

STATE_TTL = 15 * 60


@dataclass(frozen=True)
class OAuthProvider:
    key: str
    label: str
    authorize_url: str
    token_url: str
    scopes: list[str]
    pkce: bool = True
    basic_auth: bool = False  # client credentials in an Authorization: Basic header
    extra_params: dict[str, str] = field(default_factory=dict)
    scope_separator: str = " "
    developer_portal: str = ""


PROVIDERS: dict[str, OAuthProvider] = {
    "quickbooks": OAuthProvider(
        "quickbooks", "QuickBooks Online",
        "https://appcenter.intuit.com/connect/oauth2",
        "https://oauth.platform.intuit.com/oauth2/v1/tokens/bearer",
        ["com.intuit.quickbooks.accounting"], pkce=False, basic_auth=True,
        developer_portal="https://developer.intuit.com/app/developer/dashboard"),
    "zoho_books": OAuthProvider(
        "zoho_books", "Zoho Books",
        "https://accounts.zoho.com/oauth/v2/auth",
        "https://accounts.zoho.com/oauth/v2/token",
        ["ZohoBooks.reports.READ", "ZohoBooks.invoices.READ", "ZohoBooks.bills.READ", "ZohoBooks.settings.READ",
         "ZohoBooks.contacts.READ"],
        pkce=False, scope_separator=",", extra_params={"access_type": "offline", "prompt": "consent"},
        developer_portal="https://api-console.zoho.com"),
    "xero": OAuthProvider(
        "xero", "Xero",
        "https://login.xero.com/identity/connect/authorize",
        "https://identity.xero.com/connect/token",
        ["openid", "profile", "email", "offline_access", "accounting.reports.profitandloss.read",
         "accounting.reports.balancesheet.read", "accounting.reports.aged.read", "accounting.invoices.read",
         "accounting.contacts.read", "accounting.settings.read"],
        basic_auth=True, developer_portal="https://developer.xero.com/app/manage"),
    "google": OAuthProvider(
        "google", "Google (Gmail, Drive, Sheets)",
        "https://accounts.google.com/o/oauth2/v2/auth",
        "https://oauth2.googleapis.com/token",
        ["openid", "email", "https://www.googleapis.com/auth/gmail.readonly",
         "https://www.googleapis.com/auth/gmail.compose", "https://www.googleapis.com/auth/drive.readonly",
         "https://www.googleapis.com/auth/spreadsheets.readonly"],
        extra_params={"access_type": "offline", "prompt": "consent", "include_granted_scopes": "true"},
        developer_portal="https://console.cloud.google.com/apis/credentials"),
}


def get_provider(key: str) -> OAuthProvider:
    if key not in PROVIDERS:
        raise NotFound(f"Unknown connector: {key}")
    return PROVIDERS[key]


# --- OAuth app credentials ---------------------------------------------------------------------


def app_credentials(engine: Engine, provider: str) -> tuple[str, str, dict[str, Any]]:
    env_id = os.environ.get(f"DESK_{provider.upper()}_CLIENT_ID", "").strip()
    env_secret = os.environ.get(f"DESK_{provider.upper()}_CLIENT_SECRET", "").strip()
    stored = loads(engine.db.scalar("SELECT value FROM settings WHERE key = ?", (f"oauth_app:{provider}",)), {})
    options = stored.get("options", {})
    if env_id and env_secret:
        return env_id, env_secret, options
    if stored.get("client_id") and stored.get("secret_id"):
        secret = engine.secrets.get(stored["secret_id"], workspace_id=None)["client_secret"]
        return stored["client_id"], secret, options
    raise NotConfigured(
        f"{PROVIDERS[provider].label} is not set up on this installation. An administrator adds the OAuth "
        f"client ID and secret under Connections → OAuth apps (redirect URI: {engine.settings.oauth_redirect_uri}).",
        code="oauth_app_missing")


def save_app_credentials(engine: Engine, provider: str, client_id: str, client_secret: str | None,
                         options: dict[str, Any]) -> None:
    get_provider(provider)
    key = f"oauth_app:{provider}"
    stored = loads(engine.db.scalar("SELECT value FROM settings WHERE key = ?", (key,)), {})
    secret_id = stored.get("secret_id")
    if client_secret:
        secret_id = engine.secrets.put("oauth_app", {"client_secret": client_secret}, workspace_id=None,
                                       secret_id=secret_id)
    if not secret_id:
        raise Invalid("Client secret is required.")
    value = {"client_id": client_id.strip(), "secret_id": secret_id, "options": options}
    with engine.db.tx() as c:
        c.execute("INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                  (key, dumps(value)))


def app_status(engine: Engine) -> list[dict[str, Any]]:
    out = []
    for key, p in PROVIDERS.items():
        configured = True
        source = "settings"
        try:
            app_credentials(engine, key)
            if os.environ.get(f"DESK_{key.upper()}_CLIENT_ID"):
                source = "environment"
        except NotConfigured:
            configured = False
            source = None
        stored = loads(engine.db.scalar("SELECT value FROM settings WHERE key = ?", (f"oauth_app:{key}",)), {})
        out.append({"provider": key, "label": p.label, "configured": configured, "source": source,
                    "client_id": stored.get("client_id"), "options": stored.get("options", {}),
                    "redirect_uri": engine.settings.oauth_redirect_uri, "developer_portal": p.developer_portal,
                    "scopes": scopes_for(engine, key)})
    return out


def scopes_for(engine: Engine, provider: str) -> list[str]:
    p = get_provider(provider)
    stored = loads(engine.db.scalar("SELECT value FROM settings WHERE key = ?", (f"oauth_app:{provider}",)), {})
    custom = (stored.get("options") or {}).get("scopes")
    if isinstance(custom, list) and custom:
        return [str(s) for s in custom]
    return p.scopes


# --- the flow ----------------------------------------------------------------------------------


def authorization_url(engine: Engine, provider: str, workspace_id: str, user_id: str,
                      extra: dict[str, Any] | None = None) -> str:
    p = get_provider(provider)
    client_id, _, _ = app_credentials(engine, provider)
    state = new_token(24)
    verifier, challenge = pkce_pair()
    engine.db.execute("DELETE FROM oauth_states WHERE created_at < ?", (now() - STATE_TTL,))
    engine.db.insert("oauth_states", {"state": state, "workspace_id": workspace_id, "user_id": user_id,
                                      "provider": provider, "code_verifier": verifier,
                                      "extra": dumps(extra or {}), "created_at": now()})
    params = {"client_id": client_id, "response_type": "code", "redirect_uri": engine.settings.oauth_redirect_uri,
              "scope": p.scope_separator.join(scopes_for(engine, provider)), "state": state, **p.extra_params}
    if p.pkce:
        params |= {"code_challenge": challenge, "code_challenge_method": "S256"}
    return f"{p.authorize_url}?{urlencode(params)}"


def consume_state(engine: Engine, state: str) -> dict[str, Any]:
    with engine.db.tx() as c:
        row = c.execute("SELECT * FROM oauth_states WHERE state = ?", (state,)).fetchone()
        c.execute("DELETE FROM oauth_states WHERE state = ?", (state,))
    if row is None or now() - row["created_at"] > STATE_TTL:
        raise Invalid("This sign-in link has expired or was already used. Start the connection again.",
                      code="bad_state")
    out = dict(row)
    out["extra"] = loads(out["extra"], {})
    return out


async def token_request(engine: Engine, provider: str, data: dict[str, str], *, token_url: str | None = None) -> dict[str, Any]:
    p = get_provider(provider)
    client_id, client_secret, _ = app_credentials(engine, provider)
    headers = {"Accept": "application/json"}
    body = dict(data)
    if p.basic_auth:
        headers["Authorization"] = "Basic " + base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    else:
        body |= {"client_id": client_id, "client_secret": client_secret}
    async with httpx.AsyncClient(timeout=30, transport=engine.http_transport) as client:
        resp = await client.post(token_url or p.token_url, data=body, headers=headers)
    if resp.status_code >= 400:
        detail = resp.text[:300]
        raise UpstreamError(f"{p.label} refused the token request (HTTP {resp.status_code}): {detail}",
                            code="oauth_token_error")
    tokens = resp.json()
    if "error" in tokens:
        raise UpstreamError(f"{p.label}: {tokens.get('error_description') or tokens['error']}", code="oauth_token_error")
    tokens["obtained_at"] = time.time()
    return tokens


async def exchange_code(engine: Engine, provider: str, code: str, verifier: str, *,
                        token_url: str | None = None) -> dict[str, Any]:
    data = {"grant_type": "authorization_code", "code": code, "redirect_uri": engine.settings.oauth_redirect_uri}
    if get_provider(provider).pkce:
        data["code_verifier"] = verifier
    return await token_request(engine, provider, data, token_url=token_url)


def store_connection(engine: Engine, *, workspace_id: str, user_id: str, provider: str, label: str,
                     config: dict[str, Any], tokens: dict[str, Any], connection_id: str | None = None) -> str:
    cid = connection_id or new_id("cn_")
    secret_id = engine.secrets.put("connection", {"tokens": tokens}, workspace_id=workspace_id,
                                   secret_id=f"sec_{cid}")
    existing = engine.db.one("SELECT id FROM connections WHERE id = ? AND workspace_id = ?", (cid, workspace_id))
    if existing:
        engine.db.update("connections", cid, {"status": "connected", "config": dumps(config), "secret_id": secret_id,
                                              "label": label, "last_error": None}, workspace_id=workspace_id)
    else:
        engine.db.insert("connections", {"id": cid, "workspace_id": workspace_id, "provider": provider, "label": label,
                                         "status": "connected", "config": dumps(config), "secret_id": secret_id,
                                         "created_by": user_id, "created_at": now()})
    return cid


class TokenManager:
    """Gives a valid access token for a connection, refreshing (and storing rotated refresh tokens)
    when needed."""

    def __init__(self, engine: Engine, connection: dict[str, Any]) -> None:
        self.engine = engine
        self.connection = connection

    def _tokens(self) -> dict[str, Any]:
        return self.engine.secrets.get(self.connection["secret_id"], workspace_id=self.connection["workspace_id"])["tokens"]

    async def access_token(self, force_refresh: bool = False) -> str:
        tokens = self._tokens()
        expires_in = float(tokens.get("expires_in") or 3600)
        fresh = time.time() < float(tokens.get("obtained_at", 0)) + expires_in - 120
        if fresh and not force_refresh:
            return tokens["access_token"]
        refresh = tokens.get("refresh_token")
        if not refresh:
            self._mark_error("The connection has expired. Reconnect it.")
            raise UpstreamError("The connection has expired. Reconnect it under Connections.", code="reconnect")
        config = loads(self.connection["config"], {})
        try:
            new = await token_request(self.engine, self.connection["provider"],
                                      {"grant_type": "refresh_token", "refresh_token": refresh},
                                      token_url=config.get("token_url"))
        except UpstreamError:
            self._mark_error("Refreshing access failed; reconnect it.")
            raise
        if "refresh_token" not in new:
            new["refresh_token"] = refresh
        self.engine.secrets.put("connection", {"tokens": new}, workspace_id=self.connection["workspace_id"],
                                secret_id=self.connection["secret_id"])
        return new["access_token"]

    def _mark_error(self, message: str) -> None:
        self.engine.db.update("connections", self.connection["id"], {"status": "error", "last_error": message},
                              workspace_id=self.connection["workspace_id"])


def get_connection(engine: Engine, ws_id: str, connection_id: str | None, provider: str | set[str] | None = None) -> dict[str, Any]:
    providers = {provider} if isinstance(provider, str) else provider
    if connection_id:
        row = engine.db.one("SELECT * FROM connections WHERE id = ? AND workspace_id = ?", (connection_id, ws_id))
    else:
        rows = [r for r in engine.db.all("SELECT * FROM connections WHERE workspace_id = ? AND status != 'pending' "
                                         "ORDER BY created_at", (ws_id,))
                if providers is None or r["provider"] in providers]
        if len(rows) > 1:
            raise Invalid("Several connections match; pass connection_id. Options: "
                          + ", ".join(f"{r['label']} ({r['id']})" for r in rows))
        row = rows[0] if rows else None
    if row is None or (providers and row["provider"] not in providers):
        names = ", ".join(sorted(providers)) if providers else "a connection"
        raise NotConfigured(f"No {names} connection in this workspace. Connect one under Connections.",
                            code="not_connected")
    return row
