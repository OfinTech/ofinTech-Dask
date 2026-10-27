import asyncio
import base64
import json
import time
import unittest
from urllib.parse import parse_qs, urlparse

import httpx2 as httpx

from desk_engine.llm.anthropic import AnthropicModel, to_anthropic
from desk_engine.llm.base import ChatMessage, ToolCall, ToolSpec, complete
from desk_engine.llm.openai_compat import OpenAICompatible
from tests import fixtures as fx
from tests.helpers import Harness


def sse_lines(events: list[dict]) -> bytes:
    return "".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in events).encode()


class Providers(unittest.TestCase):
    def test_anthropic_stream_with_tool_use(self) -> None:
        seen = {}

        def handler(req: httpx.Request) -> httpx.Response:
            seen["body"] = json.loads(req.content)
            seen["headers"] = req.headers
            events = [
                {"type": "message_start", "message": {"usage": {"input_tokens": 21, "cache_read_input_tokens": 250,
                                                                 "cache_creation_input_tokens": 50}}},
                {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
                {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "Checking "}},
                {"type": "content_block_start", "index": 1, "content_block": {"type": "tool_use", "id": "toolu_1",
                                                                             "name": "calc_npv_irr", "input": {}}},
                {"type": "content_block_delta", "index": 1, "delta": {"type": "input_json_delta", "partial_json": "{\"cashflows\": [-1"}},
                {"type": "content_block_delta", "index": 1, "delta": {"type": "input_json_delta", "partial_json": "0, 6, 6]}"}},
                {"type": "message_delta", "delta": {"stop_reason": "tool_use"}, "usage": {"output_tokens": 44}},
                {"type": "message_stop"},
            ]
            return httpx.Response(200, content=sse_lines(events), headers={"content-type": "text/event-stream"})

        model = AnthropicModel(label="Anthropic", base_url="https://api.anthropic.test/v1", api_key="k", model="m",
                               transport=httpx.MockTransport(handler))
        msgs = [ChatMessage("system", "rules"), ChatMessage("system", "today"), ChatMessage("user", "hi"),
                ChatMessage("assistant", "", [ToolCall("t0", "list_datasets", {})]),
                ChatMessage("tool", "none", tool_call_id="t0"), ChatMessage("user", "npv?")]
        result = asyncio.run(complete(model, msgs, [ToolSpec("calc_npv_irr", "d", {"type": "object"})]))
        self.assertEqual(result.text, "Checking ")
        self.assertEqual(result.tool_calls[0].arguments, {"cashflows": [-10, 6, 6]})
        self.assertEqual((result.usage.input_tokens, result.usage.output_tokens), (321, 44))
        self.assertEqual((result.usage.cache_read, result.usage.cache_write), (250, 50))
        self.assertEqual(result.finish_reason, "tool_calls")
        # prompt caching: the stable system block, the tools and the latest message are cache breakpoints
        cache = {"type": "ephemeral"}
        self.assertEqual(seen["body"]["system"], [{"type": "text", "text": "rules", "cache_control": cache},
                                                  {"type": "text", "text": "today"}])
        self.assertEqual(seen["body"]["tools"][-1]["cache_control"], cache)
        self.assertEqual(seen["body"]["messages"][-1]["content"][-1]["cache_control"], cache)
        self.assertEqual(seen["headers"]["anthropic-version"], "2023-06-01")
        self.assertEqual([m["role"] for m in seen["body"]["messages"]], ["user", "assistant", "user"])
        self.assertEqual(seen["body"]["messages"][2]["content"][0]["type"], "tool_result")

    def test_to_anthropic_merges_consecutive_roles(self) -> None:
        system, msgs = to_anthropic([ChatMessage("user", "a"), ChatMessage("user", "b")])
        self.assertEqual(len(msgs), 1)
        self.assertEqual(len(msgs[0]["content"]), 2)

    def test_openai_errors_and_stream_options_fallback(self) -> None:
        calls = []

        def handler(req: httpx.Request) -> httpx.Response:
            body = json.loads(req.content)
            calls.append(body)
            if "stream_options" in body:
                return httpx.Response(400, json={"error": {"message": "Unrecognized request argument: stream_options"}})
            return httpx.Response(200, content=b'data: {"choices":[{"delta":{"content":"OK"}}]}\n\ndata: [DONE]\n\n')

        model = OpenAICompatible(label="X", base_url="https://x.test/v1", api_key="k", model="m",
                                 transport=httpx.MockTransport(handler))
        r = asyncio.run(complete(model, [ChatMessage("user", "hi")]))
        self.assertEqual(r.text, "OK")
        self.assertEqual(len(calls), 2)
        bad = OpenAICompatible(label="X", base_url="https://x.test/v1", api_key="k", model="m",
                               transport=httpx.MockTransport(lambda req: httpx.Response(401, json={"error": "nope"})))
        with self.assertRaises(Exception) as ctx:
            asyncio.run(complete(bad, [ChatMessage("user", "hi")]))
        self.assertEqual(getattr(ctx.exception, "code", ""), "llm_auth")
        azure = []
        az = OpenAICompatible(label="Azure", base_url="https://r.openai.azure.com/openai/v1", api_key="secret", model="m",
                              auth_header="api-key", transport=httpx.MockTransport(
                                  lambda req: azure.append(req) or httpx.Response(200, content=b"data: [DONE]\n\n")))
        asyncio.run(complete(az, [ChatMessage("user", "hi")]))
        self.assertEqual(azure[0].headers["api-key"], "secret")
        self.assertNotIn("authorization", azure[0].headers)


class GoogleThroughAgent(unittest.TestCase):
    def setUp(self) -> None:
        self.h = Harness(script=[
            [("gmail_search", {"query": "invoice newer_than:7d"})],
            [("gmail_create_draft", {"to": "ap@vendor.test", "subject": "Re: Invoice 77",
                                     "body": "Thanks, scheduled for payment on Friday.", "reply_to_message_id": "m1"})],
            "I drafted a reply in Gmail.",
        ])
        self.ws = self.h.setup_workspace()
        h = self.h
        h.api("PUT", "/api/oauth-apps/google", json={"client_id": "g-id", "client_secret": "g-secret"})
        url = h.api("POST", f"/api/w/{self.ws}/connections/google/authorize", json={})["url"]
        q = {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}
        self.assertIn("https://www.googleapis.com/auth/gmail.compose", q["scope"])
        self.assertEqual(q["access_type"], "offline")
        h.router.add("POST", "https://oauth2.googleapis.com/token",
                     {"access_token": "ga", "refresh_token": "gr", "expires_in": 3600, "scope": q["scope"]})
        h.router.add("GET", "https://openidconnect.googleapis.com/v1/userinfo", {"email": "finance@acme.test"})
        g = "https://gmail.googleapis.com/gmail/v1/users/me"
        h.router.add("GET", f"{g}/messages?", {"messages": [{"id": "m1"}]})
        h.router.add("GET", f"{g}/messages/m1", {"id": "m1", "threadId": "t1", "snippet": "Invoice 77 attached",
                                                 "payload": {"headers": [{"name": "From", "value": "ap@vendor.test"},
                                                                         {"name": "Subject", "value": "Invoice 77"},
                                                                         {"name": "Message-ID", "value": "<abc@vendor>"}]}})
        self.drafts: list[dict] = []
        h.router.add("POST", f"{g}/drafts", lambda req: self.drafts.append(json.loads(req.content)) or
                     httpx.Response(200, json={"id": "d1"}))
        ok = h.client.get("/oauth/callback", params={"code": "c", "state": q["state"]})
        self.assertEqual(ok.status_code, 200, ok.text)

    def tearDown(self) -> None:
        self.h.close()

    def test_search_then_draft_with_approval(self) -> None:
        h = self.h
        conns = h.api("GET", f"/api/w/{self.ws}/connections")["connections"]
        self.assertEqual(conns[0]["label"], "Google: finance@acme.test")
        t = h.api("POST", f"/api/w/{self.ws}/threads", json={})
        h.api("POST", f"/api/w/{self.ws}/threads/{t['id']}/messages", json={"text": "Reply to the latest invoice e-mail"})
        data = h.wait_run(t["id"])
        self.assertEqual(data["run"]["status"], "waiting_approval")
        search = [m for m in data["messages"] if m["role"] == "tool"][0]
        self.assertIn("Invoice 77", search["content"])
        self.assertEqual(self.drafts, [])  # nothing sent before approval
        appr = h.api("GET", f"/api/w/{self.ws}/approvals")[0]
        self.assertIn("ap@vendor.test", appr["summary"])
        h.api("POST", f"/api/w/{self.ws}/approvals/{appr['id']}/decide", json={"approve": True})
        data = h.wait_run(t["id"])
        self.assertEqual(data["run"]["status"], "done")
        self.assertEqual(self.drafts[0]["message"]["threadId"], "t1")
        raw = base64.urlsafe_b64decode(self.drafts[0]["message"]["raw"] + "==").decode()
        self.assertIn("In-Reply-To: <abc@vendor>", raw)
        self.assertIn("To: ap@vendor.test", raw)


class ZohoFlow(unittest.TestCase):
    def test_zoho_callback_uses_account_data_centre(self) -> None:
        h = Harness()
        try:
            ws = h.setup_workspace(with_model=False)
            h.api("PUT", "/api/oauth-apps/zoho_books", json={"client_id": "zoho-cid", "client_secret": "zs"})
            url = h.api("POST", f"/api/w/{ws}/connections/zoho_books/authorize", json={})["url"]
            q = {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}
            self.assertEqual(q["scope"].split(",")[0], "ZohoBooks.reports.READ")
            h.router.add("POST", "https://accounts.zoho.eu/oauth/v2/token",
                         {"access_token": "za", "refresh_token": "zr", "expires_in": 3600,
                          "api_domain": "https://www.zohoapis.eu"})
            h.router.add("GET", "https://www.zohoapis.eu/books/v3/organizations",
                         {"organizations": [{"organization_id": "777", "name": "Acme GmbH", "is_default_org": True,
                                             "currency_code": "EUR"}]})
            h.router.add("GET", "https://www.zohoapis.eu/books/v3/reports/profitandloss", fx.ZOHO_PL)
            h.router.add("GET", "https://www.zohoapis.eu/books/v3/reports/balancesheet", {"balance_sheet": []})
            h.router.add("GET", "https://www.zohoapis.eu/books/v3/invoices", {"invoices": [
                {"invoice_number": "INV-1", "customer_name": "Kunde AG", "date": "2026-09-01", "due_date": "2026-09-30",
                 "currency_code": "EUR", "total": 500, "balance": 500, "status": "overdue"}], "page_context": {}})
            h.router.add("GET", "https://www.zohoapis.eu/books/v3/bills", {"bills": [], "page_context": {}})
            r = h.client.get("/oauth/callback", params={"code": "c", "state": q["state"], "location": "eu",
                                                        "accounts-server": "https://accounts.zoho.eu"})
            self.assertEqual(r.status_code, 200, r.text)
            self.assertIn("Zoho Books: Acme GmbH", r.text)
            # a forged accounts-server is refused
            url2 = h.api("POST", f"/api/w/{ws}/connections/zoho_books/authorize", json={})["url"]
            s2 = parse_qs(urlparse(url2).query)["state"][0]
            bad = h.client.get("/oauth/callback", params={"code": "c", "state": s2, "accounts-server": "https://evil.test"})
            self.assertEqual(bad.status_code, 400)
            for _ in range(200):
                if len(h.api("GET", f"/api/w/{ws}/datasets")) >= 3:
                    break
                time.sleep(0.05)
            zoho_calls = h.router.find("https://www.zohoapis.eu/books/v3/")
            self.assertTrue(all("organization_id=777" in str(c.url) for c in zoho_calls if "organizations" not in str(c.url)))
            self.assertTrue(all(c.headers["authorization"] == "Zoho-oauthtoken za" for c in zoho_calls))
            names = {d["name"] for d in h.api("GET", f"/api/w/{ws}/datasets")}
            self.assertIn("Zoho Books: Acme GmbH · Invoices", names)
        finally:
            h.close()


if __name__ == "__main__":
    unittest.main()
