"""Webhooks under Connections: incoming deliveries (bearer and HMAC, rejected, rate limit, workflow start, log) and
outgoing events (queued from the audit log, signed, delivered, retried, test event, redelivery)."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
import unittest

import httpx2 as httpx

from desk_engine.security import hmac_sha256_hex

from .helpers import Harness


class Incoming(unittest.TestCase):
    def setUp(self) -> None:
        self.h = Harness()
        self.ws = self.h.setup_workspace()
        self.anon = self.h.client.__class__(self.h.app, base_url="http://localhost")  # another system: no session

    def tearDown(self) -> None:
        self.h.close()

    def test_bearer_signature_log_and_workflow(self) -> None:
        h, ws = self.h, self.ws
        wf = h.api("POST", f"/api/w/{ws}/workflows", json={"name": "Bank alert", "enabled": True, "definition": {
            "trigger": {"type": "webhook"}, "steps": [{"id": "n", "name": "Tell", "type": "notify", "recipients": "me",
                                                       "message": "{{trigger.source}}: {{trigger.data.amount}}"}]}})
        hook = h.api("POST", f"/api/w/{ws}/webhooks", json={"direction": "in", "name": "Bank portal", "workflow_id": wf["id"]})
        self.assertTrue(hook["secret"].startswith("whs_"))
        self.assertTrue(hook["endpoint"].endswith(f"/api/hooks/in/{hook['id']}"))
        listed = h.api("GET", f"/api/w/{ws}/webhooks")["webhooks"][0]
        self.assertNotIn("secret", listed)
        self.assertEqual(listed["secret_prefix"], hook["secret"][:8])
        self.assertEqual(listed["workflow_name"], "Bank alert")
        url = f"/api/hooks/in/{hook['id']}"
        secret = hook["secret"]
        # unsigned and wrongly signed deliveries are refused and logged
        self.assertEqual(self.anon.post(url, json={"amount": 1}).status_code, 401)
        self.assertEqual(self.anon.post(url, json={"amount": 1}, headers={"authorization": "Bearer whs_wrong"}).status_code, 401)
        body = json.dumps({"amount": 2500, "event": "payment.received"}).encode()
        bad = "sha256=" + hmac.new(b"other", body, hashlib.sha256).hexdigest()
        self.assertEqual(self.anon.post(url, content=body, headers={"x-ofintech-signature-256": bad}).status_code, 401)
        # bearer form
        r = self.anon.post(url, content=body, headers={"authorization": f"Bearer {secret}", "content-type": "application/json"})
        self.assertEqual(r.status_code, 202, r.text)
        self.assertEqual(r.json()["status"], "workflow_started")
        run = h.wait_workflow(r.json()["run_id"])
        self.assertEqual(run["status"], "done")
        self.assertEqual(run["trigger"]["source"], "Bank portal")
        self.assertEqual(h.api("GET", f"/api/w/{ws}/notifications")["items"][0]["title"], "Bank alert: Bank portal: 2500")
        # HMAC form, GitHub-style header name too
        sig = "sha256=" + hmac_sha256_hex(secret, body)
        self.assertEqual(self.anon.post(url, content=body, headers={"x-hub-signature-256": sig}).status_code, 202)
        deliveries = h.api("GET", f"/api/w/{ws}/webhooks/{hook['id']}/deliveries")
        self.assertEqual([d["status"] for d in deliveries], ["workflow_started", "workflow_started", "rejected", "rejected", "rejected"])
        self.assertEqual(deliveries[0]["event"], "payment.received")
        self.assertEqual(deliveries[0]["payload"]["amount"], 2500)
        self.assertEqual(self.anon.post(url, content=b"x" * (300 * 1024), headers={"authorization": f"Bearer {secret}"}).status_code, 413)
        # the assistant can read what arrived
        h.llm.script = [[("webhook_events", {"webhook": "bank"})], "Done."]
        t = h.api("POST", f"/api/w/{ws}/threads", json={})
        h.api("POST", f"/api/w/{ws}/threads/{t['id']}/messages", json={"text": "What did the bank webhook send?"})
        tool = next(m for m in h.wait_run(t["id"])["messages"] if m["role"] == "tool")
        self.assertIn("payment.received", tool["content"])
        self.assertIn("2500", tool["content"])
        # switched off, rotated secret, deleted
        h.api("PATCH", f"/api/w/{ws}/webhooks/{hook['id']}", json={"enabled": False})
        self.assertEqual(self.anon.post(url, content=body, headers={"authorization": f"Bearer {secret}"}).status_code, 409)
        h.api("PATCH", f"/api/w/{ws}/webhooks/{hook['id']}", json={"enabled": True, "workflow_id": None})
        new = h.api("POST", f"/api/w/{ws}/webhooks/{hook['id']}/secret")["secret"]
        self.assertEqual(self.anon.post(url, content=body, headers={"authorization": f"Bearer {secret}"}).status_code, 401)
        r = self.anon.post(url, content=body, headers={"authorization": f"Bearer {new}"})
        self.assertEqual((r.status_code, r.json()["status"], r.json()["run_id"]), (202, "received", None))
        h.api("DELETE", f"/api/w/{ws}/webhooks/{hook['id']}", expect=None)
        self.assertEqual(self.anon.post(url, content=body, headers={"authorization": f"Bearer {new}"}).status_code, 401)
        self.assertEqual(h.api("GET", f"/api/w/{ws}/webhooks")["webhooks"], [])

    def test_rate_limit_and_other_workspace(self) -> None:
        h, ws = self.h, self.ws
        hook = h.api("POST", f"/api/w/{ws}/webhooks", json={"direction": "in", "name": "ERP"})
        url, auth = f"/api/hooks/in/{hook['id']}", {"authorization": f"Bearer {hook['secret']}"}
        codes = [self.anon.post(url, json={"n": i}, headers=auth).status_code for i in range(61)]
        self.assertEqual(codes[:60], [202] * 60)
        self.assertEqual(codes[60], 429)
        other = h.api("POST", "/api/workspaces", json={"name": "Other Ltd", "base_currency": "USD", "country": "US",
                                                       "fiscal_year_start_month": 1, "accounting_framework": "IFRS",
                                                       "timezone": "UTC"})["id"]
        h.api("GET", f"/api/w/{other}/webhooks/{hook['id']}/deliveries", expect=404)
        h.api("POST", f"/api/w/{ws}/webhooks", json={"direction": "in", "name": "x", "workflow_id": "wf_missing"}, expect=404)


class Outgoing(unittest.TestCase):
    def setUp(self) -> None:
        self.h = Harness()
        self.ws = self.h.setup_workspace()
        self.received: list[httpx.Request] = []
        self.fail_times = 0

        def receiver(request: httpx.Request) -> httpx.Response:
            self.received.append(request)
            if self.fail_times > 0:
                self.fail_times -= 1
                return httpx.Response(503, text="try later")
            return httpx.Response(200, json={"ok": True})

        self.h.router.add("POST", "https://erp.example.com/hooks/desk", receiver)

    def tearDown(self) -> None:
        self.h.close()

    def deliver(self) -> int:
        return self.h.client.portal.call(self.h.engine.webhooks.deliver_pending)

    def test_events_are_signed_and_retried(self) -> None:
        h, ws = self.h, self.ws
        h.api("POST", f"/api/w/{ws}/webhooks", json={"direction": "out", "name": "ERP", "url": "https://erp.example.com/hooks/desk",
                                                     "events": []}, expect=422)
        hook = h.api("POST", f"/api/w/{ws}/webhooks", json={"direction": "out", "name": "ERP", "url": "https://erp.example.com/hooks/desk",
                                                            "events": ["workflow", "cost_centre", "test"]})
        self.assertTrue(hook["secret"].startswith("who_"))
        # something audited in a chosen group is queued; another group is not
        h.api("POST", f"/api/w/{ws}/memories", json={"content": "Close on day 3"})     # memory.*: not subscribed
        wf = h.api("POST", f"/api/w/{ws}/workflows", json={"name": "Flow", "enabled": True, "definition": {
            "trigger": {"type": "manual"}, "steps": [{"id": "n", "name": "T", "type": "notify", "message": "hi"}]}})
        rid = h.api("POST", f"/api/w/{ws}/workflows/{wf['id']}/run", json={})["run_id"]
        h.wait_workflow(rid)
        self.assertGreaterEqual(self.deliver(), 2)
        events = [r.headers["x-ofintech-event"] for r in self.received]
        self.assertIn("workflow.create", events)
        self.assertIn("workflow.run", events)
        self.assertIn("workflow.run.done", events)
        self.assertFalse(any(e.startswith("memory") for e in events))
        req = next(r for r in self.received if r.headers["x-ofintech-event"] == "workflow.run.done")
        body = json.loads(req.content)
        self.assertEqual(body["detail"]["run_id"], rid)
        self.assertEqual(body["workspace"]["name"], "Acme Ltd")
        self.assertEqual(req.headers["x-ofintech-signature-256"], "sha256=" + hmac_sha256_hex(hook["secret"], req.content))
        self.assertEqual(req.headers["x-ofintech-delivery"], body["id"])
        self.assertTrue(req.headers["user-agent"].startswith("OfinTech-Desk/"))
        log = h.api("GET", f"/api/w/{ws}/webhooks/{hook['id']}/deliveries")
        self.assertTrue(all(d["status"] == "delivered" and d["http_status"] == 200 for d in log))
        # a failing receiver gets retries with back-off, then the delivery is marked failed
        self.fail_times = 10
        h.api("POST", f"/api/w/{ws}/webhooks/{hook['id']}/test")
        self.deliver()
        d = h.api("GET", f"/api/w/{ws}/webhooks/{hook['id']}/deliveries")[0]
        self.assertEqual((d["event"], d["status"], d["attempts"], d["http_status"]), ("test.ping", "pending", 1, 503))
        self.assertGreater(d["next_attempt_at"], time.time() + 30)
        self.assertEqual(self.deliver(), 0)  # not due yet
        for _ in range(3):
            h.engine.db.execute("UPDATE webhook_deliveries SET next_attempt_at = 0 WHERE id = ?", (d["id"],))
            self.deliver()
        d = h.api("GET", f"/api/w/{ws}/webhooks/{hook['id']}/deliveries")[0]
        self.assertEqual((d["status"], d["attempts"]), ("failed", 4))
        listed = h.api("GET", f"/api/w/{ws}/webhooks")["webhooks"][0]
        self.assertEqual((listed["failed"], listed["last_status"]), (1, "failed"))
        # redelivery after the receiver recovers
        self.fail_times = 0
        h.api("POST", f"/api/w/{ws}/webhooks/{hook['id']}/deliveries/{d['id']}/redeliver", expect=None)
        self.deliver()
        self.assertEqual(h.api("GET", f"/api/w/{ws}/webhooks/{hook['id']}/deliveries")[0]["status"], "delivered")
        # no credentials leave with an event
        conn = h.api("POST", f"/api/w/{ws}/connections/custom", json={"label": "ERP", "mode": "push"})
        h.api("PATCH", f"/api/w/{ws}/webhooks/{hook['id']}", json={"events": ["*"]})
        h.api("POST", f"/api/w/{ws}/connections/{conn['id']}/ingest-key")
        self.deliver()
        sent = b"".join(r.content for r in self.received)
        self.assertNotIn(b"odk_", sent)
        self.assertNotIn(hook["secret"].encode(), sent)
        # a disabled hook queues nothing
        h.api("PATCH", f"/api/w/{ws}/webhooks/{hook['id']}", json={"enabled": False})
        before = len(self.received)
        h.api("POST", f"/api/w/{ws}/workflows/{wf['id']}/run", json={})
        self.deliver()
        self.assertEqual(len(self.received), before)

    def test_private_addresses_refused_on_a_server(self) -> None:
        srv = Harness(mode="server")
        try:
            c = srv.client
            c.post("/api/auth/signup", json={"email": "o@a.test", "name": "O", "password": "Str0ngPassw0rd!x"})
            ws = c.post("/api/workspaces", json={"name": "A", "base_currency": "USD", "country": "US",
                                                 "fiscal_year_start_month": 1, "accounting_framework": "IFRS",
                                                 "timezone": "UTC"}, headers={"origin": "https://desk.example.com"}).json()["id"]
            for url in ("http://erp.example.com/x", "https://127.0.0.1/x", "https://10.0.0.5/hook"):
                r = c.post(f"/api/w/{ws}/webhooks", json={"direction": "out", "name": "x", "url": url, "events": ["*"]},
                           headers={"origin": "https://desk.example.com"})
                self.assertEqual(r.status_code, 422, url)
        finally:
            srv.close()


if __name__ == "__main__":
    unittest.main()
