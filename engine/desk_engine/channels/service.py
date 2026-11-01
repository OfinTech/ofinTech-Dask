"""Telegram and WhatsApp as front ends to the agent.

Only chats linked to a workspace member get answers: a member opens Connections → Telegram (or
WhatsApp) → Link my account, and sends the one-time code to the bot. Group chats are ignored.
Actions that need approval are approved in the app, not in the chat.
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import TYPE_CHECKING, Any

import httpx2 as httpx

from ..db import dumps, loads, new_id, now
from ..errors import AppError, Forbidden, Invalid, NotConfigured, UpstreamError
from ..security import new_token, token_hash

if TYPE_CHECKING:
    from ..app import Engine

log = logging.getLogger("desk.channels")
PAIRING_TTL = 15 * 60
MAX_LEN = 4000


def chunks(text: str, size: int = MAX_LEN) -> list[str]:
    text = text.strip() or "(empty)"
    out = []
    while text:
        if len(text) <= size:
            out.append(text)
            break
        cut = text.rfind("\n", 0, size)
        cut = cut if cut > size // 2 else size
        out.append(text[:cut])
        text = text[cut:].lstrip()
    return out


def plain(markdown: str) -> str:
    """Chat apps get plain text: strip Markdown emphasis and headings, keep tables readable."""
    text = re.sub(r"^#{1,6}\s*", "", markdown, flags=re.M)
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    text = re.sub(r"(?<!\w)[*_](.+?)[*_](?!\w)", r"\1", text)
    text = re.sub(r"`([^`]+)`", r"\1", text)
    text = re.sub(r"^\|?\s*:?-{3,}.*$", "", text, flags=re.M)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


class Telegram:
    def __init__(self, engine: Engine, token: str) -> None:
        self.engine = engine
        self.base = f"https://api.telegram.org/bot{token}"

    async def call(self, method: str, payload: dict[str, Any] | None = None, timeout: float = 30) -> Any:
        try:
            async with httpx.AsyncClient(timeout=timeout + 10, transport=self.engine.http_transport) as client:
                resp = await client.post(f"{self.base}/{method}", json=payload or {})
        except httpx.HTTPError as exc:
            # the request URL carries the bot token, so the transport's own message is never passed on
            raise UpstreamError(f"Telegram {method} failed: {type(exc).__name__}", code="telegram_error") from None
        data = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
        if resp.status_code >= 400 or not data.get("ok"):
            raise UpstreamError(f"Telegram {method} failed: {data.get('description') or resp.status_code}",
                                code="telegram_error")
        return data["result"]

    async def send(self, chat_id: str, text: str) -> None:
        for part in chunks(text):
            await self.call("sendMessage", {"chat_id": chat_id, "text": part, "link_preview_options": {"is_disabled": True}})


class WhatsApp:
    def __init__(self, engine: Engine, config: dict[str, Any], secret: dict[str, Any]) -> None:
        self.engine = engine
        self.config = config
        self.token = secret["access_token"]
        self.url = (f"https://graph.facebook.com/{engine.settings.whatsapp_graph_version}/"
                    f"{config['phone_number_id']}/messages")

    async def _post(self, body: dict[str, Any]) -> Any:
        async with httpx.AsyncClient(timeout=30, transport=self.engine.http_transport) as client:
            resp = await client.post(self.url, json=body, headers={"Authorization": f"Bearer {self.token}"})
        if resp.status_code >= 400:
            err = resp.json().get("error", {}) if resp.headers.get("content-type", "").startswith("application/json") else {}
            raise UpstreamError(f"WhatsApp send failed: {err.get('message') or resp.status_code} "
                                f"(code {err.get('code')})", code="whatsapp_error")
        return resp.json()

    async def send(self, to: str, text: str, *, notification: bool = False) -> None:
        template = self.config.get("template_name")
        if notification and template:
            # outside the 24-hour customer service window only approved templates can be sent
            await self._post({"messaging_product": "whatsapp", "to": to, "type": "template",
                              "template": {"name": template, "language": {"code": self.config.get("template_language", "en")},
                                           "components": [{"type": "body", "parameters": [
                                               {"type": "text", "text": plain(text)[:1000]}]}]}})
            return
        for part in chunks(text, 4000):
            await self._post({"messaging_product": "whatsapp", "recipient_type": "individual", "to": to, "type": "text",
                              "text": {"preview_url": False, "body": part}})


class Channels:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine
        self.offsets: dict[str, int] = {}

    # -- setup -------------------------------------------------------------------------------------
    def _check_open(self, ws_id: str) -> None:
        from ..egress import workspace_sealed
        if workspace_sealed(self.engine, ws_id):
            raise Forbidden("This workspace keeps its data inside the organization, so Telegram and WhatsApp are off.",
                            code="egress_blocked")

    async def connect_telegram(self, ws_id: str, user_id: str, bot_token: str) -> dict[str, Any]:
        self._check_open(ws_id)
        if not re.match(r"^\d{5,15}:[A-Za-z0-9_-]{30,}$", bot_token.strip()):
            raise Invalid("That does not look like a Telegram bot token from @BotFather.")
        bot = await Telegram(self.engine, bot_token.strip()).call("getMe")
        cid = new_id("cn_")
        secret = {"bot_token": bot_token.strip(), "webhook_secret": new_token(24)}
        sid = self.engine.secrets.put("connection", secret, workspace_id=ws_id, secret_id=f"sec_{cid}")
        mode = "webhook" if self.engine.settings.public_url.startswith("https://") and not self.engine.settings.is_desktop \
            else "polling"
        self.engine.db.insert("connections", {"id": cid, "workspace_id": ws_id, "provider": "telegram",
                                              "label": f"Telegram: @{bot.get('username')}", "status": "connected",
                                              "config": dumps({"bot_username": bot.get("username"), "mode": mode}),
                                              "secret_id": sid, "created_by": user_id, "created_at": now()})
        tg = Telegram(self.engine, secret["bot_token"])
        if mode == "webhook":
            await tg.call("setWebhook", {"url": f"{self.engine.settings.public_url.rstrip('/')}/hooks/telegram/{cid}",
                                         "secret_token": secret["webhook_secret"], "allowed_updates": ["message"]})
        else:
            await tg.call("deleteWebhook", {"drop_pending_updates": False})
        return {"id": cid, "mode": mode, "bot_username": bot.get("username")}

    def connect_whatsapp(self, ws_id: str, user_id: str, *, phone_number_id: str, display_number: str,
                         access_token: str, app_secret: str, template_name: str | None,
                         template_language: str | None) -> dict[str, Any]:
        self._check_open(ws_id)
        if not self.engine.settings.public_url.startswith("https://"):
            raise NotConfigured("WhatsApp delivers messages to a public HTTPS webhook. Run the Team Server (or set "
                                "DESK_PUBLIC_URL to an HTTPS tunnel) to connect WhatsApp.", code="needs_public_url")
        if not phone_number_id.isdigit():
            raise Invalid("Phone number ID is the numeric ID from WhatsApp Manager / the Meta app dashboard.")
        cid = new_id("cn_")
        verify_token = new_token(18)
        sid = self.engine.secrets.put("connection", {"access_token": access_token.strip(), "app_secret": app_secret.strip(),
                                                     "verify_token": verify_token},
                                      workspace_id=ws_id, secret_id=f"sec_{cid}")
        config = {"phone_number_id": phone_number_id, "display_number": re.sub(r"[^\d+]", "", display_number),
                  "template_name": template_name or None, "template_language": template_language or "en"}
        self.engine.db.insert("connections", {"id": cid, "workspace_id": ws_id, "provider": "whatsapp",
                                              "label": f"WhatsApp: {display_number}", "status": "connected",
                                              "config": dumps(config), "secret_id": sid, "created_by": user_id,
                                              "created_at": now()})
        return {"id": cid, "webhook_url": f"{self.engine.settings.public_url.rstrip('/')}/hooks/whatsapp/{cid}",
                "verify_token": verify_token}

    def pairing_code(self, ws_id: str, connection_id: str, user_id: str) -> dict[str, Any]:
        conn = self.engine.db.one("SELECT * FROM connections WHERE id = ? AND workspace_id = ? AND provider IN "
                                  "('telegram','whatsapp')", (connection_id, ws_id))
        if conn is None:
            raise Invalid("Not a messaging connection.")
        alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
        import secrets as _s

        code = "".join(_s.choice(alphabet) for _ in range(8))
        self.engine.db.execute("DELETE FROM pairing_codes WHERE expires_at < ? OR (user_id = ? AND connection_id = ?)",
                               (now(), user_id, connection_id))
        self.engine.db.insert("pairing_codes", {"code_hash": token_hash(code), "workspace_id": ws_id,
                                                "connection_id": connection_id, "user_id": user_id,
                                                "expires_at": now() + PAIRING_TTL})
        config = loads(conn["config"], {})
        if conn["provider"] == "telegram":
            link = f"https://t.me/{config.get('bot_username')}?start={code}"
            how = f"Open {link} and press Start, or send /link {code} to @{config.get('bot_username')}."
        else:
            number = config.get("display_number", "").lstrip("+")
            link = f"https://wa.me/{number}?text=LINK%20{code}"
            how = f"Send LINK {code} to {config.get('display_number')} on WhatsApp (or open {link})."
        return {"code": code, "link": link, "instructions": how, "expires_in": PAIRING_TTL}

    # -- incoming ----------------------------------------------------------------------------------
    async def handle_incoming(self, connection: dict[str, Any], external_id: str, display_name: str, text: str) -> None:
        engine = self.engine
        text = (text or "").strip()[:8000]
        ws_id = connection["workspace_id"]
        m = re.match(r"^(?:/start|/link|link)\s+([A-Za-z0-9]{8})$", text, re.I)
        if m:
            await self._link(connection, external_id, display_name, m.group(1).upper())
            return
        link = engine.db.one("SELECT * FROM channel_links WHERE connection_id = ? AND external_id = ?",
                             (connection["id"], external_id))
        if link is None:
            await self.reply(connection, external_id,
                             "This chat is not linked to OfinTech Desk. In the app open Connections, choose this "
                             "channel and press 'Link my account' to get a code.")
            return
        member = engine.db.one("SELECT m.role, u.disabled FROM members m JOIN users u ON u.id = m.user_id "
                               "WHERE m.workspace_id = ? AND m.user_id = ?", (ws_id, link["user_id"]))
        if member is None or member["disabled"]:
            await self.reply(connection, external_id, "Your access to this workspace has ended.")
            return
        workspace = engine.db.one("SELECT * FROM workspaces WHERE id = ?", (ws_id,))
        if text.lower() in {"/new", "new"}:
            engine.db.execute("UPDATE channel_links SET thread_id = NULL WHERE id = ?", (link["id"],))
            await self.reply(connection, external_id, "Started a new conversation.")
            return
        if text.lower() in {"/help", "help", "/start"}:
            await self.reply(connection, external_id, "Ask any finance question about your workspace, e.g. 'What is "
                             "overdue in receivables?'. Send /new to start a new conversation.")
            return
        wf = engine.workflows.match_channel_command(ws_id, text)
        if wf is not None:
            if member["role"] == "viewer":
                await self.reply(connection, external_id, "Running workflows needs the editor role.")
                return
            run_id = engine.workflows.start(workspace, {**wf, "definition": loads(wf["definition"], {})},
                                            {"type": "channel_command", "text": text, "channel": connection["provider"],
                                             "link_id": link["id"]}, link["user_id"])
            await self.reply(connection, external_id, f"Started workflow '{wf['name']}'. I'll send the result here.")
            engine.db.execute("INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET "
                              "value = excluded.value", (f"wf_reply:{run_id}", dumps({"link_id": link["id"]})))
            return
        thread_id = link["thread_id"]
        if not thread_id or engine.db.one("SELECT 1 FROM threads WHERE id = ?", (thread_id,)) is None:
            thread_id = new_id("t_")
            ts = now()
            engine.db.insert("threads", {"id": thread_id, "workspace_id": ws_id,
                                         "title": f"{connection['provider'].title()} · {display_name or external_id}"[:120],
                                         "created_by": link["user_id"], "visibility": "private",
                                         "channel": dumps({"type": connection["provider"], "link_id": link["id"]}),
                                         "created_at": ts, "updated_at": ts})
            engine.db.execute("UPDATE channel_links SET thread_id = ? WHERE id = ?", (thread_id, link["id"]))
        if engine.agent.thread_busy(thread_id):
            await self.reply(connection, external_id, "I'm still working on your previous message (or waiting for an "
                             "approval in the app).")
            return
        try:
            engine.agent.start_turn(workspace, link["user_id"], thread_id, text,
                                    parent={"type": "channel", "link_id": link["id"]}, source=connection["provider"])
        except AppError as exc:
            await self.reply(connection, external_id, exc.message)

    async def _link(self, connection: dict[str, Any], external_id: str, display_name: str, code: str) -> None:
        engine = self.engine
        row = engine.db.one("SELECT * FROM pairing_codes WHERE code_hash = ? AND connection_id = ?",
                            (token_hash(code), connection["id"]))
        if row is None or row["expires_at"] < now():
            await self.reply(connection, external_id, "That code is not valid or has expired. Create a new one in the app.")
            return
        with engine.db.tx() as c:
            c.execute("DELETE FROM pairing_codes WHERE code_hash = ?", (row["code_hash"],))
            c.execute("DELETE FROM channel_links WHERE connection_id = ? AND external_id = ?",
                      (connection["id"], external_id))
            c.execute("INSERT INTO channel_links (id, workspace_id, connection_id, external_id, display_name, user_id, "
                      "created_at) VALUES (?,?,?,?,?,?,?)", (new_id("cl_"), connection["workspace_id"], connection["id"],
                                                             external_id, display_name[:120], row["user_id"], now()))
        engine.audit(connection["workspace_id"], row["user_id"], "channel.linked", connection["id"],
                     {"provider": connection["provider"]})
        ws = engine.db.one("SELECT name FROM workspaces WHERE id = ?", (connection["workspace_id"],))
        await self.reply(connection, external_id, f"Linked to the workspace '{ws['name']}'. Ask me anything about your "
                         "finances. Send /new to start a new conversation.")

    async def reply(self, connection: dict[str, Any], external_id: str, text: str, *, notification: bool = False) -> bool:
        from ..egress import workspace_sealed
        if workspace_sealed(self.engine, connection["workspace_id"]):
            log.warning("not replying via %s: workspace %s keeps its data inside", connection["provider"],
                        connection["workspace_id"])
            return False
        secret = self.engine.secrets.get(connection["secret_id"], workspace_id=connection["workspace_id"])
        try:
            if connection["provider"] == "telegram":
                await Telegram(self.engine, secret["bot_token"]).send(external_id, plain(text))
            else:
                await WhatsApp(self.engine, loads(connection["config"], {}), secret).send(external_id, plain(text),
                                                                                          notification=notification)
        except AppError as exc:
            log.warning("reply via %s failed: %s", connection["provider"], exc.message)
            self.engine.db.update("connections", connection["id"], {"last_error": exc.message[:300]},
                                  workspace_id=connection["workspace_id"])
            return False
        return True

    async def send_to_member(self, ws_id: str, user_id: str, provider: str, text: str) -> int:
        links = self.engine.db.all("SELECT l.*, c.provider FROM channel_links l JOIN connections c ON c.id = l.connection_id "
                                   "WHERE l.workspace_id = ? AND l.user_id = ? AND c.provider = ?", (ws_id, user_id, provider))
        sent = 0
        for link in links:
            conn = self.engine.db.one("SELECT * FROM connections WHERE id = ?", (link["connection_id"],))
            sent += await self.reply(conn, link["external_id"], text, notification=True)
        return sent

    # -- results back to the chat --------------------------------------------------------------------
    async def on_agent_done(self, run: dict[str, Any], parent: dict[str, Any]) -> None:
        link = self.engine.db.one("SELECT * FROM channel_links WHERE id = ?", (parent["link_id"],))
        if link is None:
            return
        conn = self.engine.db.one("SELECT * FROM connections WHERE id = ?", (link["connection_id"],))
        state = loads(run["state"], {})
        if run["status"] == "done":
            await self.reply(conn, link["external_id"], state.get("final_text") or "(no answer)")
        elif run["status"] == "waiting_approval":
            pending = self.engine.db.all("SELECT summary FROM approvals WHERE source = 'agent' AND source_id = ? AND "
                                         "status = 'pending'", (run["id"],))
            await self.reply(conn, link["external_id"], "This needs approval in the OfinTech Desk app (Inbox):\n- "
                             + "\n- ".join(p["summary"][:300] for p in pending))
        elif run["status"] == "error":
            await self.reply(conn, link["external_id"], f"Sorry, that failed: {run.get('error')}")

    async def on_workflow_finished(self, run_id: str) -> None:
        key = f"wf_reply:{run_id}"
        target = loads(self.engine.db.scalar("SELECT value FROM settings WHERE key = ?", (key,)), None)
        if not target:
            return
        self.engine.db.execute("DELETE FROM settings WHERE key = ?", (key,))
        run = self.engine.db.one("SELECT * FROM workflow_runs WHERE id = ?", (run_id,))
        link = self.engine.db.one("SELECT * FROM channel_links WHERE id = ?", (target["link_id"],))
        if run is None or link is None:
            return
        conn = self.engine.db.one("SELECT * FROM connections WHERE id = ?", (link["connection_id"],))
        context = loads(run["context"], {})
        outputs = [v.get("output") for v in context.get("steps", {}).values() if isinstance(v, dict)]
        last = next((o for o in reversed(outputs) if isinstance(o, str) and o.strip()), None)
        status = {"done": "finished", "stopped": "stopped at a condition", "rejected": "was rejected",
                  "failed": f"failed: {run['error']}"}.get(run["status"], run["status"])
        await self.reply(conn, link["external_id"], f"Workflow {status}." + (f"\n\n{last}" if last else ""))

    # -- Telegram long polling (desktop, or a server without public HTTPS) ----------------------------
    async def poll_telegram_once(self) -> None:
        for conn in self.engine.db.all("SELECT * FROM connections WHERE provider = 'telegram' AND status = 'connected'"):
            if loads(conn["config"], {}).get("mode") != "polling":
                continue
            secret = self.engine.secrets.get(conn["secret_id"], workspace_id=conn["workspace_id"])
            tg = Telegram(self.engine, secret["bot_token"])
            offset = self.offsets.get(conn["id"])
            try:
                updates = await tg.call("getUpdates", {"timeout": 0, "allowed_updates": ["message"],
                                                       **({"offset": offset} if offset else {})}, timeout=10)
            except AppError as exc:
                log.warning("telegram polling: %s", exc.message)
                continue
            for upd in updates:
                self.offsets[conn["id"]] = upd["update_id"] + 1
                await self.handle_telegram_update(conn, upd)

    async def handle_telegram_update(self, conn: dict[str, Any], update: dict[str, Any]) -> None:
        msg = update.get("message") or {}
        chat = msg.get("chat") or {}
        if chat.get("type") != "private" or "text" not in msg:
            return
        sender = msg.get("from") or {}
        name = " ".join(x for x in [sender.get("first_name"), sender.get("last_name")] if x) or sender.get("username", "")
        await self.handle_incoming(conn, str(chat["id"]), name, msg["text"])

    async def handle_whatsapp_payload(self, conn: dict[str, Any], payload: dict[str, Any]) -> int:
        handled = 0
        for entry in payload.get("entry", []):
            for change in entry.get("changes", []):
                value = change.get("value", {})
                if str(value.get("metadata", {}).get("phone_number_id")) != str(loads(conn["config"], {}).get("phone_number_id")):
                    continue
                names = {c.get("wa_id"): (c.get("profile") or {}).get("name", "") for c in value.get("contacts", [])}
                for m in value.get("messages", []):
                    if m.get("type") != "text":
                        await self.reply(conn, m.get("from", ""), "Please send text messages; files are not supported here yet.")
                        continue
                    asyncio.get_running_loop().create_task(
                        self.handle_incoming(conn, m["from"], names.get(m["from"], ""), m.get("text", {}).get("body", "")))
                    handled += 1
        return handled
