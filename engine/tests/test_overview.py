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
