"""Endpoints without a user session: the OAuth redirect target and messaging webhooks. Each one
authenticates the caller by other means (single-use state, Telegram secret header, Meta signature)."""

from __future__ import annotations

import hmac
import html
import json
import logging
import re
from typing import Any

from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, PlainTextResponse, Response
from starlette.routing import Route

from ..db import loads
from ..errors import AppError
from ..security import hmac_sha256_hex

log = logging.getLogger("desk.public")

PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>OfinTech Desk</title><style>
body{{font:16px/1.5 system-ui,sans-serif;background:#f3f5f4;color:#14231d;display:grid;place-items:center;min-height:100vh;margin:0}}
main{{max-width:28rem;padding:2rem;background:#fff;border:1px solid #d5dcd8;border-radius:10px}}
h1{{font-size:1.25rem;margin:0 0 .5rem}}p{{margin:.25rem 0}}
</style></head><body><main><h1>{title}</h1><p>{body}</p></main></body></html>"""


def page(title: str, body: str, status: int = 200) -> HTMLResponse:
    return HTMLResponse(PAGE.format(title=html.escape(title), body=html.escape(body)), status_code=status)


async def oauth_callback(request: Request) -> Response:
    from ..connectors.flows import complete_callback

    engine = request.app.state.engine
    params = dict(request.query_params)
    if engine.db.one("SELECT 1 FROM login_states WHERE state = ?", (params.get("state", ""),)):
        return await _google_login(request, engine, params)
    pending = engine.db.one("SELECT workspace_id, extra FROM oauth_states WHERE state = ?", (params.get("state", ""),))
    extra = loads(pending["extra"], {}) if pending else {}
    back = extra.get("return_to") if re.fullmatch(r"/[A-Za-z0-9_\-/]{0,119}", str(extra.get("return_to") or "")) else "/connections"
    ws_id = pending["workspace_id"] if pending else None
    if pending is None:  # not started from this app (or already used): no page to return to
        return page("Connection not completed", "This sign-in link has expired or was already used. Start the "
                    "connection again from OfinTech Desk.", 400)
    try:
        result = await complete_callback(engine, params)
    except AppError as exc:
        return _connect_back(engine, back, ws_id, None, exc.message)
    except Exception:
        log.exception("oauth callback failed")
        return _connect_back(engine, back, ws_id, None, "Something went wrong. The details are in the engine log.")
    return _connect_back(engine, back, ws_id, result["connection_id"], None)


def _connect_back(engine: Any, back: str, ws_id: str | None, connection_id: str | None, error: str | None) -> Response:
    """After a provider's sign-in, return to the app page the connection was started from.

    On the Team Server the browser tab that started it is sent back there (the app shows the result). The desktop app
    started the sign-in in the system browser, so that tab offers to return to the app, which reloads the page."""
    from urllib.parse import urlencode

    from starlette.responses import RedirectResponse

    query = {"ws": ws_id} if ws_id else {}
    query |= {"connected": connection_id} if connection_id else {"connect_error": (error or "")[:300]}
    if not engine.settings.is_desktop:
        return RedirectResponse(f"/#{back}?{urlencode(query)}", status_code=303, headers={"cache-control": "no-store"})
    label = engine.db.scalar("SELECT label FROM connections WHERE id = ?", (connection_id,)) if connection_id else None
    link = f"ofintech-desk://open{back}" + (f"?ws={ws_id}" if ws_id else "")
    title = "Connected" if connection_id else "Connection not completed"
    text = (f"{label} is connected." if connection_id else (error or "The connection was not completed."))
    return HTMLResponse(f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="refresh" content="1;url={html.escape(link)}">
<title>{html.escape(title)}</title><style>body{{font:16px system-ui,sans-serif;max-width:32rem;margin:15vh auto;
padding:0 1rem;color:#1d2433}}a{{color:#1e4fa3}}</style></head><body><h1>{html.escape(title)}</h1>
<p>{html.escape(text)}</p><p><a href="{html.escape(link)}">Return to OfinTech Desk</a>. You can close this tab.</p></body></html>""",
                        status_code=200 if connection_id else 400, headers={"cache-control": "no-store"})


async def _google_login(request: Request, engine: Any, params: dict[str, str]) -> Response:
    from starlette.responses import RedirectResponse

    from .auth import google_callback
    from ..web import SESSION_COOKIE

    try:
        token, message = await google_callback(engine, request, params)
    except AppError as exc:
        token, message = None, exc.message
    if token is None and message.startswith("handoff:"):
        link = f"ofintech-desk://open/auth?handoff={message[8:]}"
        return HTMLResponse(f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="refresh" content="0;url={link}">
<title>Return to OfinTech Desk</title><style>body{{font:16px system-ui,sans-serif;max-width:32rem;margin:15vh auto;
padding:0 1rem;color:#1d2433}}a{{color:#1e4fa3}}</style></head><body><h1>You are signed in</h1>
<p>Return to the OfinTech Desk app. If it did not open, <a href="{link}">open OfinTech Desk</a>.</p>
<p>You can close this tab.</p></body></html>""", headers={"cache-control": "no-store"})
    if token is None:
        return page("Sign-in not completed", message, 400)
    resp = RedirectResponse("/#/", status_code=303)
    resp.set_cookie(SESSION_COOKIE, token, max_age=engine.settings.session_days * 86400, httponly=True,
                    secure=engine.settings.public_url.startswith("https://"), samesite="strict", path="/")
    return resp


async def telegram_hook(request: Request) -> Response:
    engine = request.app.state.engine
    cid = request.path_params["cid"]
    conn = engine.db.one("SELECT * FROM connections WHERE id = ? AND provider = 'telegram'", (cid,))
    if conn is None:
        return PlainTextResponse("not found", status_code=404)
    secret = engine.secrets.get(conn["secret_id"], workspace_id=conn["workspace_id"])
    given = request.headers.get("x-telegram-bot-api-secret-token", "")
    if not hmac.compare_digest(given, secret.get("webhook_secret", "")):
        return PlainTextResponse("forbidden", status_code=403)
    try:
        update = json.loads(await request.body())
    except json.JSONDecodeError:
        return PlainTextResponse("bad request", status_code=400)
    import asyncio

    asyncio.get_running_loop().create_task(engine.channels.handle_telegram_update(conn, update))
    return JSONResponse({"ok": True})


async def whatsapp_hook(request: Request) -> Response:
    engine = request.app.state.engine
    cid = request.path_params["cid"]
    conn = engine.db.one("SELECT * FROM connections WHERE id = ? AND provider = 'whatsapp'", (cid,))
    if conn is None:
        return PlainTextResponse("not found", status_code=404)
    secret = engine.secrets.get(conn["secret_id"], workspace_id=conn["workspace_id"])
    if request.method == "GET":
        q = request.query_params
        if q.get("hub.mode") == "subscribe" and hmac.compare_digest(q.get("hub.verify_token", ""),
                                                                    secret.get("verify_token", "")):
            return PlainTextResponse(q.get("hub.challenge", ""))
        return PlainTextResponse("forbidden", status_code=403)
    body = await request.body()
    signature = request.headers.get("x-hub-signature-256", "")
    expected = "sha256=" + hmac_sha256_hex(secret["app_secret"], body)
    if not hmac.compare_digest(signature, expected):
        return PlainTextResponse("bad signature", status_code=401)
    try:
        payload: dict[str, Any] = json.loads(body)
    except json.JSONDecodeError:
        return PlainTextResponse("bad request", status_code=400)
    await engine.channels.handle_whatsapp_payload(conn, payload)
    return JSONResponse({"ok": True})


async def ingest_accounting(request: Request) -> Response:
    """Open Accounting Connector, push mode: authenticated by the connection's ingest key."""
    import time
    from collections import defaultdict, deque

    from ..connectors.open_api import ingest
    from ..security import token_hash

    engine = request.app.state.engine
    auth = request.headers.get("authorization", "")
    key = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
    row = engine.db.one("SELECT * FROM ingest_keys WHERE key_hash = ?", (token_hash(key),)) if key.startswith("odk_") \
        else None
    if row is None:
        return JSONResponse({"error": "unauthorized", "message": "Missing or wrong ingest key."}, status_code=401)
    hits: dict[str, deque[float]] = engine.__dict__.setdefault("_ingest_hits", defaultdict(deque))
    q, t = hits[row["id"]], time.monotonic()
    while q and t - q[0] > 60:
        q.popleft()
    if len(q) >= 60:
        return JSONResponse({"error": "rate_limited", "message": "At most 60 requests a minute."}, status_code=429)
    q.append(t)
    if int(request.headers.get("content-length") or 0) > 100 * 1024 * 1024:
        return JSONResponse({"error": "too_large", "message": "At most 100 MB per request."}, status_code=413)
    conn = engine.db.one("SELECT * FROM connections WHERE id = ? AND workspace_id = ?",
                         (row["connection_id"], row["workspace_id"]))
    if conn is None:
        return JSONResponse({"error": "unauthorized", "message": "The connection was removed."}, status_code=401)
    try:
        payload = json.loads(await request.body())
        result = await ingest(engine, conn, request.path_params["kind"], payload)
    except json.JSONDecodeError:
        return JSONResponse({"error": "invalid", "message": "Send JSON."}, status_code=400)
    except AppError as exc:
        return JSONResponse(exc.to_dict(), status_code=exc.status)
    engine.db.execute("UPDATE ingest_keys SET last_used_at = ? WHERE id = ?", (time.time(), row["id"]))
    engine.audit(conn["workspace_id"], None, "connection.ingest", conn["id"],
                 {"kind": result.kind, "rows": result.rows})
    return JSONResponse(result.model_dump())


async def workflow_hook(request: Request) -> Response:
    """Start a workflow from another system, authenticated by the workflow's own secret as a bearer token (only its
    hash is stored). The body (JSON or text, up to 256 KB) becomes {{trigger.data}} and {{trigger.text}}."""
    import time
    from collections import defaultdict, deque

    from ..security import token_hash

    engine = request.app.state.engine
    wid = request.path_params["wid"]
    wf = engine.db.one("SELECT * FROM workflows WHERE id = ? AND webhook_hash IS NOT NULL", (wid,))
    deny = JSONResponse({"error": "unauthorized", "message": "Unknown workflow or wrong secret."}, status_code=401)
    if wf is None:
        return deny
    if int(request.headers.get("content-length") or 0) > 256 * 1024:
        return JSONResponse({"error": "too_large", "message": "At most 256 KB per event."}, status_code=413)
    body = await request.body()
    if len(body) > 256 * 1024:
        return JSONResponse({"error": "too_large", "message": "At most 256 KB per event."}, status_code=413)
    auth = request.headers.get("authorization", "")
    bearer = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
    ok = bool(bearer) and hmac.compare_digest(token_hash(bearer), wf["webhook_hash"])
    if not ok:
        return deny
    hits: dict[str, deque[float]] = engine.__dict__.setdefault("_hook_hits", defaultdict(deque))
    q, t = hits[wid], time.monotonic()
    while q and t - q[0] > 60:
        q.popleft()
    if len(q) >= 30:
        return JSONResponse({"error": "rate_limited", "message": "At most 30 events a minute."}, status_code=429)
    q.append(t)
    if not wf["enabled"]:
        return JSONResponse({"error": "disabled", "message": "This workflow is switched off."}, status_code=409)
    try:
        data: Any = json.loads(body) if body.strip() else {}
    except json.JSONDecodeError:
        data = None
    text = json.dumps(data, ensure_ascii=False, indent=1)[:8000] if data is not None else \
        body.decode("utf-8", "replace")[:8000]
    workspace = engine.db.one("SELECT * FROM workspaces WHERE id = ?", (wf["workspace_id"],))
    trigger = {"type": "webhook", "data": data if isinstance(data, (dict, list)) else None, "text": text,
               "received_at": time.time()}
    try:
        run_id = engine.workflows.start(workspace, {**wf, "definition": json.loads(wf["definition"])}, trigger,
                                        wf["created_by"])
    except AppError as exc:
        return JSONResponse(exc.to_dict(), status_code=exc.status)
    return JSONResponse({"run_id": run_id}, status_code=202)


# --- sign-in and sign-up from the public website -------------------------------------------------------------------
#
# The website (DESK_SITE_URL, e.g. https://ofintech.ai) has plain HTML forms that post here. The website and the
# Team Server share a registrable domain, so the session cookie set on the redirect is sent to the app. Failures go
# back to the website with a short code in the URL fragment, which the page shows without any script.

AUTH_FORM_FIELDS = 8


def _allowed_origin(engine: Any, request: Request) -> bool:
    origin = request.headers.get("origin") or ""
    if not origin:
        referer = request.headers.get("referer") or ""
        origin = referer.split("/", 3)[0] + "//" + referer.split("/", 3)[2] if referer.count("/") >= 2 else ""
    allowed = {engine.settings.public_url.rstrip("/")}
    if engine.settings.site_url:
        allowed.add(engine.settings.site_url.rstrip("/"))
    return origin.rstrip("/") in allowed


def _back(engine: Any, code: str) -> Response:
    from starlette.responses import RedirectResponse

    if engine.settings.site_url:
        return RedirectResponse(f"{engine.settings.site_url}/#{code}", status_code=303)
    return RedirectResponse(f"{engine.settings.public_url.rstrip('/')}/#/login?error={code}", status_code=303)


def _into_app(engine: Any, token: str) -> Response:
    from starlette.responses import RedirectResponse

    from .auth import set_session_cookie

    resp = RedirectResponse(f"{engine.settings.public_url.rstrip('/')}/#/", status_code=303)
    set_session_cookie(engine, resp, token)
    resp.headers["cache-control"] = "no-store"
    return resp


async def _form(request: Request) -> dict[str, str]:
    async with request.form(max_files=0, max_fields=AUTH_FORM_FIELDS) as form:
        return {k: str(v)[:300] for k, v in form.items() if isinstance(v, str)}


async def auth_form_sign_in(request: Request) -> Response:
    from .auth import create_session, login_user

    engine = request.app.state.engine
    if engine.settings.is_desktop:
        return _back(engine, "desktop")
    if not _allowed_origin(engine, request):
        return page("Sign-in refused", "This form was sent from a site that is not allowed.", 403)
    f = await _form(request)
    email, password = f.get("email", "").strip(), f.get("password", "")
    if not email or not password or len(email) > 254:
        return _back(engine, "signin-error")
    try:
        uid = login_user(engine, email=email, password=password, client_host=request.client.host if request.client else "-")
    except AppError as exc:
        return _back(engine, {"rate_limited": "signin-rate", "disabled": "signin-disabled"}.get(exc.code or "", "signin-error"))
    return _into_app(engine, create_session(engine, request, uid))


async def auth_form_sign_up(request: Request) -> Response:
    from .auth import create_session, signup_user

    engine = request.app.state.engine
    if engine.settings.is_desktop:
        return _back(engine, "desktop")
    if not _allowed_origin(engine, request):
        return page("Sign-up refused", "This form was sent from a site that is not allowed.", 403)
    f = await _form(request)
    email, name, password = f.get("email", "").strip(), f.get("name", "").strip(), f.get("password", "")
    if not email or "@" not in email or "." not in email.rsplit("@", 1)[-1] or not name or not password:
        return _back(engine, "signup-invalid")
    try:
        uid = signup_user(engine, email=email, name=name[:120], password=password, invite_token=None,
                          client_host=request.client.host if request.client else "-")
    except AppError as exc:
        code = {"invitation_only": "signup-invitation", "exists": "signup-exists", "rate_limited": "signup-rate",
                "weak_password": "signup-password"}.get(exc.code or "", "signup-password" if exc.status == 422 else "signup-invalid")
        return _back(engine, code)
    return _into_app(engine, create_session(engine, request, uid))


async def auth_form_google(request: Request) -> Response:
    """A plain link on the website starts Google sign-in; the callback sets the cookie and opens the app."""
    from starlette.responses import RedirectResponse

    from .auth import _rate_limit, google_start_url

    engine = request.app.state.engine
    if engine.settings.is_desktop:
        return _back(engine, "desktop")
    try:
        _rate_limit(engine, f"google-start:{request.client.host if request.client else '-'}", limit=30)
        url = google_start_url(engine)
    except AppError as exc:
        return _back(engine, "signin-rate" if exc.code == "rate_limited" else "google-off")
    return RedirectResponse(url, status_code=303)


async def incoming_webhook(request: Request) -> Response:
    """A delivery from another system to a webhook made under Connections. The sender proves itself with the hook's
    secret (bearer or HMAC signature); the delivery is logged and may start the linked workflow."""
    engine = request.app.state.engine
    hook = engine.db.one("SELECT * FROM webhooks WHERE id = ? AND direction = 'in'", (request.path_params["hid"],))
    deny = JSONResponse({"error": "unauthorized", "message": "Unknown webhook or wrong secret."}, status_code=401)
    if hook is None:
        return deny
    if int(request.headers.get("content-length") or 0) > 256 * 1024:
        return JSONResponse({"error": "too_large", "message": "At most 256 KB per delivery."}, status_code=413)
    body = await request.body()
    if len(body) > 256 * 1024:
        return JSONResponse({"error": "too_large", "message": "At most 256 KB per delivery."}, status_code=413)
    if not engine.webhooks.verify_incoming(hook, dict(request.headers), body):
        engine.webhooks.record_rejected(hook, "missing or wrong secret")
        return deny
    if engine.webhooks.rate_limited(hook["id"]):
        return JSONResponse({"error": "rate_limited", "message": "At most 60 deliveries a minute."}, status_code=429)
    if not hook["enabled"]:
        return JSONResponse({"error": "disabled", "message": "This webhook is switched off."}, status_code=409)
    event = (request.headers.get("x-ofintech-event") or request.headers.get("x-github-event") or "")[:80] or None
    result = engine.webhooks.receive(hook, event, body)
    return JSONResponse(result, status_code=202)


async def connector_spec(request: Request) -> Response:
    from ..connectors.open_api import openapi

    engine = request.app.state.engine
    return JSONResponse(openapi(engine.settings.public_url or "https://desk.example.com"))


routes = [
    Route("/api/ingest/v1/accounting/{kind}", ingest_accounting, methods=["POST"]),
    Route("/api/connector-spec/openapi.json", connector_spec, methods=["GET"]),
    Route("/api/hooks/workflows/{wid}", workflow_hook, methods=["POST"]),
    Route("/api/hooks/in/{hid}", incoming_webhook, methods=["POST"]),
    Route("/auth/form/sign-in", auth_form_sign_in, methods=["POST"]),
    Route("/auth/form/sign-up", auth_form_sign_up, methods=["POST"]),
    Route("/auth/form/google", auth_form_google, methods=["GET"]),
    Route("/oauth/callback", oauth_callback, methods=["GET"]),
    Route("/hooks/telegram/{cid}", telegram_hook, methods=["POST"]),
    Route("/hooks/whatsapp/{cid}", whatsapp_hook, methods=["GET", "POST"]),
]
