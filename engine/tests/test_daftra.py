"""Daftra as an accounting connection: what is read, how it is mapped, and where the token may go.

What this guards.

- **The token goes to the customer's Daftra address and nowhere else.** An account address is accepted only as
  ``<name>.daftra.com`` or ``<name>.daftara.com``; anything else is refused before a request is made.
- **The reports read as Daftra shows them.** Profit and loss by month with expenses as positive amounts, cost of
  sales apart; the balance sheet by section, with credit groups turned the right way.
- **Journal lines carry their accounts and classes,** across pages, and drafts are not read as posted.
- **Nothing is invented.** A document whose amounts Daftra does not send has an empty balance and status
  "unknown", and is not counted as open.

Every company, account and figure below is invented. The shapes follow Daftra's published API reference.
"""

from __future__ import annotations

import asyncio
import json
import unittest
from datetime import date
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx2 as httpx

from desk_engine.connectors import daftra
from desk_engine.connectors.accounting import client_for, sync_connection
from desk_engine.db import dumps, now
from desk_engine.errors import Invalid

from .helpers import Harness

SITE = "harbor-test.daftra.com"
API = f"https://{SITE}/api2"

PL = {"columns": [{"key": "2026-09", "year": 2026, "month": 9}, {"key": "2026-10", "year": 2026, "month": 10}],
      "income": [{"kind": "category", "id": 4, "name": "Revenue", "multiplier": 1, "children": [
          {"kind": "account", "id": 41, "code": "4101", "name": "Sales", "multiplier": 1,
           "periods": {"2026-09": 120000, "2026-10": 135500.5}}]}],
      "expenses": [
          {"kind": "category", "id": 5, "name": "Cost of sales", "multiplier": -1, "children": [
              {"kind": "account", "id": 51, "code": "5101", "name": "Goods sold", "multiplier": -1,
               "periods": {"2026-09": -60000, "2026-10": -70250}}]},
          {"kind": "category", "id": 6, "name": "Operating expenses", "multiplier": -1, "children": [
              {"kind": "account", "id": 61, "code": "6101", "name": "Rent", "multiplier": -1,
               "periods": {"2026-09": -15000, "2026-10": -15000}},
              {"kind": "category", "id": 62, "name": "Salaries", "multiplier": -1, "truncated_children": True,
               "children": [{"kind": "account", "id": 999}], "periods": {"2026-10": -40000}}]}]}

BS = {"groups": [
    {"key": "assets", "total": 410000, "accounts": [
        {"kind": "category", "id": 1, "name": "Current assets", "children": [
            {"kind": "account", "id": 11, "code": "1101", "name": "Al Rajhi Bank", "value": 300000},
            {"kind": "account", "id": 12, "code": "1201", "name": "العملاء", "value": 110000}]}]},
    {"key": "liabilities", "total": -90000, "accounts": [
        {"kind": "category", "id": 2, "name": "Current liabilities", "children": [
            {"kind": "account", "id": 21, "code": "2101", "name": "Suppliers payable", "value": -90000}]}]},
    {"key": "owners_equity", "total": -320000, "accounts": [
        {"kind": "account", "id": 31, "code": "3101", "name": "Capital", "value": -320000}]}]}


def ok(data: Any) -> dict[str, Any]:
    return {"result": "successful", "code": 200, "data": data}


def page(items: list[dict[str, Any]], n: int, count: int) -> dict[str, Any]:
    return {"result": "successful", "code": 200, "data": items,
            "pagination": {"page": n, "page_count": count, "next": "more" if n < count else None,
                           "prev": None, "total_results": 0}}


class Addresses(unittest.TestCase):
    def test_only_a_daftra_account_address_is_accepted(self) -> None:
        for good, want in (("harbor-test.daftra.com", SITE), ("https://Harbor-Test.daftra.com/", SITE),
                           ("harbor.daftara.com", "harbor.daftara.com")):
            self.assertEqual(daftra.site_of(good), want)
        for bad in ("evil.example", "harbor.daftra.com.evil.example", "daftra.com", "harbor.daftra.co",
                    "a.b.daftra.com", "harbor.daftra.com/../x", "user@harbor.daftra.com", "", None,
                    "harbor.daftra.com:8443", "-x.daftra.com"):
            with self.assertRaises(Invalid, msg=repr(bad)):
                daftra.site_of(bad)
        self.assertEqual(daftra.token_url(SITE), f"https://{SITE}/api2/v2/oauth/token")


class Reports(unittest.TestCase):
    def test_profit_and_loss_by_month_with_cost_of_sales_apart(self) -> None:
        got = {(r["period"], r["section"], r["account"]): r["amount"] for r in daftra.parse_profit_and_loss(PL)}
        self.assertEqual(got, {
            ("2026-09-01", "Income", "4101 Sales"): 120000.0, ("2026-10-01", "Income", "4101 Sales"): 135500.5,
            ("2026-09-01", "Cost of sales", "5101 Goods sold"): 60000.0,
            ("2026-10-01", "Cost of sales", "5101 Goods sold"): 70250.0,
            ("2026-09-01", "Expenses", "6101 Rent"): 15000.0, ("2026-10-01", "Expenses", "6101 Rent"): 15000.0,
            ("2026-10-01", "Expenses", "Salaries"): 40000.0})   # a cut-off branch is read at its own total

    def test_balance_sheet_reads_each_section_the_right_way_round(self) -> None:
        got = {(r["section"], r["group"], r["account"]): r["amount"]
               for r in daftra.parse_balance_sheet(BS, date(2026, 10, 31))}
        self.assertEqual(got, {("Assets", "Current assets", "1101 Al Rajhi Bank"): 300000.0,
                               ("Assets", "Current assets", "1201 العملاء"): 110000.0,
                               ("Liabilities", "Current liabilities", "2101 Suppliers payable"): 90000.0,
                               ("Equity", "Capital", "3101 Capital"): 320000.0})

    def test_account_classes_come_from_the_reports(self) -> None:
        self.assertEqual(daftra.account_classes(BS, PL), {
            "11": "Bank", "12": "Receivable", "21": "Payable", "31": "Equity", "41": "Income",
            "51": "Cost of sales", "61": "Expense", "62": "Expense"})


class Connection(unittest.TestCase):
    def setUp(self) -> None:
        self.h = Harness()
        self.addCleanup(self.h.close)
        self.ws = self.h.setup_workspace(with_model=False)
        self.cid = self.connect(SITE)
        r = self.h.router
        r.add("GET", f"{API}/site_info.json", ok({"Site": {"business_name": "Harbor Trading (test)",
                                                            "currency_code": "SAR", "country_code": "SA"}}))
        r.add("GET", f"{API}/reports/accounts_profit_accrual", ok(PL))
        r.add("GET", f"{API}/reports/balance_sheet", ok(BS))
        r.add("GET", f"{API}/invoices.json", page([
            {"Invoice": {"id": 1, "no": "INV-001", "client_business_name": "Najd Retail (test)", "date": "2026-10-02",
                         "currency_code": "SAR", "summary_total": 11500, "summary_paid": 5000, "summary_unpaid": 6500,
                         "draft": 0}},
            {"Invoice": {"id": 2, "no": "INV-002", "client_business_name": "Najd Retail (test)", "date": "2026-10-09",
                         "currency_code": "SAR", "summary_total": 2300, "summary_unpaid": 0, "draft": 0}},
            {"Invoice": {"id": 3, "no": "INV-003", "client_business_name": "Draft Co", "date": "2026-10-10",
                         "currency_code": "SAR", "summary_total": 999, "summary_unpaid": 999, "draft": 1}}], 1, 1))
        r.add("GET", f"{API}/purchase_invoices.json", page([
            {"PurchaseOrder": {"id": 7, "no": "PI-7", "supplier_business_name": "Gulf Supply (test)",
                               "date": "2026-10-05", "currency_code": "SAR", "draft": 0}}], 1, 1))
        r.add("GET", f"{API}/journal_accounts.json", page([
            {"JournalAccount": {"id": 11, "code": "1101", "name": "Al Rajhi Bank", "type": 1}},
            {"JournalAccount": {"id": 41, "code": "4101", "name": "Sales", "type": 0}},
            {"JournalAccount": {"id": 77, "code": "1901", "name": "Unused clearing", "type": 1}}], 1, 1))

        def journals(req: httpx.Request) -> httpx.Response:
            n = int(parse_qs(urlparse(str(req.url)).query)["page"][0])
            items = [[{"Journal": {"id": 100, "number": "J-100", "date": "2026-10-02", "description": "Sale",
                                   "entity_type": "invoice", "is_automatic": 1, "JournalTransaction": [
                                       {"journal_account_id": 11, "debit": 11500, "credit": 0},
                                       {"journal_account_id": 41, "debit": 0, "credit": 11500,
                                        "description": "INV-001"}]}}],
                     [{"Journal": {"id": 101, "number": "J-101", "date": "2026-10-20", "description": "Clearing",
                                   "is_automatic": 0, "JournalTransaction": [
                                       {"journal_account_id": 77, "debit": 50, "credit": 0},
                                       {"journal_account_id": 11, "debit": 0, "credit": 50}]}}]][n - 1]
            return httpx.Response(200, json=page(items, n, 2))
        r.add("GET", f"{API}/journals.json", journals)
        self.h.engine.secrets.put("connection", {"tokens": {"access_token": "dt-access", "refresh_token": "dt-refresh",
                                                            "expires_in": 3600, "obtained_at": 4102444800}},
                                  workspace_id=self.ws, secret_id=f"sec_{self.cid}")

    def connect(self, site: str, cid: str = "cn_daftra") -> str:
        self.h.engine.db.insert("connections", {
            "id": cid, "workspace_id": self.ws, "provider": "daftra", "label": "Daftra: Harbor Trading (test)",
            "status": "connected", "config": dumps({"site": site}), "secret_id": f"sec_{cid}",
            "created_by": None, "created_at": now()})
        return cid

    def conn(self, cid: str | None = None) -> dict[str, Any]:
        return dict(self.h.engine.db.one("SELECT * FROM connections WHERE id = ?", (cid or self.cid,)))

    def test_a_sync_makes_every_dataset_and_sends_the_token_only_to_the_account(self) -> None:
        out = asyncio.run(sync_connection(self.h.engine, self.conn()))
        self.assertEqual(out["errors"], {})
        self.assertEqual(set(out["datasets"]), {"profit_and_loss", "balance_sheet", "invoices", "bills",
                                                "general_ledger"})
        sent = self.h.router.requests
        self.assertTrue(sent)
        self.assertEqual({str(x.url).split("/")[2] for x in sent}, {SITE})
        self.assertTrue(all(x.headers["authorization"] == "Bearer dt-access" for x in sent))

    def test_documents_open_paid_and_unknown(self) -> None:
        client = client_for(self.h.engine, self.conn())
        invoices = asyncio.run(client.invoices(open_only=False))
        self.assertEqual([(d["number"], d["total"], d["balance"], d["status"]) for d in invoices],
                         [("INV-001", 11500.0, 6500.0, "partially paid"), ("INV-002", 2300.0, 0.0, "paid")])
        self.assertEqual([d["number"] for d in asyncio.run(client.invoices())], ["INV-001"])
        bills = asyncio.run(client.bills(open_only=False))
        self.assertEqual([(d["number"], d["party"], d["total"], d["balance"], d["status"]) for d in bills],
                         [("PI-7", "Gulf Supply (test)", None, None, "unknown")])
        self.assertEqual(asyncio.run(client.bills()), [])           # not claimed open when Daftra sent no amounts

    def test_journal_lines_over_two_pages_with_their_classes(self) -> None:
        client = client_for(self.h.engine, self.conn())
        lines = asyncio.run(client.journal_lines(date(2026, 9, 1), date(2026, 10, 31)))
        got = [(x["entry"], x["account_code"], x["account"], x["account_class"], x["debit"], x["credit"])
               for x in lines]
        self.assertEqual(got, [("J-100", "1101", "Al Rajhi Bank", "Bank", 11500.0, 0.0),
                               ("J-100", "4101", "Sales", "Income", 0.0, 11500.0),
                               ("J-101", "1901", "Unused clearing", "Asset", 50.0, 0.0),
                               ("J-101", "1101", "Al Rajhi Bank", "Bank", 0.0, 50.0)])
        self.assertEqual(lines[0]["type"], "invoice")
        self.assertEqual(lines[1]["description"], "INV-001")
        asked = self.h.router.find(f"{API}/journals.json")
        self.assertEqual([parse_qs(urlparse(str(x.url)).query)["draft"] for x in asked], [["0"], ["0"]])

    def test_the_company(self) -> None:
        info = asyncio.run(client_for(self.h.engine, self.conn()).company())
        self.assertEqual(info, {"name": "Harbor Trading (test)", "currency": "SAR", "country": "SA"})

    def test_a_connection_whose_address_is_not_daftra_sends_nothing(self) -> None:
        cid = self.connect("harbor.daftra.com.evil.example", "cn_bad")
        with self.assertRaises(Invalid):
            client_for(self.h.engine, self.conn(cid))
        self.assertEqual(self.h.router.requests, [])

    def test_an_error_answer_is_an_error(self) -> None:
        self.h.router.add("GET", f"{API}/site_info.json",
                          {"result": "failed", "code": 403, "message": "missing scope"})
        from desk_engine.errors import UpstreamError
        with self.assertRaises(UpstreamError):
            asyncio.run(client_for(self.h.engine, self.conn()).company())
        self.assertNotIn("dt-access", json.dumps([str(x.url) for x in self.h.router.requests]))


if __name__ == "__main__":
    unittest.main()
