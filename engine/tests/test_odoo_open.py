"""Odoo (JSON-2 and JSON-RPC) and the Open Accounting Connector (pull adapter and push ingest)."""

from __future__ import annotations

import importlib.util
import json
import time
import unittest
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlsplit

import httpx2 as httpx

from .helpers import Harness

ACCOUNTS = {
    1: ("400000", "Product Sales", "income"), 2: ("600000", "Office Expenses", "expense"),
    3: ("500000", "Cost of Goods Sold", "expense_direct_cost"), 4: ("101401", "Bank", "asset_cash"),
    5: ("121000", "Account Receivable", "asset_receivable"), 6: ("211000", "Account Payable", "liability_payable"),
    7: ("110000", "Share Capital", "equity"), 8: ("999999", "Other Company Sales", "income"),
}
LINES = [  # date, account, balance (debit − credit), company
    ("2026-01-10", 1, -10000.0, 1), ("2026-01-10", 5, 10000.0, 1), ("2026-01-20", 3, 4000.0, 1), ("2026-01-20", 6, -4000.0, 1),
    ("2026-02-05", 1, -12000.0, 1), ("2026-02-05", 5, 12000.0, 1), ("2026-02-15", 2, 1500.0, 1), ("2026-02-15", 4, -1500.0, 1),
    ("2026-01-01", 4, 50000.0, 1), ("2026-01-01", 7, -50000.0, 1), ("2026-02-01", 8, -999.0, 2),
]
MOVES = [
    {"id": 1, "name": "INV/2026/00001", "partner_id": [9, "Deco Addict"], "invoice_date": "2026-01-10",
     "invoice_date_due": "2026-02-09", "currency_id": [1, "EUR"], "amount_total_signed": 10000.0,
     "amount_residual_signed": 10000.0, "payment_state": "not_paid", "move_type": "out_invoice", "state": "posted",
     "company_id": 1},
    {"id": 2, "name": "RINV/2026/00001", "partner_id": [9, "Deco Addict"], "invoice_date": "2026-02-01",
     "invoice_date_due": "2026-02-01", "currency_id": [1, "EUR"], "amount_total_signed": -500.0,
     "amount_residual_signed": -500.0, "payment_state": "not_paid", "move_type": "out_refund", "state": "posted",
     "company_id": 1},
    {"id": 3, "name": "BILL/2026/01/0001", "partner_id": [11, "Wood Corner"], "invoice_date": "2026-01-20",
     "invoice_date_due": "2026-02-19", "currency_id": [1, "EUR"], "amount_total_signed": -4000.0,
     "amount_residual_signed": -4000.0, "payment_state": "partial", "move_type": "in_invoice", "state": "posted",
     "company_id": 1},
]


def _match(rec: dict[str, Any], domain: list[Any]) -> bool:
    for field, op, value in domain:
        v = rec.get(field)
        if op == "=" and v != value or op == "in" and v not in value or op == ">=" and v < value \
                or op == "<=" and v > value:
            return False
    return True


def _line_rec(line: tuple[str, int, float, int]) -> dict[str, Any]:
    d, acc, bal, comp = line
    return {"date": d, "account_id": acc, "balance": bal, "company_id": comp, "parent_state": "posted"}


def _group(domain: list[Any]) -> dict[int, float]:
    out: dict[int, float] = {}
    for line in LINES:
        rec = _line_rec(line)
        if _match(rec, domain):
            out[rec["account_id"]] = out.get(rec["account_id"], 0.0) + rec["balance"]
    return out


def _acc_name(aid: int) -> str:
    return f"{ACCOUNTS[aid][0]} {ACCOUNTS[aid][1]}"


def search_read(model: str, kw: dict[str, Any], domain: list[Any]) -> list[dict[str, Any]]:
    if model == "res.company":
        return [{"id": 1, "name": "Acme SA", "currency_id": [1, "EUR"]}, {"id": 2, "name": "Acme US", "currency_id": [2, "USD"]}]
    if model == "account.account":
        return [{"id": i, "code": c, "name": n, "account_type": t} for i, (c, n, t) in ACCOUNTS.items()]
    if model == "account.move":
        rows = [m for m in MOVES if _match(m, domain)]
        return rows[kw.get("offset", 0): kw.get("offset", 0) + kw.get("limit", 80)]
    if model == "account.analytic.account":
        return [{"id": 4, "name": "Riyadh tower", "plan_id": [1, "Projects"]}]
    if model == "account.move.line":
        rows = []
        for i, (d, acc, bal, comp) in enumerate(LINES):
            rec = _line_rec((d, acc, bal, comp)) | {
                "id": i + 1, "move_id": [i // 2 + 1, f"MISC/{i // 2 + 1}"], "move_name": f"MISC/{i // 2 + 1}",
                "journal_id": [1, "Miscellaneous"], "account_id": [acc, _acc_name(acc)], "name": f"line {i + 1}",
                "debit": max(bal, 0.0), "credit": max(-bal, 0.0), "partner_id": False,
                "analytic_distribution": {"4": 100.0} if acc == 3 else False}
            if _match(rec, domain):
                rows.append(rec)
        return rows[kw.get("offset", 0): kw.get("offset", 0) + kw.get("limit", 80)]
    raise KeyError(model)


class OdooJson2:
    """Odoo 19+: /json/2/<model>/<method>."""

    def __init__(self, key: str = "odoo-key-1234567890", db: str = "acme") -> None:
        self.key, self.db = key, db
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.headers.get("authorization") != f"bearer {self.key}":
            return httpx.Response(401, json="Invalid apikey")
        if request.headers.get("x-odoo-database") != self.db:
            return httpx.Response(404, json="database not found")
        _, _, _, model, method = urlsplit(str(request.url)).path.split("/")
        body = json.loads(request.content)
        if method == "context_get":
            return httpx.Response(200, json={"lang": "en_US", "tz": "Europe/Brussels"})
        assert body["context"]["allowed_company_ids"] in ([1], None) or "allowed_company_ids" not in body["context"]
        if method == "search_read":
            return httpx.Response(200, json=search_read(model, body, body.get("domain", [])))
        if method == "formatted_read_group":
            assert body["groupby"] == ["account_id"] and body["aggregates"] == ["balance:sum"]
            return httpx.Response(200, json=[{"account_id": [a, _acc_name(a)], "balance:sum": v, "__extra_domain": []}
                                             for a, v in _group(body["domain"]).items()])
        return httpx.Response(404, json=f"{model}.{method} not found")


class OdooJsonRpc:
    """Odoo 17/18: /jsonrpc only (no JSON-2, no formatted_read_group)."""

    def __init__(self, key: str = "legacy-key-12345678", db: str = "legacy", login: str = "api@acme.test") -> None:
        self.key, self.db, self.login = key, db, login

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if "/json/2/" in str(request.url):
            return httpx.Response(404, text="<html>404</html>")
        p = json.loads(request.content)["params"]
        if p["service"] == "common":
            db, login, key, _ = p["args"]
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1,
                                             "result": 2 if (db, login, key) == (self.db, self.login, self.key) else False})
        db, uid, key, model, method, args, kw = p["args"]
        assert (db, uid, key) == (self.db, 2, self.key)
        if method == "formatted_read_group":
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "error": {"code": 200, "message": "Odoo Server Error",
                                  "data": {"message": "'account.move.line' object has no attribute 'formatted_read_group'"}}})
        if method == "read_group":
            assert kw["lazy"] is False
            res = [{"account_id": [a, _acc_name(a)], "balance": v, "__count": 1} for a, v in _group(args[0]).items()]
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": res})
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": search_read(model, kw, args[0] if args else [])})


def load_adapter() -> Any:
    path = Path(__file__).resolve().parents[2] / "examples" / "accounting-adapter" / "adapter.py"
    spec = importlib.util.spec_from_file_location("oac_adapter", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class Odoo(unittest.TestCase):
    def setUp(self) -> None:
        self.h = Harness()
        self.ws = self.h.setup_workspace()

    def tearDown(self) -> None:
        self.h.close()

    def wait_datasets(self, n: int) -> dict[str, Any]:
        for _ in range(200):
            ds = {d["name"]: d for d in self.h.api("GET", f"/api/w/{self.ws}/datasets")}
            if len(ds) >= n:
                return ds
            time.sleep(0.03)
        raise AssertionError(f"only {list(ds)}")

    def rows(self, ds_id: str) -> list[list[Any]]:
        return self.h.api("GET", f"/api/w/{self.ws}/datasets/{ds_id}")["rows"]

    def test_json2_sync(self) -> None:
        h, ws = self.h, self.ws
        mock = OdooJson2()
        h.router.add("POST", "https://acme.odoo.com/", mock)
        conn = h.api("POST", f"/api/w/{ws}/connections/odoo", json={"base_url": "https://acme.odoo.com/",
                                                                   "api_key": mock.key, "database": "acme",
                                                                   "company_id": 1})
        self.assertEqual(conn["label"], "Odoo: Acme SA")
        self.assertEqual(conn["config"]["api"], "json2")
        self.assertNotIn("api_key", json.dumps(conn))
        ds = self.wait_datasets(5)
        gl = self.rows(ds["Odoo: Acme SA · General ledger"]["id"])
        self.assertEqual(len(gl), 10)  # company 2's line is left out
        cogs = next(r for r in gl if r[3] == "500000")
        self.assertEqual((cogs[5], cogs[6], cogs[10]), ("Cost of sales", 4000.0, "Projects: Riyadh tower"))
        self.assertEqual(next(r for r in gl if r[3] == "101401")[5], "Bank")
        pl = self.rows(ds["Odoo: Acme SA · Profit and loss"]["id"])
        jan = {(r[1], r[2]): r[3] for r in pl if str(r[0]).startswith("2026-01")}
        self.assertEqual(jan[("Income", "400000 Product Sales")], 10000.0)      # credit balance shown as income
        self.assertEqual(jan[("Cost of sales", "500000 Cost of Goods Sold")], 4000.0)
        self.assertEqual(jan[("Net profit", "Net profit")], 6000.0)
        self.assertFalse(any("Other Company" in str(r[2]) for r in pl))         # company filter applied
        bs = {r[3]: r[4] for r in self.rows(ds["Odoo: Acme SA · Balance sheet"]["id"])}
        self.assertEqual(bs["121000 Account Receivable"], 22000.0)
        self.assertEqual(bs["211000 Account Payable"], 4000.0)
        self.assertEqual(bs["110000 Share Capital"], 50000.0)
        self.assertEqual(bs["Unallocated earnings (P&L to date)"], 16500.0)     # 22,000 − 4,000 − 1,500
        assets = bs["101401 Bank"] + bs["121000 Account Receivable"]
        self.assertAlmostEqual(assets, bs["211000 Account Payable"] + bs["110000 Share Capital"]
                               + bs["Unallocated earnings (P&L to date)"])        # it balances
        inv = self.rows(ds["Odoo: Acme SA · Invoices"]["id"])
        self.assertEqual({r[0]: r[6] for r in inv}, {"INV/2026/00001": 10000.0, "RINV/2026/00001": -500.0})
        self.assertTrue(any(r[7].startswith("Credit note") for r in inv))
        bills = self.rows(ds["Odoo: Acme SA · Bills"]["id"])
        self.assertEqual(bills[0][7], "Partially paid")
        self.assertTrue(all(r.headers["x-odoo-database"] == "acme" for r in mock.requests))

    def test_json_rpc_fallback_for_odoo_18(self) -> None:
        h, ws = self.h, self.ws
        h.router.add("POST", "https://legacy.example.com/", OdooJsonRpc())
        h.api("POST", f"/api/w/{ws}/connections/odoo", json={"base_url": "https://legacy.example.com",
                                                            "api_key": "wrong-key-123456789", "database": "legacy",
                                                            "username": "api@acme.test"}, expect=502)
        conn = h.api("POST", f"/api/w/{ws}/connections/odoo", json={"base_url": "https://legacy.example.com",
                                                                   "api_key": "legacy-key-12345678",
                                                                   "database": "legacy", "username": "api@acme.test",
                                                                   "company_id": 1})
        self.assertEqual(conn["config"]["api"], "jsonrpc")
        ds = self.wait_datasets(5)
        gl = self.rows(ds["Odoo: Acme SA · General ledger"]["id"])
        self.assertEqual(len(gl), 10)  # company 2's line is left out
        cogs = next(r for r in gl if r[3] == "500000")
        self.assertEqual((cogs[5], cogs[6], cogs[10]), ("Cost of sales", 4000.0, "Projects: Riyadh tower"))
        self.assertEqual(next(r for r in gl if r[3] == "101401")[5], "Bank")
        pl = self.rows(ds["Odoo: Acme SA · Profit and loss"]["id"])
        feb = {r[2]: r[3] for r in pl if str(r[0]).startswith("2026-02")}
        self.assertEqual(feb["400000 Product Sales"], 12000.0)
        self.assertEqual(feb["600000 Office Expenses"], 1500.0)

    def test_needs_login_for_old_servers(self) -> None:
        h, ws = self.h, self.ws
        h.router.add("POST", "https://legacy.example.com/", OdooJsonRpc())
        r = h.api("POST", f"/api/w/{ws}/connections/odoo", json={"base_url": "https://legacy.example.com",
                                                                "api_key": "legacy-key-12345678"}, expect=422)
        self.assertIn("Odoo 17 or 18", r["message"])


class OpenConnector(unittest.TestCase):
    def setUp(self) -> None:
        self.h = Harness()
        self.ws = self.h.setup_workspace()

    def tearDown(self) -> None:
        self.h.close()

    def test_pull_adapter(self) -> None:
        h, ws = self.h, self.ws
        adapter = load_adapter()

        def handle(request: httpx.Request) -> httpx.Response:
            parts = urlsplit(str(request.url))
            status, body = adapter.handle(parts.path, dict(parse_qsl(parts.query)),
                                          request.headers.get("authorization", ""), "adapter-secret-key")
            return httpx.Response(status, json=body)

        h.router.add("GET", "https://erp.example.com/", handle)
        h.api("POST", f"/api/w/{ws}/connections/custom", json={"label": "Our ERP", "mode": "pull",
                                                              "base_url": "https://erp.example.com",
                                                              "api_key": "wrong"}, expect=502)
        conn = h.api("POST", f"/api/w/{ws}/connections/custom", json={"label": "Our ERP", "mode": "pull",
                                                                     "base_url": "https://erp.example.com",
                                                                     "api_key": "adapter-secret-key"})
        self.assertEqual(conn["status"], "connected")
        for _ in range(200):
            ds = {d["name"]: d for d in h.api("GET", f"/api/w/{ws}/datasets")}
            if len(ds) >= 4:
                break
            time.sleep(0.03)
        pl = h.api("GET", f"/api/w/{ws}/datasets/{ds['Our ERP · Profit and loss']['id']}")["rows"]
        self.assertIn(["2026-03-01", "Net profit", "Net profit", 27300.0], pl)
        inv = h.api("GET", f"/api/w/{ws}/datasets/{ds['Our ERP · Invoices']['id']}")["rows"]
        self.assertEqual(len(inv), 3)  # synced with status=all
        # the assistant reads reports through the same connection
        h.llm.script = [[("accounting_report", {"report": "profit_and_loss", "start_date": "2026-01-01",
                                                "end_date": "2026-03-31"})], "Sales were 156,000.00."]
        t = h.api("POST", f"/api/w/{ws}/threads", json={"title": "P&L"})
        h.api("POST", f"/api/w/{ws}/threads/{t['id']}/messages", json={"text": "Q1 P&L?"})
        data = h.wait_run(t["id"])
        tool = next(m for m in data["messages"] if m["role"] == "tool")
        self.assertIn("4000 Sales", tool["content"])
        self.assertIn("156,000.00", tool["content"])

    def test_push_ingest(self) -> None:
        h, ws = self.h, self.ws
        conn = h.api("POST", f"/api/w/{ws}/connections/custom", json={"label": "Tally export", "mode": "push"})
        key = conn["ingest_key"]
        self.assertTrue(key.startswith("odk_"))
        listed = h.api("GET", f"/api/w/{ws}/connections")["connections"][0]
        self.assertEqual(listed["ingest_key_prefix"], key[:10])
        self.assertNotIn(key, json.dumps(listed))
        url = "/api/ingest/v1/accounting/profit-and-loss"
        body = {"lines": [{"period": "2026-03-01", "section": "Income", "account": "Sales", "amount": 1000},
                          {"period": "2026-03-01", "section": "Expenses", "account": "Rent", "amount": 300}]}
        anon = h.client.post(url, json=body, headers={"authorization": "Bearer odk_wrong"})
        self.assertEqual(anon.status_code, 401)
        auth = {"authorization": f"Bearer {key}"}
        r = h.client.post(url, json=body, headers=auth)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["rows"], 3)  # + net profit line
        bad = h.client.post(url, json={"lines": [{"period": "March", "section": "Sales", "account": "x", "amount": "y"}]},
                            headers=auth)
        self.assertEqual(bad.status_code, 422)
        self.assertTrue(bad.json()["details"])
        r = h.client.post("/api/ingest/v1/accounting/invoices", headers=auth, json={"documents": [
            {"number": "A-1", "party": "Client", "due_date": "2026-01-31", "currency": "INR", "total": 250000,
             "balance": 250000}]})
        self.assertEqual(r.status_code, 200)
        r = h.client.post("/api/ingest/v1/accounting/journal", headers=auth, json={})
        self.assertEqual(r.status_code, 422)
        # sync is refused, reports are answered from the pushed data
        h.api("POST", f"/api/w/{ws}/connections/{conn['id']}/sync", expect=422)
        h.llm.script = [[("list_invoices", {"kind": "receivable"})], "One invoice is open."]
        t = h.api("POST", f"/api/w/{ws}/threads", json={"title": "AR"})
        h.api("POST", f"/api/w/{ws}/threads/{t['id']}/messages", json={"text": "Open invoices?"})
        tool = next(m for m in h.wait_run(t["id"])["messages"] if m["role"] == "tool")
        self.assertIn("A-1", tool["content"])
        self.assertIn("250,000.00", tool["content"])
        # rotating the key revokes the old one
        new = h.api("POST", f"/api/w/{ws}/connections/{conn['id']}/ingest-key", json={})["ingest_key"]
        self.assertEqual(h.client.post(url, json=body, headers=auth).status_code, 401)
        self.assertEqual(h.client.post(url, json=body, headers={"authorization": f"Bearer {new}"}).status_code, 200)

    def test_openapi_spec(self) -> None:
        spec = self.h.client.get("/api/connector-spec/openapi.json").json()
        self.assertEqual(spec["openapi"], "3.1.0")
        for p in ("/v1/company", "/v1/profit-and-loss", "/v1/balance-sheet", "/v1/invoices", "/v1/bills",
                  "/api/ingest/v1/accounting/profit-and-loss"):
            self.assertIn(p, spec["paths"])
        self.assertIn("PLLine", spec["components"]["schemas"])
        refs = json.dumps(spec)
        for name in set(x.split('"')[0] for x in refs.split("#/components/schemas/")[1:]):
            self.assertIn(name, spec["components"]["schemas"])
        repo = Path(__file__).resolve().parents[2] / "docs" / "openapi" / "accounting-connector.json"
        if repo.exists():  # the published copy is up to date
            published = json.loads(repo.read_text())
            self.assertEqual(published["components"], spec["components"])
            self.assertEqual(sorted(published["paths"]), sorted(spec["paths"]))


if __name__ == "__main__":
    unittest.main()
