import hashlib
import hmac
import json
import time
import unittest

import httpx2 as httpx

from tests.helpers import Harness

BOT = "123456789:AAH" + "x" * 32


class TelegramChannel(unittest.TestCase):
    def setUp(self) -> None:
        self.h = Harness(script=["Overdue receivables total 1,200.00 across 1 invoice."])
        self.ws = self.h.setup_workspace()
        self.sent: list[dict] = []
        tg = f"https://api.telegram.org/bot{BOT}"
        self.h.router.add("POST", f"{tg}/getMe", {"ok": True, "result": {"id": 1, "username": "acme_fin_bot"}})
        self.h.router.add("POST", f"{tg}/deleteWebhook", {"ok": True, "result": True})

        def send(req: httpx.Request) -> httpx.Response:
            self.sent.append(json.loads(req.content))
            return httpx.Response(200, json={"ok": True, "result": {}})

        self.h.router.add("POST", f"{tg}/sendMessage", send)

    def tearDown(self) -> None:
        self.h.close()

    def deliver(self, chat_id: int, text: str, chat_type: str = "private") -> None:
        conn = self.h.engine.db.one("SELECT * FROM connections WHERE provider = 'telegram'")
        update = {"update_id": 1, "message": {"chat": {"id": chat_id, "type": chat_type},
                                              "from": {"first_name": "Sam"}, "text": text}}
        self.h.client.portal.call(self.h.engine.channels.handle_telegram_update, conn, update)

    def wait_sent(self, n: int) -> None:
        for _ in range(200):
            if len(self.sent) >= n:
                return
            time.sleep(0.05)
        raise AssertionError(f"expected {n} messages, got {self.sent}")

    def test_link_and_chat(self) -> None:
        h = self.h
        r = h.api("POST", f"/api/w/{self.ws}/connections/telegram", json={"bot_token": BOT})
        self.assertEqual(r["mode"], "polling")
        h.api("POST", f"/api/w/{self.ws}/connections/telegram", json={"bot_token": "nope-not-a-token-at-all"}, expect=422)
        cid = r["id"]
        # unlinked chats get no data
        self.deliver(42, "What is overdue?")
        self.wait_sent(1)
        self.assertIn("not linked", self.sent[-1]["text"])
        self.assertEqual(h.llm.calls, [])
        # group chats are ignored entirely
        self.deliver(-100, "hello", chat_type="group")
        time.sleep(0.1)
        self.assertEqual(len(self.sent), 1)
        pair = h.api("POST", f"/api/w/{self.ws}/connections/{cid}/pair")
        self.assertTrue(pair["link"].startswith("https://t.me/acme_fin_bot?start="))
        self.deliver(42, "/start WRONGCOD")
        self.wait_sent(2)
        self.assertIn("not valid", self.sent[-1]["text"])
        self.deliver(42, f"/start {pair['code']}")
        self.wait_sent(3)
        self.assertIn("Linked to the workspace 'Acme Ltd'", self.sent[-1]["text"])
        # the code is single-use
        self.deliver(43, f"/link {pair['code']}")
        self.wait_sent(4)
        self.assertIn("not valid", self.sent[-1]["text"])
        # a linked chat talks to the agent; the answer comes back as plain text
        self.deliver(42, "What is overdue?")
        self.wait_sent(5)
        self.assertEqual(self.sent[-1]["text"], "Overdue receivables total 1,200.00 across 1 invoice.")
        self.assertEqual(self.sent[-1]["chat_id"], "42")
        threads = h.api("GET", f"/api/w/{self.ws}/threads")
        self.assertEqual(threads[0]["channel"]["type"], "telegram")
        members = h.api("GET", f"/api/w/{self.ws}/members")
        self.assertEqual(members[0]["channels"], ["telegram"])
        # workflow notifications reach linked members
        wf = h.api("POST", f"/api/w/{self.ws}/workflows", json={"name": "Cash alert", "definition": {"steps": [
            {"id": "n", "name": "n", "type": "notify", "channels": ["telegram"], "message": "Cash below buffer"}]}})
        run_id = h.api("POST", f"/api/w/{self.ws}/workflows/{wf['id']}/run", json={})["run_id"]
        h.wait_workflow(run_id)
        self.wait_sent(6)
        self.assertEqual(self.sent[-1]["text"], "Cash below buffer")


class WhatsAppChannel(unittest.TestCase):
    def setUp(self) -> None:
        self.h = Harness(mode="server")
        c = self.h.client
        r = c.post("/api/auth/signup", json={"email": "o@acme.test", "name": "Olivia", "password": "Correct-Horse-9-battery"})
        c.headers["authorization"] = f"Bearer {r.json()['token']}"
        self.ws = c.post("/api/workspaces", json={"name": "Acme"}).json()["id"]
        self.sent: list[dict] = []

        def send(req: httpx.Request) -> httpx.Response:
            self.sent.append(json.loads(req.content))
            return httpx.Response(200, json={"messages": [{"id": "wamid.1"}]})

        self.h.router.add("POST", "https://graph.facebook.com/v26.0/1098765/messages", send)

    def tearDown(self) -> None:
        self.h.close()

    def test_verify_signature_and_link(self) -> None:
        h = self.h
        r = h.api("POST", f"/api/w/{self.ws}/connections/whatsapp", json={
            "phone_number_id": "1098765", "display_number": "+44 7700 900123", "access_token": "EAAG" + "z" * 30,
            "app_secret": "app-secret-123"})
        cid, verify = r["id"], r["verify_token"]
        self.assertEqual(r["webhook_url"], f"https://desk.example.com/hooks/whatsapp/{cid}")
        c = h.client
        ok = c.get(f"/hooks/whatsapp/{cid}", params={"hub.mode": "subscribe", "hub.verify_token": verify,
                                                     "hub.challenge": "12345"})
        self.assertEqual(ok.text, "12345")
        bad = c.get(f"/hooks/whatsapp/{cid}", params={"hub.mode": "subscribe", "hub.verify_token": "x",
                                                      "hub.challenge": "1"})
        self.assertEqual(bad.status_code, 403)
        pair = h.api("POST", f"/api/w/{self.ws}/connections/{cid}/pair")
        self.assertTrue(pair["link"].startswith("https://wa.me/447700900123?text=LINK%20"))
        payload = {"object": "whatsapp_business_account", "entry": [{"id": "1", "changes": [{"field": "messages", "value": {
            "messaging_product": "whatsapp", "metadata": {"phone_number_id": "1098765"},
            "contacts": [{"wa_id": "447700900999", "profile": {"name": "Sam"}}],
            "messages": [{"from": "447700900999", "id": "wamid.in", "type": "text",
                          "text": {"body": f"LINK {pair['code']}"}}]}}]}]}
        body = json.dumps(payload).encode()
        r = c.post(f"/hooks/whatsapp/{cid}", content=body, headers={"x-hub-signature-256": "sha256=deadbeef"})
        self.assertEqual(r.status_code, 401)
        sig = "sha256=" + hmac.new(b"app-secret-123", body, hashlib.sha256).hexdigest()
        r = c.post(f"/hooks/whatsapp/{cid}", content=body, headers={"x-hub-signature-256": sig})
        self.assertEqual(r.status_code, 200)
        for _ in range(100):
            if self.sent:
                break
            time.sleep(0.05)
        self.assertEqual(self.sent[0]["to"], "447700900999")
        self.assertIn("Linked to the workspace 'Acme'", self.sent[0]["text"]["body"])
        self.assertEqual(h.engine.db.scalar("SELECT COUNT(*) FROM channel_links"), 1)

    def test_whatsapp_needs_https(self) -> None:
        h2 = Harness(mode="desktop", public_url="http://localhost:47821")
        try:
            ws = h2.setup_workspace(with_model=False)
            r = h2.api("POST", f"/api/w/{ws}/connections/whatsapp", json={
                "phone_number_id": "1098765", "display_number": "+447700900123", "access_token": "E" * 30,
                "app_secret": "s" * 12}, expect=409)
            self.assertEqual(r["error"], "needs_public_url")
        finally:
            h2.close()


if __name__ == "__main__":
    unittest.main()
