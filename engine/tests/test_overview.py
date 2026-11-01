"""The executive overview and its drill-down, on the demo company's general ledger.

Every figure on the overview must reconcile with the level below it: the headline net profit with the profit and
loss lines, each line with the accounts behind it, each account with its ledger lines in the month, and each entry
must balance. Provenance is "unverified" until the finance team confirms the account roles.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from .helpers import Harness

DEMO = Path(__file__).resolve().parents[2] / "examples" / "demo-company" / "HarborLine-demo" / "ofintech-desk"
GL = DEMO / "HarborLine_General_Ledger.xlsx"


class OverviewTests(unittest.TestCase):
    def setUp(self) -> None:
        self.h = Harness(script=["- Revenue is up on last year.\n- Cash covers about two months of payments.\n- Watch the overdue receivables."])
        self.ws = self.h.setup_workspace()
        up = self.h.api("POST", f"/api/w/{self.ws}/cost-centres/ledger/upload", files={"file": ("GL.xlsx", GL.read_bytes())})
        self.did = up["dataset"]["id"]

    def tearDown(self) -> None:
        self.h.close()

    def test_overview_reconciles_with_the_drill_down(self) -> None:
        h, ws = self.h, self.ws
        ov = h.api("GET", f"/api/w/{ws}/overview")
        self.assertEqual(ov["month"], "2026-10")
        self.assertEqual(ov["months"][0], "2025-09")
        self.assertEqual(ov["currency"], "GBP")
        self.assertEqual(ov["provenance"]["kind"], "unverified")
        self.assertIn("roles_unconfirmed", ov["provenance"]["notes"])
        head = ov["headline"]
        for k in ("revenue", "expenses", "net_profit", "cash", "ocf", "fcf", "receivables", "payables"):
            self.assertIn(k, head)
            self.assertEqual(len(head[k]["series"]), 12)
        self.assertGreater(head["revenue"]["value"], 0)
        self.assertIsNotNone(head["revenue"]["change_year"])          # 14 months: same month last year exists
        self.assertAlmostEqual(head["net_profit"]["value"], head["revenue"]["value"] - head["expenses"]["value"]
                               + (ov["headline"].get("other_income", {}).get("value") or 0), places=2)
        self.assertIn(ov["health"]["label"], {"strong", "stable", "watch", "at_risk"})
        self.assertEqual(ov["health"]["good"] + ov["health"]["watch"] + ov["health"]["act"], ov["health"]["kpis_scored"])
        self.assertTrue(all(r["level"] in {"high", "medium"} for r in ov["risks"]))
        self.assertTrue(any(r["kind"] == "roles_unconfirmed" for r in ov["risks"]))
        kinds = [x["kind"] for x in ov["insights"]]
        self.assertIn("revenue_yoy", kinds)
        self.assertIn("days_cash", kinds)
        self.assertEqual(ov["decisions"], [])

        # the month travels: an earlier month gives that month's figures and the previous one for comparison
        feb = h.api("GET", f"/api/w/{ws}/overview?month=2026-02")
        self.assertEqual(feb["month"], "2026-02")
        self.assertEqual(feb["headline"]["revenue"]["previous"], h.api("GET", f"/api/w/{ws}/overview?month=2026-01")["headline"]["revenue"]["value"])
        self.assertIsNone(feb["headline"]["revenue"]["last_year"])
        h.api("GET", f"/api/w/{ws}/overview?month=2026-1", expect=422)

        # level 2: profit and loss lines; net profit is the total of the lines, as on the overview
        pl = h.api("GET", f"/api/w/{ws}/overview/profitability?month=2026-10")
        lines = {ln["measure"]: ln for ln in pl["lines"]}
        self.assertEqual(next(iter(lines)), "revenue")
        self.assertEqual(list(lines)[-1], "net_profit")
        self.assertAlmostEqual(lines["net_profit"]["value"], head["net_profit"]["value"], places=2)
        costs = sum(lines[c]["value"] for c in ("cogs", "payroll", "opex", "depreciation", "interest", "tax", "other_expense"))
        self.assertAlmostEqual(lines["net_profit"]["value"], lines["revenue"]["value"] + lines["other_income"]["value"] - costs, places=2)
        self.assertAlmostEqual(lines["gross_profit"]["value"], lines["revenue"]["value"] - lines["cogs"]["value"], places=2)
        self.assertTrue(lines["opex"]["drillable"] and not lines["gross_profit"]["drillable"])
        self.assertGreater(lines["opex"]["accounts"], 1)
        self.assertEqual(pl["fiscal_year_start"], "2026-04")                   # Acme's year starts in April
        self.assertAlmostEqual(lines["revenue"]["ytd"], sum(
            h.api("GET", f"/api/w/{ws}/overview/profitability?month={m}")["lines"][0]["value"]
            for m in ("2026-04", "2026-05", "2026-06", "2026-07", "2026-08", "2026-09", "2026-10")), places=2)

        # level 3: the accounts behind operating expenses add up to the line
        br = h.api("GET", f"/api/w/{ws}/overview/breakdown/opex?month=2026-10")
        self.assertAlmostEqual(br["total"], lines["opex"]["value"], places=2)
        self.assertAlmostEqual(sum(x["value"] for x in br["items"]), br["total"], places=2)
        self.assertAlmostEqual(sum(x["share"] for x in br["items"]), 1.0, places=3)
        self.assertEqual(br["items"], sorted(br["items"], key=lambda x: -abs(x["value"])))
        h.api("GET", f"/api/w/{ws}/overview/breakdown/gross_profit", expect=404)

        # level 4: the ledger lines of the largest account in the month add up to its amount
        top = br["items"][0]
        tx = h.api("GET", f"/api/w/{ws}/overview/transactions/opex?month=2026-10&account={top['key']}")
        self.assertEqual(tx["account"], top["account"])
        self.assertEqual(tx["count"], top["lines"])
        self.assertAlmostEqual(tx["total"], top["value"], places=2)
        self.assertAlmostEqual(sum(r["amount"] for r in tx["rows"]), tx["total"], places=2)
        self.assertTrue(all(r["date"].startswith("2026-10") for r in tx["rows"]))
        h.api("GET", f"/api/w/{ws}/overview/transactions/opex?month=2026-10", expect=422)

        # level 5: one journal entry, balanced, with its source and the import in the audit trail
        first = tx["rows"][0]
        en = h.api("GET", f"/api/w/{ws}/overview/entries/{first['entry']}?month=2026-10")
        self.assertEqual(en["entry"], first["entry"])
        self.assertTrue(en["balanced"])
        self.assertGreaterEqual(len(en["lines"]), 2)
        self.assertAlmostEqual(sum(ln["debit"] for ln in en["lines"]), sum(ln["credit"] for ln in en["lines"]), places=2)
        self.assertEqual(en["source"]["dataset_id"], self.did)
        self.assertEqual(en["source"]["kind"], "excel")
        self.assertTrue(any(a["action"].startswith("dataset.") or "ledger" in a["action"] for a in en["audit"]), en["audit"])
        h.api("GET", f"/api/w/{ws}/overview/entries/no-such-entry", expect=404)

    def test_ai_insights_are_labelled_estimates(self) -> None:
        h, ws = self.h, self.ws
        res = h.api("POST", f"/api/w/{ws}/overview/insights?month=2026-10&lang=en", json={})
        self.assertEqual(res["provenance"], "estimate")
        self.assertEqual(res["month"], "2026-10")
        self.assertEqual(res["items"], ["Revenue is up on last year.", "Cash covers about two months of payments.",
                                        "Watch the overdue receivables."])
        sent = h.llm.calls[-1]["messages"]
        self.assertIn("chief executive", sent[0]["content"])
        self.assertIn("English", sent[0]["content"])
        self.assertIn('"figures"', sent[1]["content"])

    def test_without_a_ledger(self) -> None:
        h2 = Harness()
        try:
            ws = h2.setup_workspace(with_model=False)
            err = h2.api("GET", f"/api/w/{ws}/overview", expect=422)
            self.assertEqual(err["error"], "no_ledger")
        finally:
            h2.close()


if __name__ == "__main__":
    unittest.main()


class StatementsTests(unittest.TestCase):
    """The CFO's pages agree with the overview and with each other, on the demo company."""

    def setUp(self) -> None:
        self.h = Harness()
        self.ws = self.h.setup_workspace(with_model=False)
        self.h.api("POST", f"/api/w/{self.ws}/cost-centres/ledger/upload", files={"file": ("GL.xlsx", GL.read_bytes())})

    def tearDown(self) -> None:
        self.h.close()

    def test_statements_reconcile(self) -> None:
        h, ws = self.h, self.ws
        st = h.api("GET", f"/api/w/{ws}/overview/statements?month=2026-10&periods=6")
        self.assertEqual(st["columns"], ["2026-05", "2026-06", "2026-07", "2026-08", "2026-09", "2026-10"])
        ov = h.api("GET", f"/api/w/{ws}/overview?month=2026-10")
        pl = {ln["measure"]: ln for ln in st["profit_and_loss"]["lines"]}
        self.assertAlmostEqual(pl["net_profit"]["values"][-1], ov["headline"]["net_profit"]["value"], places=2)
        self.assertAlmostEqual(pl["revenue"]["ytd"], ov["headline"]["revenue"]["ytd"], places=2)
        self.assertIsNone(pl["revenue"]["plan"])
        # balance sheet: assets = liabilities + equity in every column, and sections add up to their totals
        bs = st["balance_sheet"]
        for n in range(len(st["columns"])):
            assets = bs["sections"][0]["total"][n] + bs["sections"][1]["total"][n]
            liabilities = bs["sections"][2]["total"][n] + bs["sections"][3]["total"][n]
            self.assertAlmostEqual(assets, bs["totals"]["total_assets"][n], places=2)
            self.assertAlmostEqual(liabilities, bs["totals"]["total_liabilities"][n], places=2)
            self.assertAlmostEqual(assets - liabilities, bs["totals"]["equity"][n], places=2)
            for sec in bs["sections"]:
                self.assertAlmostEqual(sum(r["values"][n] for r in sec["rows"]), sec["total"][n], places=2)
        self.assertAlmostEqual(bs["sections"][0]["rows"][0]["values"][-1], ov["headline"]["cash"]["value"], places=2)
        # cash flow: opening + operating + investing + financing = closing, every month, with nothing unexplained
        for cf in st["cash_flow"]:
            self.assertAlmostEqual(cf["opening"] + cf["net"], cf["closing"], places=2)
            self.assertAlmostEqual(cf["unexplained"], 0.0, places=2)
        self.assertAlmostEqual(st["cash_flow"][-1]["operating"], ov["headline"]["ocf"]["value"], places=2)
        h.api("GET", f"/api/w/{ws}/overview/statements?periods=0", expect=422)

    def test_budget_against_actual_needs_a_plan_then_uses_it(self) -> None:
        h, ws = self.h, self.ws
        b = h.api("GET", f"/api/w/{ws}/overview/budget?month=2026-10")
        self.assertFalse(b["has_plan"])
        self.assertTrue(all(ln["plan"] is None for ln in b["lines"]))
        # the ledger starts in September 2025, so a year-on-year plan can start in September 2026
        f = h.api("POST", f"/api/w/{ws}/forecasts", json={"name": "FY plan", "start_month": "2026-09", "months": 3})
        h.api("POST", f"/api/w/{ws}/forecasts/{f['id']}/status", json={"status": "submitted"})
        h.api("POST", f"/api/w/{ws}/forecasts/{f['id']}/status", json={"status": "approved"})
        h.api("POST", f"/api/w/{ws}/forecasts/{f['id']}/plan")
        b = h.api("GET", f"/api/w/{ws}/overview/budget?month=2026-10")
        self.assertTrue(b["has_plan"])
        lines = {ln["measure"]: ln for ln in b["lines"]}
        rev = lines["revenue"]
        self.assertIsNotNone(rev["plan"])
        self.assertAlmostEqual(rev["variance"]["amount"], rev["actual"] - rev["plan"], places=2)
        self.assertEqual(rev["variance"]["favourable"], rev["actual"] >= rev["plan"])
        cogs = lines["cogs"]
        self.assertEqual(cogs["variance"]["favourable"], cogs["actual"] <= cogs["plan"])
        # the plan's net profit is the plan's lines combined, as the actual is
        self.assertAlmostEqual(lines["net_profit"]["plan"], lines["revenue"]["plan"] + lines["other_income"]["plan"] - sum(
            lines[c]["plan"] for c in ("cogs", "payroll", "opex", "depreciation", "interest", "tax", "other_expense")), places=2)
        self.assertTrue(all(d["accounts"] for d in b["drivers"]))
        # the statements carry the plan too
        st = h.api("GET", f"/api/w/{ws}/overview/statements?month=2026-10&periods=3")
        pl = {ln["measure"]: ln for ln in st["profit_and_loss"]["lines"]}
        self.assertEqual(pl["revenue"]["plan"][-1], rev["plan"])

    def test_receivables_and_payables_by_party_with_ageing(self) -> None:
        h, ws = self.h, self.ws
        ar = h.api("GET", f"/api/w/{ws}/overview/parties/receivables?month=2026-10")
        ov = h.api("GET", f"/api/w/{ws}/overview?month=2026-10")
        self.assertEqual(ar["as_of"], "2026-10-31")
        self.assertAlmostEqual(ar["ledger_balance"], ov["headline"]["receivables"]["value"], places=2)
        self.assertAlmostEqual(ar["difference"], 0.0, places=2)             # every open item is explained by a document
        self.assertAlmostEqual(sum(r["closing"] for r in ar["rows"]), ar["totals"]["closing"], places=2)
        for r in ar["rows"]:
            self.assertAlmostEqual(sum(r["buckets"]) - r["unapplied"], r["closing"], places=2)
            self.assertAlmostEqual(r["opening"] + r["raised"] - r["settled"], r["closing"], places=2)
        kestrel = next(r for r in ar["rows"] if r["party"] == "Kestrel Auto Parts")   # the demo's slow payer
        self.assertGreater(kestrel["buckets"][3], 40000)
        self.assertLess(kestrel["oldest"], "2026-05-01")
        self.assertEqual(ar["rows"], sorted(ar["rows"], key=lambda r: -abs(r["closing"])))
        ap = h.api("GET", f"/api/w/{ws}/overview/parties/payables?month=2026-10")
        self.assertAlmostEqual(ap["ledger_balance"], ov["headline"]["payables"]["value"], places=2)
        self.assertAlmostEqual(ap["difference"], 0.0, places=2)
        self.assertTrue(all(r["party"] != "—" for r in ap["rows"]))
        h.api("GET", f"/api/w/{ws}/overview/parties/loans", expect=422)
        # an earlier month ages against that month's end
        feb = h.api("GET", f"/api/w/{ws}/overview/parties/receivables?month=2026-02")
        self.assertEqual(feb["as_of"], "2026-02-28")
        self.assertAlmostEqual(feb["difference"], 0.0, places=2)

    def test_anomalies_find_what_the_demo_planted(self) -> None:
        h, ws = self.h, self.ws
        an = h.api("GET", f"/api/w/{ws}/overview/anomalies?month=2026-10")
        kinds = {(a["kind"], a["account"]) for a in an["items"]}
        self.assertIn(("unusual_amount", "6400 Professional Fees"), kinds)       # the fit-out contractor's bill
        self.assertIn(("missing_posting", "6500 Depreciation - Vehicles"), kinds)  # depreciation not charged in October
        self.assertNotIn(("unusual_amount", "6500 Depreciation - Vehicles"), kinds)
        self.assertGreaterEqual(an["materiality"], 500)
        fees = next(a for a in an["items"] if a["account"] == "6400 Professional Fees")
        self.assertEqual(fees["level"], "high")
        self.assertGreater(fees["value"], fees["average"] * 3)
        self.assertEqual(an["items"], sorted(an["items"], key=lambda a: {"high": 0, "medium": 1}[a["level"]]))


class OperationsTests(unittest.TestCase):
    """The general-ledger browser, accounting validation and the accounting manager's desk, on the demo company."""

    def setUp(self) -> None:
        self.h = Harness()
        self.ws = self.h.setup_workspace(with_model=False)
        up = self.h.api("POST", f"/api/w/{self.ws}/cost-centres/ledger/upload", files={"file": ("GL.xlsx", GL.read_bytes())})
        self.did = up["dataset"]["id"]

    def tearDown(self) -> None:
        self.h.close()

    def test_ledger_browser(self) -> None:
        h, ws = self.h, self.ws
        e = h.api("GET", f"/api/w/{ws}/ledger/entries?month=2026-10&limit=10")
        self.assertEqual((e["count"], e["unbalanced"], len(e["rows"])), (40, 0, 10))
        self.assertEqual(e["rows"], sorted(e["rows"], key=lambda r: (r["date"], r["entry"]), reverse=True))
        self.assertTrue(all(r["balanced"] and r["lines"] >= 2 for r in e["rows"]))
        self.assertIn("Invoice", e["types"])
        page2 = h.api("GET", f"/api/w/{ws}/ledger/entries?month=2026-10&limit=10&offset=10")
        self.assertEqual(len(page2["rows"]), 10)
        self.assertFalse({r["entry"] for r in page2["rows"]} & {r["entry"] for r in e["rows"]})
        # text search across the whole ledger, filters by type, party and account, and the month travels
        s = h.api("GET", f"/api/w/{ws}/ledger/entries?q=kestrel")
        self.assertGreater(s["count"], 10)
        self.assertTrue(all("Kestrel" in (r["party"] or "") or "Kestrel" in (r["description"] or "") for r in s["rows"]))
        inv = h.api("GET", f"/api/w/{ws}/ledger/entries?month=2026-10&type=Invoice")
        self.assertTrue(all(r["type"] == "Invoice" for r in inv["rows"]) and inv["count"] > 0)
        acc = h.api("GET", f"/api/w/{ws}/ledger/entries?month=2026-10&account=6400|Professional Fees")
        self.assertEqual(acc["count"], 1)
        big = h.api("GET", f"/api/w/{ws}/ledger/entries?month=2026-10&min=50000")
        self.assertTrue(all(max(r["debit"], r["credit"]) >= 50000 for r in big["rows"]))
        h.api("GET", f"/api/w/{ws}/ledger/entries?limit=0", expect=422)
        h.api("GET", f"/api/w/{ws}/ledger/entries?min=abc", expect=422)
        # accounts: every account with its role, month activity and month-end balance; the suspense account is flagged
        a = h.api("GET", f"/api/w/{ws}/ledger/accounts?month=2026-10")
        self.assertEqual(len(a["rows"]), 23)
        bank = next(r for r in a["rows"] if r["code"] == "1000")
        ov = h.api("GET", f"/api/w/{ws}/overview?month=2026-10")
        self.assertAlmostEqual(bank["balance"], ov["headline"]["cash"]["value"], places=2)
        self.assertEqual((bank["role"], bank["kind"], bank["confirmed"]), ("cash", "balance", False))
        susp = next(r for r in a["rows"] if r["suspense"])
        self.assertEqual((susp["name"], susp["balance"]), ("1900 Suspense", 4800.0))
        fees = next(r for r in a["rows"] if r["code"] == "6400")
        self.assertEqual((fees["kind"], fees["balance"]), ("pl", 19900.0))

    def test_validation(self) -> None:
        h, ws = self.h, self.ws
        v = h.api("GET", f"/api/w/{ws}/ledger/validation?month=2026-10")
        checks = {c["kind"]: c for c in v["checks"]}
        self.assertEqual(checks["suspense_balances"]["count"], 1)
        self.assertEqual(checks["suspense_balances"]["samples"][0]["account"], "1900 Suspense")
        self.assertEqual(checks["unconfirmed_roles"]["count"], 17)             # accounts used in October
        self.assertEqual(checks["unbalanced_entries"]["count"], 0)
        self.assertFalse(v["clean"])
        self.assertEqual(v["issues"], 18)
        self.assertEqual(v["checks"][0]["level"], "high")
        whole = h.api("GET", f"/api/w/{ws}/ledger/validation")
        self.assertEqual({c["kind"]: c["count"] for c in whole["checks"] if c["count"]}, {"suspense_balances": 1, "unconfirmed_roles": 23})
        # confirming the roles clears that check
        roles = h.api("GET", f"/api/w/{ws}/analysis/roles")
        items = [{"key": a["key"], "role": a["role"]} for a in roles["accounts"]]
        h.api("PUT", f"/api/w/{ws}/analysis/roles", json={"items": items})
        after = h.api("GET", f"/api/w/{ws}/ledger/validation")
        self.assertEqual({c["kind"]: c["count"] for c in after["checks"] if c["count"]}, {"suspense_balances": 1})
        self.assertEqual(after["provenance"]["kind"], "actual")

    def test_oversight(self) -> None:
        from unittest.mock import patch

        h, ws = self.h, self.ws
        o = h.api("GET", f"/api/w/{ws}/oversight")
        self.assertEqual(o["month"], "2026-10")
        self.assertEqual([r["month"] for r in o["reconciliation"]], ["2026-10", "2026-09", "2026-08", "2026-07", "2026-06", "2026-05"])
        self.assertTrue(all(r["status"] == "none" for r in o["reconciliation"]))
        self.assertIsNone(o["unmatched"])
        self.assertEqual(o["exceptions"]["validation"]["issues"], 18)
        self.assertGreaterEqual(o["exceptions"]["anomalies"]["count"], 2)
        self.assertEqual(o["team"][0]["name"], "Root")
        self.assertIn("dataset.import", {x["action"] for x in o["recent"]})
        self.assertEqual(o["documents"]["total"], 0)
        self.assertEqual(o["operations"]["entries_last_month"], 40)
        self.assertEqual(o["operations"]["months_not_started"], 6)
        # prepare October's close with the demo bank statement: the desk shows the run, the match rate and the unmatched items
        with patch("desk_engine.close.service._today", return_value="2026-11-15"):
            run = h.api("POST", f"/api/w/{ws}/close", json={"month": "2026-10"})
            stmt = (DEMO / "HarborLine_Bank_Statement_2026-10.csv").read_bytes()
            h.api("POST", f"/api/w/{ws}/close/{run['id']}/statement", files={"file": ("october.csv", stmt)})
        o = h.api("GET", f"/api/w/{ws}/oversight")
        oct_ = o["reconciliation"][0]
        self.assertEqual((oct_["status"], oct_["statement"], oct_["run_id"]), ("open", True, run["id"]))
        self.assertGreater(oct_["matched"], 0)
        self.assertIsNotNone(o["unmatched"])
        self.assertEqual(o["unmatched"]["month"], "2026-10")
        self.assertEqual(o["operations"]["months_open"], 1)
        self.assertIsNotNone(o["operations"]["avg_days_to_prepare"])
        self.assertTrue(any(x["kind"] in ("accrual", "depreciation", "variance", "bank_fee", "bank_interest", "to_review")
                            for x in o["open_close_items"]), o["open_close_items"])
        self.assertIn("close.prepare", {x["action"] for x in o["recent"]})
