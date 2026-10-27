import json
import time
import unittest
from datetime import date
from urllib.parse import parse_qs, urlparse

import httpx2 as httpx

from desk_engine.connectors import quickbooks, xero, zoho_books
from desk_engine.connectors.accounting import add_net_profit, aging_bucket, with_aging
from desk_engine.connectors.google import build_raw_email, html_to_text, parse_message
from tests import fixtures as fx
from tests.helpers import Harness


class Parsers(unittest.TestCase):
    def test_quickbooks_pl(self) -> None:
        lines = quickbooks.parse_profit_and_loss(fx.QBO_PL)
        self.assertIn({"period": "2026-01-01", "section": "Income", "account": "Sales", "amount": 1000.0}, lines)
        self.assertIn({"period": "2026-01-01", "section": "Income", "account": "Consulting", "amount": 200.0}, lines)
        self.assertIn({"period": "2026-02-01", "section": "Cost of sales", "account": "Materials", "amount": 600.0}, lines)
        net = [ln for ln in lines if ln["section"] == "Net profit"]
        self.assertEqual([n["amount"] for n in net], [500.0, 600.0])
        self.assertFalse(any(ln["account"].startswith("Total") for ln in lines))
        income_jan = sum(ln["amount"] for ln in lines if ln["section"] == "Income" and ln["period"] == "2026-01-01")
        self.assertEqual(income_jan, 1200.0)  # equals the report's Total Income (no double counting)

    def test_quickbooks_balance_sheet(self) -> None:
        lines = quickbooks.parse_balance_sheet(fx.QBO_BS, date(2026, 2, 28))
        by = {ln["account"]: (ln["section"], ln["group"], ln["amount"]) for ln in lines}
        self.assertEqual(by["Checking"], ("Assets", "Current Assets", 5000.0))
        self.assertEqual(by["Accounts Payable (A/P)"], ("Liabilities", "Current Liabilities", 700.0))
        self.assertEqual(by["Retained Earnings"], ("Equity", "Equity", 5500.0))

    def test_xero(self) -> None:
        lines = xero.parse_profit_and_loss(fx.XERO_PL["Reports"][0])
        self.assertIn({"period": "2026-02-01", "section": "Income", "account": "Sales", "amount": 1500.0}, lines)
        self.assertIn({"period": "2026-01-01", "section": "Cost of sales", "account": "Purchases", "amount": 400.0}, lines)
        self.assertIn({"period": "2026-01-01", "section": "Expenses", "account": "Rent", "amount": 300.0}, lines)
        self.assertIn({"period": "2026-02-01", "section": "Net profit", "account": "Net profit", "amount": 600.0}, lines)
        self.assertFalse(any(ln["account"] in ("Gross Profit", "Total Income") for ln in lines))
        bs = xero.parse_balance_sheet(fx.XERO_BS["Reports"][0], date(2026, 2, 28))
        self.assertEqual({(b["section"], b["account"]) for b in bs},
                         {("Assets", "Business Account"), ("Liabilities", "GST"), ("Equity", "Retained Earnings")})

    def test_zoho_flatten(self) -> None:
        body = {k: v for k, v in fx.ZOHO_PL.items() if k not in {"code", "message"}}
        flat = zoho_books.flatten_report(body, section_fn=zoho_books._section_from_name)
        self.assertIn(("Income", "Operating Income", "Sales", 1400.0), flat)
        self.assertIn(("Cost of sales", "Cost of Goods Sold", "Cost of Goods Sold", 500.0), flat)
        self.assertIn(("Expenses", "Operating Expense", "Rent Expense", 300.0), flat)
        self.assertIn(("Other income", "Non Operating Income", "Interest Income", 20.0), flat)
        self.assertEqual(len(flat), 5)

    def test_net_profit_and_aging(self) -> None:
        lines = [{"period": "2026-01-01", "section": "Income", "account": "S", "amount": 100.0},
                 {"period": "2026-01-01", "section": "Expenses", "account": "R", "amount": 30.0},
                 {"period": "2026-01-01", "section": "Other income", "account": "I", "amount": 5.0}]
        self.assertEqual(add_net_profit(lines)[-1]["amount"], 75.0)
        self.assertEqual(aging_bucket(45), "2: 31–60 days")
        docs = with_aging([{"due_date": "2026-01-01", "balance": 10.0}, {"due_date": "2026-01-01", "balance": 0.0}],
                          today=date(2026, 2, 15))
        self.assertEqual((docs[0]["days_overdue"], docs[1]["days_overdue"]), (45, 0))

    def test_gmail_parsing(self) -> None:
        import base64

        def b64(s: str) -> str:
            return base64.urlsafe_b64encode(s.encode()).decode().rstrip("=")

        msg = {"id": "m1", "threadId": "t1", "snippet": "Invoice &amp; statement", "payload": {
            "headers": [{"name": "From", "value": "AP <ap@vendor.test>"}, {"name": "Subject", "value": "Invoice 77"}],
            "mimeType": "multipart/mixed", "parts": [
                {"mimeType": "text/html", "body": {"data": b64("<p>Total <b>1,200.00</b></p><script>alert(1)</script>")}},
                {"mimeType": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "filename": "inv.xlsx",
                 "body": {"attachmentId": "att1", "size": 100}}]}}
        parsed = parse_message(msg)
        self.assertEqual(parsed["body"], "Total 1,200.00")
        self.assertEqual(parsed["attachments"][0]["filename"], "inv.xlsx")
        self.assertEqual(parsed["snippet"], "Invoice & statement")
        self.assertNotIn("alert", html_to_text("<script>alert(1)</script>ok"))
        raw = build_raw_email(sender=None, to="a@b.test", subject="Hi\nBcc: x@y", body="Body")
        decoded = base64.urlsafe_b64decode(raw + "==").decode()
        self.assertIn("Subject: Hi Bcc: x@y", decoded)  # header injection neutralised
        with self.assertRaises(Exception):
            build_raw_email(sender=None, to="not-an-address", subject="s", body="b")


class OAuthFlows(unittest.TestCase):
    def setUp(self) -> None:
        self.h = Harness()
        self.ws = self.h.setup_workspace(with_model=False)

    def tearDown(self) -> None:
        self.h.close()

    def _set_app(self, provider: str, **extra: object) -> None:
        self.h.api("PUT", f"/api/oauth-apps/{provider}", json={"client_id": "cid-123", "client_secret": "csecret",
                                                                **extra})

    def _authorize(self, provider: str) -> dict:
        url = self.h.api("POST", f"/api/w/{self.ws}/connections/{provider}/authorize", json={})["url"]
        return {k: v[0] for k, v in parse_qs(urlparse(url).query).items()} | {"_url": url}

    def _wait_datasets(self, n: int) -> list:
        for _ in range(200):
            items = self.h.api("GET", f"/api/w/{self.ws}/datasets")
            if len(items) >= n:
                return items
            time.sleep(0.05)
        raise AssertionError("sync did not finish")

    def test_requires_oauth_app(self) -> None:
        r = self.h.api("POST", f"/api/w/{self.ws}/connections/quickbooks/authorize", json={}, expect=409)
        self.assertEqual(r["error"], "oauth_app_missing")
        self.assertIn("http://localhost:47821/oauth/callback", r["message"])

    def test_quickbooks_end_to_end(self) -> None:
        h = self.h
        self._set_app("quickbooks", environment="sandbox")
        apps = h.api("GET", "/api/oauth-apps")
        self.assertNotIn("csecret", json.dumps(apps))
        q = self._authorize("quickbooks")
        self.assertTrue(q["_url"].startswith("https://appcenter.intuit.com/connect/oauth2?"))
        self.assertEqual(q["scope"], "com.intuit.quickbooks.accounting")
        self.assertEqual(q["redirect_uri"], "http://localhost:47821/oauth/callback")
        r = h.router
        r.add("POST", "https://oauth.platform.intuit.com/oauth2/v1/tokens/bearer",
              lambda req: httpx.Response(200, json={"access_token": "at1", "refresh_token": "rt1", "expires_in": 3600}))
        base = "https://sandbox-quickbooks.api.intuit.com/v3/company/9130"
        r.add("GET", f"{base}/companyinfo/", {"CompanyInfo": {"CompanyName": "Sandbox Co", "Country": "US"}})
        r.add("GET", f"{base}/preferences", {"Preferences": {"CurrencyPrefs": {"HomeCurrency": {"value": "USD"}}}})
        r.add("GET", f"{base}/reports/ProfitAndLoss", fx.QBO_PL)
        r.add("GET", f"{base}/reports/BalanceSheet", fx.QBO_BS)
        r.add("GET", f"{base}/query", lambda req: httpx.Response(200, json=fx.qbo_invoices() if "Invoice" in str(req.url)
                                                                 else {"QueryResponse": {}}))
        # a forged / reused state is refused
        bad = h.client.get("/oauth/callback", params={"code": "c", "state": "forged", "realmId": "9130"})
        self.assertEqual(bad.status_code, 400)
        ok = h.client.get("/oauth/callback", params={"code": "c0de", "state": q["state"], "realmId": "9130"})
        self.assertEqual(ok.status_code, 200, ok.text)
        self.assertIn("QuickBooks: Sandbox Co", ok.text)
        token_req = r.find("https://oauth.platform.intuit.com")[0]
        self.assertTrue(token_req.headers["authorization"].startswith("Basic "))
        self.assertIn(b"code=c0de", token_req.content)
        again = h.client.get("/oauth/callback", params={"code": "c0de", "state": q["state"], "realmId": "9130"})
        self.assertEqual(again.status_code, 400)
        names = sorted(d["name"] for d in self._wait_datasets(4))
        self.assertEqual(names, ["QuickBooks: Sandbox Co · Balance sheet", "QuickBooks: Sandbox Co · Bills",
                                 "QuickBooks: Sandbox Co · Invoices", "QuickBooks: Sandbox Co · Profit and loss"])
        self.assertTrue(all("minorversion=75" in str(x.url) for x in r.find(base)))
        conns = h.api("GET", f"/api/w/{self.ws}/connections")["connections"]
        self.assertEqual(conns[0]["status"], "connected")
        self.assertNotIn("at1", json.dumps(conns))
        pl = next(d for d in h.api("GET", f"/api/w/{self.ws}/datasets") if d["name"].endswith("Profit and loss"))
        dash = h.api("POST", f"/api/w/{self.ws}/dashboards/from-template", json={"template": "profit_and_loss",
                                                                                   "dataset_id": pl["id"]})
        view = h.api("GET", f"/api/w/{self.ws}/dashboards/{dash['id']}")
        self.assertFalse([w for w in view["widgets"] if "error" in w], view["widgets"])
        inv = next(d for d in h.api("GET", f"/api/w/{self.ws}/datasets") if d["name"].endswith("Invoices"))
        dash = h.api("POST", f"/api/w/{self.ws}/dashboards/from-template", json={"template": "receivables",
                                                                                   "dataset_id": inv["id"]})
        view = h.api("GET", f"/api/w/{self.ws}/dashboards/{dash['id']}")
        self.assertEqual(view["widgets"][0]["data"]["rows"], [[1200.0]])

    def test_token_refresh_rotates(self) -> None:
        h = self.h
        self._set_app("xero")
        q = self._authorize("xero")
        self.assertIn("accounting.reports.profitandloss.read", q["scope"])
        self.assertEqual(q["code_challenge_method"], "S256")
        calls = {"n": 0}

        def token(req: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            body = parse_qs(req.content.decode())
            if body["grant_type"] == ["authorization_code"]:
                self.assertIn("code_verifier", body)
                return httpx.Response(200, json={"access_token": "a1", "refresh_token": "r1", "expires_in": 1})
            self.assertEqual(body["refresh_token"], ["r1"])
            return httpx.Response(200, json={"access_token": "a2", "refresh_token": "r2", "expires_in": 1800})

        h.router.add("POST", "https://identity.xero.com/connect/token", token)
        h.router.add("GET", "https://api.xero.com/connections",
                     lambda req: httpx.Response(200, json=[{"tenantId": "T1", "tenantName": "Demo NZ",
                                                            "tenantType": "ORGANISATION"}]))
        h.router.add("GET", "https://api.xero.com/api.xro/2.0/Reports/ProfitAndLoss", fx.XERO_PL)
        h.router.add("GET", "https://api.xero.com/api.xro/2.0/Reports/BalanceSheet", fx.XERO_BS)
        h.router.add("GET", "https://api.xero.com/api.xro/2.0/Invoices", {"Invoices": []})
        ok = h.client.get("/oauth/callback", params={"code": "x", "state": q["state"]})
        self.assertEqual(ok.status_code, 200, ok.text)
        self._wait_datasets(4)
        self.assertGreaterEqual(calls["n"], 2)  # expired immediately -> refreshed during sync
        xero_calls = h.router.find("https://api.xero.com/api.xro/2.0/")
        self.assertEqual(xero_calls[-1].headers["xero-tenant-id"], "T1")
        self.assertEqual(xero_calls[-1].headers["authorization"], "Bearer a2")
        conn = h.engine.db.one("SELECT * FROM connections")
        stored = h.engine.secrets.get(conn["secret_id"], workspace_id=self.ws)["tokens"]
        self.assertEqual(stored["refresh_token"], "r2")


if __name__ == "__main__":
    unittest.main()
