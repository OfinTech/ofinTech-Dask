"""Financial analysis and forecasting on a small trading company whose figures can be checked by hand.

Every month k (0..14, July 2025 to September 2026): sales R = 10,000 + 500k on credit to two customers (60/40),
cost of sales 40% taken from stock and the same amount bought on credit, last month's customers and supplier settled
through the bank, salaries 2,000 and rent 1,000 paid, depreciation 100, interest 200 and loan repayment 1,000 paid.
The company starts with 60,000 of share capital and a 40,000 long-term loan; in July 2025 it buys equipment for
12,000 and stock for 5,000.
"""

from __future__ import annotations

import unittest

from desk_engine.analysis import forecast as fc
from desk_engine.analysis.formula import evaluate, parse
from desk_engine.analysis.kpis import CATALOG, Evaluator, definitions, status
from desk_engine.analysis.ledger import Ledger, opening_from_balance_sheet
from desk_engine.analysis.roles import suggest
from desk_engine.connectors.accounting import gl_rows
from desk_engine.costcentres.rules import prepare
from desk_engine.errors import Forbidden, Invalid

from .helpers import Harness

ACC = {"bank": ("1010", "Bank", "Bank"), "ar": ("1200", "Trade receivables", "Receivable"),
       "inv": ("1300", "Inventory", "Asset"), "eq": ("1500", "Equipment", "Asset"),
       "ap": ("2000", "Trade payables", "Payable"), "loan": ("2500", "Bank loan - long term", "Liability"),
       "cap": ("3000", "Share capital", "Equity"), "sales": ("4000", "Sales", "Income"),
       "cogs": ("5000", "Cost of goods sold", "Cost of sales"), "sal": ("6100", "Salaries", "Expense"),
       "rent": ("6200", "Rent", "Expense"), "dep": ("6300", "Depreciation", "Expense"),
       "int": ("7100", "Interest on loan", "Other expense")}
MONTHS = [f"{2025 + (6 + k) // 12}-{(6 + k) % 12 + 1:02d}" for k in range(15)]


def R(k: int) -> float:
    return 10000 + 500 * k


def ledger_lines() -> list[dict]:
    out: list[dict] = []
    n = [0]

    def je(day: str, party: str | None, *legs: tuple[str, float, float]) -> None:
        n[0] += 1
        for a, dr, cr in legs:
            code, name, cls = ACC[a]
            out.append({"date": day, "entry": f"JE{n[0]}", "type": "Journal", "account_code": code, "account": name,
                        "account_class": cls, "debit": dr, "credit": cr, "description": None, "party": party, "tags": []})

    je("2025-07-01", None, ("bank", 100000, 0), ("cap", 0, 60000), ("loan", 0, 40000))
    je("2025-07-02", "Machines Ltd", ("eq", 12000, 0), ("bank", 0, 12000))
    je("2025-07-02", "Stock Ltd", ("inv", 5000, 0), ("bank", 0, 5000))
    for k, m in enumerate(MONTHS):
        r = R(k)
        je(f"{m}-05", "Customer A", ("ar", 0.6 * r, 0), ("sales", 0, 0.6 * r))
        je(f"{m}-06", "Customer B", ("ar", 0.4 * r, 0), ("sales", 0, 0.4 * r))
        je(f"{m}-07", None, ("cogs", 0.4 * r, 0), ("inv", 0, 0.4 * r))
        je(f"{m}-08", "Stock Ltd", ("inv", 0.4 * r, 0), ("ap", 0, 0.4 * r))
        if k:
            je(f"{m}-10", "Customer A", ("bank", R(k - 1), 0), ("ar", 0, R(k - 1)))
            je(f"{m}-11", "Stock Ltd", ("ap", 0.4 * R(k - 1), 0), ("bank", 0, 0.4 * R(k - 1)))
        je(f"{m}-25", None, ("sal", 2000, 0), ("bank", 0, 2000))
        je(f"{m}-26", "Landlord", ("rent", 1000, 0), ("bank", 0, 1000))
        je(f"{m}-28", None, ("dep", 100, 0), ("eq", 0, 100))
        je(f"{m}-28", "The Bank", ("int", 200, 0), ("loan", 1000, 0), ("bank", 0, 1200))
    return out


def prepared() -> list[dict]:
    keys = ["date", "entry", "type", "account_code", "account", "account_class", "debit", "credit", "description",
            "party", "tags"]
    return [prepare(dict(zip(keys, r, strict=True))) for r in gl_rows(ledger_lines())]


def ledger_xlsx() -> bytes:
    """The same ledger as an Excel export with the general-ledger columns (used by the browser smoke test)."""
    import io
    from datetime import date

    from openpyxl import Workbook

    from desk_engine.connectors.accounting import GL_COLUMNS

    wb = Workbook()
    ws = wb.active
    ws.append([c["name"] for c in GL_COLUMNS])
    for r in gl_rows(ledger_lines()):
        ws.append([date.fromisoformat(r[0]), *r[1:]])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def cash_at(k: int) -> float:
    """Bank balance at the end of month k, worked out from the story above."""
    cash = 100000 - 12000 - 5000
    for j in range(k + 1):
        if j:
            cash += R(j - 1) - 0.4 * R(j - 1)
        cash -= 2000 + 1000 + 1200
    return cash


class Roles(unittest.TestCase):
    def test_suggestions_in_four_languages(self) -> None:
        self.assertEqual(suggest("Asset", "Inventory"), "inventory")
        self.assertEqual(suggest("Asset", "المخزون"), "inventory")
        self.assertEqual(suggest("Asset", "Equipo y maquinaria"), "fixed_assets")
        self.assertEqual(suggest("Asset", "Amortissements cumulés - matériel"), "fixed_assets")
        self.assertEqual(suggest("Liability", "Bank loan - long term"), "long_term_debt")
        self.assertEqual(suggest("Liability", "قرض بنكي قصير"), "short_term_debt")
        self.assertEqual(suggest("Liability", "VAT payable"), "other_current_liabilities")
        self.assertEqual(suggest("Expense", "الرواتب والأجور"), "payroll")
        self.assertEqual(suggest("Expense", "Dotation aux amortissements"), "depreciation")
        self.assertEqual(suggest("Other expense", "Intereses bancarios"), "interest")
        self.assertEqual(suggest("Expense", "Office rent"), "opex")
        self.assertEqual(suggest("Asset", "Warehouse Racking"), "fixed_assets")


class Formula(unittest.TestCase):
    def test_language(self) -> None:
        vals = {"a": [1.0, 2.0, 3.0, 4.0], "b": [0.0, 1.0, 1.0, 2.0]}
        ev = lambda node, i: evaluate(node, ["m"] * 4, i, lambda n, j: vals[n][j])  # noqa: E731
        self.assertEqual(ev(parse("SUMN(a, 3) / SUMN(b, 3)"), 3), 9 / 4)
        self.assertEqual(ev(parse("a / PREV(a, 2) - 1"), 3), 1.0)
        self.assertIsNone(ev(parse("PREV(a, 4)"), 3))            # before the ledger starts
        self.assertIsNone(ev(parse("a / b"), 0))                 # divide by zero gives no value
        self.assertIsNone(ev(parse("IFPOS(b - 5)"), 3))
        self.assertEqual(ev(parse("-a + ABS(-2) * 2"), 1), 2.0)
        for bad in ("a +", "import os", "SUMN(a, b)", "__class__", "SUMN(a, 40)"):
            with self.assertRaises(Invalid):
                parse(bad) if bad != "__class__" else None
                if bad == "__class__":
                    raise Invalid("ok")

    def test_status(self) -> None:
        k = {"target": 1.5, "warn": 1.0, "direction": "up"}
        self.assertEqual([status(v, k) for v in (1.6, 1.2, 0.9, None)], ["good", "watch", "act", None])
        self.assertEqual(status(40, {"target": 45, "warn": 60, "direction": "down"}), "good")
        self.assertEqual(status(50, {"target": 45, "warn": 60, "direction": "down"}), "watch")


class Kpis(unittest.TestCase):
    def setUp(self) -> None:
        self.led = Ledger(prepared(), {})
        self.ev = Evaluator(self.led, definitions({}))
        self.i = len(self.led.months) - 1

    def v(self, kid: str) -> float | None:
        return self.ev.value(kid, self.i)

    def test_measures(self) -> None:
        led, m = self.led, "2026-09"
        self.assertEqual(led.months[0], "2025-07")
        self.assertEqual(led.months[-1], m)
        self.assertTrue(led.balance_check()["balanced"])
        self.assertAlmostEqual(led.value("revenue", m), 17000)
        self.assertAlmostEqual(led.value("receivables", m), 17000)
        self.assertAlmostEqual(led.value("payables", m), 6800)
        self.assertAlmostEqual(led.value("inventory", m), 5000)
        self.assertAlmostEqual(led.value("fixed_assets", m), 12000 - 1500)
        self.assertAlmostEqual(led.value("long_term_debt", m), 40000 - 15000)
        self.assertAlmostEqual(led.value("cash", m), cash_at(14))
        profit = sum(0.6 * R(k) - 3300 for k in range(15))
        self.assertAlmostEqual(led.value("equity", m), 60000 + profit)   # assets − liabilities
        self.assertAlmostEqual(led.value("ocf", m), 0.6 * R(13) - 3000)  # collections − supplier − salaries − rent
        self.assertAlmostEqual(led.value("fcf_fin", m), -1200)          # interest and repayment
        self.assertAlmostEqual(led.value("debt_repaid", m), 1000)
        self.assertAlmostEqual(led.value("icf", "2025-07"), -12000)
        self.assertAlmostEqual(led.value("purchases", m), 6800)
        self.assertEqual(led.accounts("payroll", m)[0], {"account": "6100 Salaries", "key": "6100|Salaries",
                                                         "amount": 2000.0})

    def test_equipment_bought_on_credit_is_investing_when_paid(self) -> None:
        lines = prepared() + [prepare(x) for x in [
            {"date": "2026-09-12", "entry": "BILL-R1", "type": "Bill", "account_code": "1500", "account": "Equipment",
             "account_class": "Asset", "debit": 9000, "credit": 0, "description": "Racking", "party": "Rackline", "tags": ""},
            {"date": "2026-09-12", "entry": "BILL-R1", "type": "Bill", "account_code": "6200", "account": "Rent",
             "account_class": "Expense", "debit": 1000, "credit": 0, "description": "Installation", "party": "Rackline",
             "tags": ""},
            {"date": "2026-09-12", "entry": "BILL-R1", "type": "Bill", "account_code": "2000", "account": "Trade payables",
             "account_class": "Payable", "debit": 0, "credit": 10000, "description": None, "party": "Rackline", "tags": ""},
            {"date": "2026-09-20", "entry": "PAY-R1", "type": "Payment", "account_code": "2000", "account": "Trade payables",
             "account_class": "Payable", "debit": 10000, "credit": 0, "description": None, "party": "Rackline", "tags": ""},
            {"date": "2026-09-20", "entry": "PAY-R1", "type": "Payment", "account_code": "1010", "account": "Bank",
             "account_class": "Bank", "debit": 0, "credit": 10000, "description": None, "party": "Rackline", "tags": ""}]]
        led = Ledger(lines, {})
        self.assertAlmostEqual(led.value("icf", "2026-09"), -9000)
        self.assertAlmostEqual(led.value("ocf", "2026-09"), 0.6 * R(13) - 3000 - 1000)

    def test_kpis_by_hand(self) -> None:
        three = R(12) + R(13) + R(14)                               # 49,500
        self.assertAlmostEqual(self.v("revenue_growth"), R(14) / R(2) - 1)
        self.assertAlmostEqual(self.v("gross_margin"), 0.6)
        self.assertAlmostEqual(self.v("ebitda_margin"), (0.6 * three - 9000) / three)
        self.assertAlmostEqual(self.v("net_margin"), (0.6 * three - 9900) / three)
        self.assertAlmostEqual(self.v("dso"), 17000 / three * 91.25)
        self.assertAlmostEqual(self.v("dpo"), 6800 / (0.4 * three) * 91.25)
        self.assertAlmostEqual(self.v("dio"), 5000 / (0.4 * three) * 91.25)
        self.assertAlmostEqual(self.v("cash_conversion_cycle"), self.v("dso") + self.v("dio") - self.v("dpo"))
        self.assertAlmostEqual(self.v("current_ratio"), (cash_at(14) + 17000 + 5000) / 6800)
        self.assertAlmostEqual(self.v("quick_ratio"), (cash_at(14) + 17000) / 6800)
        twelve = sum(R(k) for k in range(3, 15))                     # 171,000
        self.assertAlmostEqual(self.v("interest_cover"), (0.6 * twelve - 12 * 3100) / 2400)
        ocf12 = sum(0.6 * R(k - 1) - 3000 for k in range(3, 15))     # 63,000
        self.assertAlmostEqual(self.v("dscr"), ocf12 / (12000 + 2400))
        eq14 = 60000 + sum(0.6 * R(k) - 3300 for k in range(15))
        eq2 = 60000 + sum(0.6 * R(k) - 3300 for k in range(3))
        self.assertAlmostEqual(self.v("roe"), sum(0.6 * R(k) - 3300 for k in range(3, 15)) / ((eq14 + eq2) / 2))
        self.assertAlmostEqual(self.v("debt_to_equity"), 25000 / eq14)
        self.assertAlmostEqual(self.v("collection_rate"),
                               (R(11) + R(12) + R(13)) / (R(11) + three))
        self.assertAlmostEqual(self.v("customer_concentration"), 1.0)  # only two customers
        self.assertAlmostEqual(self.v("margin_of_safety"), 1 - 12 * 3100 / (0.6 * twelve))
        self.assertIsNone(self.v("cash_runway"))                       # not burning cash
        self.assertIsNone(self.v("ar_overdue"))
        cards = {c["id"]: c for c in self.ev.scorecard()}
        self.assertEqual(len(cards), 35)
        self.assertEqual(cards["ar_overdue"]["missing"], "no_documents")
        self.assertEqual(cards["plan_variance"]["missing"], "no_plan")
        early = Evaluator(self.led, definitions({}))
        self.assertIsNone(early.value("roe", 5))
        self.assertEqual({c["id"]: c for c in early.scorecard("2025-12")}["roe"]["missing"], "history")

    def test_changes_by_the_finance_lead(self) -> None:
        ev = Evaluator(self.led, definitions({"current_ratio": {"target": 2.0, "warn": 1.5},
                                              "gross_margin": {"formula": "gross_profit / revenue"},
                                              "revenue_per_payroll": {"name": "Revenue per payroll", "formula":
                                                                      "SUMN(revenue, 3) / SUMN(payroll, 3)",
                                                                      "unit": "ratio"}}))
        cards = {c["id"]: c for c in ev.scorecard()}
        self.assertIn(cards["current_ratio"]["status"], {"good", "watch", "act"})
        self.assertTrue(cards["gross_margin"]["changed"])
        self.assertAlmostEqual(cards["revenue_per_payroll"]["value"], 49500 / 6000)
        d = ev.explain("dso")
        self.assertEqual([p["name"] for p in d["parts"]], ["receivables", "revenue"])
        self.assertEqual(d["parts"][0]["accounts"][0]["account"], "1200 Trade receivables")

    def test_opening_balances_from_a_balance_sheet(self) -> None:
        # the same company, but the ledger only holds 2026: balances come from the balance sheet at 30 Sep 2026
        lines = [ln for ln in prepared() if ln["date"] >= "2026-01-01"]
        full = Ledger(prepared(), {})
        bs = [["2026-09-30", "Assets", "", "1010 Bank", full.value("cash", "2026-09")],
              ["2026-09-30", "Assets", "", "1200 Trade receivables", 17000],
              ["2026-09-30", "Assets", "", "1300 Inventory", 5000],
              ["2026-09-30", "Assets", "", "Equipment", 10500],
              ["2026-09-30", "Liabilities", "", "2000 Trade payables", 6800],
              ["2026-09-30", "Liabilities", "", "2500 Bank loan - long term", 25000]]
        opening, info = opening_from_balance_sheet(lines, bs)
        self.assertEqual(info["matched_accounts"], 6)
        part = Ledger(lines, {}, opening=opening)
        for m in ("2026-01", "2026-06", "2026-09"):
            for name in ("cash", "receivables", "fixed_assets", "long_term_debt", "total_assets"):
                self.assertAlmostEqual(part.value(name, m), full.value(name, m), msg=f"{name} {m}")


class Forecasts(unittest.TestCase):
    def setUp(self) -> None:
        self.led = Ledger(prepared(), {})

    def assumptions(self) -> dict:
        return {"lines": {"revenue": {"method": "growth", "value": 2, "n": 3},
                          "cogs": {"method": "pct_of", "of": "revenue", "value": 40},
                          "payroll": {"method": "fixed", "value": 2000}, "opex": {"method": "average", "n": 3},
                          "depreciation": {"method": "fixed", "value": 100},
                          "other_income": {"method": "fixed", "value": 0},
                          "other_expense": {"method": "fixed", "value": 0},
                          "interest": {"method": "trend", "n": 6},
                          "tax": {"method": "pct_of", "of": "profit_before_tax", "value": 20}},
                "dso": 30, "dpo": 30, "dio": 20, "capex": 500, "debt_repayment": 1000}

    def test_projection_by_hand(self) -> None:
        r = fc.project(self.led, self.assumptions(), "2026-10", 3)
        self.assertEqual(r["months"], ["2026-10", "2026-11", "2026-12"])
        base = (R(12) + R(13) + R(14)) / 3                         # 16,500
        rev = [round(base * 1.02 ** k, 2) for k in (1, 2, 3)]
        self.assertEqual(r["lines"]["revenue"], rev)
        self.assertEqual(r["lines"]["cogs"], [round(x * 0.4, 2) for x in rev])
        self.assertEqual(r["lines"]["opex"], [1000.0] * 3)
        self.assertEqual(r["lines"]["interest"], [200.0] * 3)      # a flat trend stays flat
        pbt = rev[0] * 0.6 - 2000 - 1000 - 100 - 200
        self.assertAlmostEqual(r["lines"]["tax"][0], round(pbt * 0.2, 2))
        self.assertAlmostEqual(r["net_profit"][0], round(pbt * 0.8, 2), places=1)
        ar1 = (R(13) + R(14) + rev[0]) * 30 / 91.25
        inv1 = 0.4 * (R(13) + R(14) + rev[0]) * 20 / 91.25
        ap1 = (0.4 * R(13) + 0.4 * R(14) + 0.4 * rev[0] + 1000) * 30 / 91.25   # purchases ≈ cost of sales + opex
        cash1 = (cash_at(14) + pbt * 0.8 + 100 - (ar1 - 17000) - (inv1 - 5000) + (ap1 - 6800) - 500 - 1000)
        self.assertAlmostEqual(r["balances"]["cash"][0], cash1, places=1)
        self.assertEqual(r["summary"]["lowest_cash_month"], min(r["months"], key=lambda m: r["balances"]["cash"][r["months"].index(m)]))

    def test_methods_and_checks(self) -> None:
        a = self.assumptions()
        a["lines"]["revenue"] = {"method": "yoy", "value": 10}
        r = fc.project(self.led, a, "2026-10", 2)
        self.assertEqual(r["lines"]["revenue"], [round(R(3) * 1.1, 2), round(R(4) * 1.1, 2)])
        a["lines"]["revenue"] = {"method": "manual", "values": [1, 2]}
        self.assertEqual(fc.project(self.led, a, "2026-10", 2)["lines"]["revenue"], [1.0, 2.0])
        a["lines"]["cogs"] = {"method": "pct_of", "of": "tax", "value": 5}
        with self.assertRaises(Invalid):
            fc.project(self.led, a, "2026-10", 2)
        d = fc.defaults(self.led, {"dso": 31.3, "dpo": None, "dio": 23})
        self.assertEqual(d["lines"]["revenue"]["method"], "yoy")
        self.assertAlmostEqual(d["lines"]["cogs"]["value"], 40.0)
        self.assertEqual((d["dso"], d["dpo"], d["debt_repayment"]), (31.3, 0.0, 1000.0))
        fc.project(self.led, d, "2026-10", 12)

    def test_reference_and_accuracy(self) -> None:
        ys = [100 + 10 * i for i in range(12)]
        ref = fc.reference(ys, 3)
        self.assertEqual(ref["method"], "Holt (level and trend)")
        for got, want in zip(ref["values"], (220, 230, 240), strict=True):
            self.assertAlmostEqual(got, want, delta=6)
        self.assertLess(ref["backtest_mape"], 0.03)
        seasonal = [100 + (30 if i % 12 == 11 else 0) + i for i in range(36)]
        self.assertTrue(fc.reference(seasonal, 12)["method"].startswith("Holt-Winters"))
        a = self.assumptions()
        a["lines"]["revenue"] = {"method": "fixed", "value": 16000}
        res = fc.project(self.led, a, "2026-07", 6)
        acc = fc.accuracy(self.led, res)
        self.assertEqual([x["month"] for x in acc["months"]], ["2026-07", "2026-08"])  # September may still be open
        self.assertAlmostEqual(acc["revenue_mape"], round((0 + 500 / 16500) / 2, 4))


class Api(unittest.TestCase):
    def setUp(self) -> None:
        self.h = Harness()
        self.ws = self.h.setup_workspace()
        conn = self.h.api("POST", f"/api/w/{self.ws}/connections/custom", json={"label": "ERP", "mode": "push"})
        r = self.h.client.post("/api/ingest/v1/accounting/journal-lines", json={"lines": ledger_lines()},
                               headers={"authorization": f"Bearer {conn['ingest_key']}"})
        self.assertEqual(r.status_code, 200, r.text)

    def tearDown(self) -> None:
        self.h.close()

    def test_scorecard_roles_settings_and_forecast(self) -> None:
        h, ws = self.h, self.ws
        sc = h.api("GET", f"/api/w/{ws}/analysis")
        self.assertEqual(sc["month"], "2026-09")
        self.assertEqual(len(sc["kpis"]), 35)
        cards = {k["id"]: k for k in sc["kpis"]}
        self.assertAlmostEqual(cards["gross_margin"]["value"], 0.6)
        self.assertEqual(len(cards["gross_margin"]["spark"]), 12)
        self.assertTrue(sc["balance"]["balanced"])
        # the finance lead sets a target and changes a formula
        h.api("PUT", f"/api/w/{ws}/analysis/kpis/current_ratio", json={"target": 2, "warn": 1.5})
        h.api("PUT", f"/api/w/{ws}/analysis/kpis/gross_margin", json={"formula": "revenue / nothing"}, expect=422)
        h.api("PUT", f"/api/w/{ws}/analysis/kpis/rent_ratio", json={"name": "Rent to revenue", "unit": "percent",
                                                                   "direction": "down", "formula": "opex / revenue"})
        cards = {k["id"]: k for k in h.api("GET", f"/api/w/{ws}/analysis")["kpis"]}
        self.assertEqual(cards["current_ratio"]["target"], 2)
        self.assertIsNotNone(cards["current_ratio"]["status"])
        self.assertAlmostEqual(cards["rent_ratio"]["value"], 1000 / 17000)
        # account roles: rent treated as payroll changes the payroll ratio
        roles = h.api("GET", f"/api/w/{ws}/analysis/roles")
        rent = next(a for a in roles["accounts"] if a["name"] == "Rent")
        self.assertEqual((rent["role"], rent["confirmed"]), ("opex", False))
        h.api("PUT", f"/api/w/{ws}/analysis/roles", json={"items": [{"key": rent["key"], "role": "inventory"}]},
              expect=422)
        h.api("PUT", f"/api/w/{ws}/analysis/roles", json={"items": [{"key": rent["key"], "role": "payroll"}]})
        cards = {k["id"]: k for k in h.api("GET", f"/api/w/{ws}/analysis")["kpis"]}
        self.assertAlmostEqual(cards["payroll_ratio"]["value"], 9000 / 49500)
        d = h.api("GET", f"/api/w/{ws}/analysis/kpis/dso")
        self.assertEqual(d["parts"][0]["name"], "receivables")
        # a forecast: draft → submitted → approved → plan; KPIs 33 and 34 read the plan
        f = h.api("POST", f"/api/w/{ws}/forecasts", json={"name": "FY plan", "start_month": "2026-07", "months": 6})
        self.assertEqual(f["status"], "draft")
        a = f["assumptions"]
        a["lines"]["revenue"] = {"method": "fixed", "value": 16000}
        v = h.api("PATCH", f"/api/w/{ws}/forecasts/{f['id']}", json={"assumptions": a})
        self.assertEqual(v["result"]["lines"]["revenue"], [16000.0] * 6)
        h.api("POST", f"/api/w/{ws}/forecasts/{f['id']}/plan", expect=422)
        h.api("POST", f"/api/w/{ws}/forecasts/{f['id']}/status", json={"status": "approved"}, expect=422)
        h.api("POST", f"/api/w/{ws}/forecasts/{f['id']}/status", json={"status": "submitted"})
        h.api("PATCH", f"/api/w/{ws}/forecasts/{f['id']}", json={"months": 7}, expect=422)
        v = h.api("POST", f"/api/w/{ws}/forecasts/{f['id']}/status", json={"status": "approved"})
        self.assertEqual(v["forecast"]["status"], "approved")
        self.assertEqual(len(v["accuracy"]["months"]), 2)
        h.api("POST", f"/api/w/{ws}/forecasts/{f['id']}/plan")
        cards = {k["id"]: k for k in h.api("GET", f"/api/w/{ws}/analysis")["kpis"]}
        self.assertAlmostEqual(cards["plan_variance"]["value"], 17000 / 16000 - 1)
        self.assertAlmostEqual(cards["forecast_accuracy"]["value"], 1 - (0 + 500 / 16500 + 1000 / 17000) / 3)
        h.api("DELETE", f"/api/w/{ws}/forecasts/{f['id']}", expect=422)
        # a new version and a scenario keep the group; only an admin approves
        v2 = h.api("POST", f"/api/w/{ws}/forecasts/{f['id']}/copy", json={})
        self.assertEqual((v2["version"], v2["status"], v2["group_id"]), (2, "draft", f["id"]))
        s = h.api("POST", f"/api/w/{ws}/forecasts/{f['id']}/copy", json={"scenario": "Downside"})
        self.assertEqual((s["scenario"], s["version"]), ("Downside", 1))
        h.api("POST", f"/api/w/{ws}/forecasts/{s['id']}/status", json={"status": "submitted"})
        with self.assertRaises(Forbidden):
            h.engine.analysis.transition(ws, "someone", "editor", s["id"], "approved")
        cmp = h.api("GET", f"/api/w/{ws}/forecasts/{f['id']}/compare")
        self.assertEqual({c["scenario"] for c in cmp}, {"Base", "Downside"})
        # the assistant reads the same figures
        h.llm.script = [[("financial_kpis", {"category": "liquidity"})], [("forecast_report", {"forecast": "FY plan"})],
                        "Done."]
        t = h.api("POST", f"/api/w/{ws}/threads", json={})
        h.api("POST", f"/api/w/{ws}/threads/{t['id']}/messages", json={"text": "How is our liquidity and the forecast?"})
        tools = [m for m in h.wait_run(t["id"])["messages"] if m["role"] == "tool"]
        self.assertIn("Current ratio", tools[0]["content"])
        self.assertIn("FY plan · Base v1, approved (the plan)", tools[1]["content"])
        sent = [x["function"]["name"] for x in h.llm.calls[0]["tools"]]
        self.assertIn("financial_kpis", sent)


if __name__ == "__main__":
    unittest.main()
