"""Calculator edge cases found by the AI agent audit (reports/ai-agent-audit, E1 and E2), with answers that are known
independently of the code under test.

- Several IRRs: -1,000 / 3,600 / -4,310 / 1,716 has IRRs of exactly 10%, 20% and 30%. With x = 1 + r,
  NPV * x^3 = -1000x^3 + 3600x^2 - 4310x + 1716 = -1000 (x - 1.1)(x - 1.2)(x - 1.3).
- XIRR above 1,000%: -100 then +200 31 days later has XIRR = 2^(365/31) - 1 exactly.
- Growth from a negative base, a rate written as 15 for 15%, salvage above cost, a month outside the ledger.
"""

from __future__ import annotations

import unittest
from datetime import date

from desk_engine.agent import finance_tools as f
from desk_engine.agent.tools import ToolContext, registry

from .helpers import Harness


class Pure(unittest.TestCase):
    def test_three_irrs_are_all_found(self) -> None:
        info = f.irr_analysis([-1000, 3600, -4310, 1716])
        self.assertEqual(info["status"], "multiple")
        self.assertIsNone(info["irr"])
        for got, want in zip(info["irrs"], (0.10, 0.20, 0.30), strict=True):
            self.assertAlmostEqual(got, want, places=9)

    def test_conventional_flows_keep_their_single_irr(self) -> None:
        cfs = [-100000, 25000, 30000, 35000, 40000]
        info = f.irr_analysis(cfs)
        self.assertEqual(info["status"], "unique")
        self.assertEqual(info["irr"], f.irr(cfs))          # exactly the value the engine always gave
        self.assertAlmostEqual(f.npv(info["irr"], cfs), 0, places=6)

    def test_long_series_near_minus_100_percent_does_not_break(self) -> None:
        cfs = [-100] + [1] * 300                           # 300 payments of 1 for 100: a small positive IRR
        r = f.irr_analysis(cfs)["irr"]
        self.assertIsNotNone(r)
        self.assertAlmostEqual(f.npv(r, cfs), 0, places=6)

    def test_two_sign_changes_without_any_root(self) -> None:
        info = f.irr_analysis([-100, 50, -200])            # NPV is negative at every rate above -100%
        self.assertEqual(info["status"], "none")

    def test_xirr_above_1000_percent(self) -> None:
        got = f.xirr([-100, 200], [date(2026, 1, 1), date(2026, 2, 1)])
        self.assertAlmostEqual(got / (2 ** (365 / 31) - 1), 1, places=9)

    def test_xirr_known_value_unchanged(self) -> None:
        cfs, ds = [-10000, 2750, 4250, 3250, 2750], [date(2008, 1, 1), date(2008, 3, 1), date(2008, 10, 30),
                                                     date(2009, 2, 15), date(2009, 4, 1)]
        self.assertAlmostEqual(f.xirr(cfs, ds), 0.373362535, places=5)   # Microsoft's documented XIRR example


class Tools(unittest.TestCase):
    def setUp(self) -> None:
        self.h = Harness()
        self.addCleanup(self.h.close)
        ws = self.h.setup_workspace(with_model=False)
        self.ws = ws
        self.ctx = ToolContext(self.h.engine, self.h.engine.db.one("SELECT * FROM workspaces WHERE id = ?", (ws,)), "u")

    def run_tool(self, name: str, args: dict):
        return self.h.client.portal.call(registry.get_static(name).run, self.ctx, args)

    def test_npv_irr_says_the_irr_is_not_unique(self) -> None:
        o = self.run_tool("calc_npv_irr", {"cashflows": [-1000, 3600, -4310, 1716], "discount_rate": 0.1})
        self.assertIn("not unique", o.text)
        for shown in ("10.00%", "20.00%", "30.00%"):
            self.assertIn(shown, o.text)
        self.assertIsNone(o.data["irr"])
        self.assertEqual(o.data["irr_status"], "multiple")
        self.assertIn("npv", o.data)                         # NPV at the required rate is still given

    def test_xnpv_xirr_reports_high_rates_and_several_roots(self) -> None:
        o = self.run_tool("calc_xnpv_xirr", {"cashflows": [-100, 200], "dates": ["2026-01-01", "2026-02-01"]})
        self.assertNotIn("n/a", o.text)
        self.assertAlmostEqual(o.data["xirr"] / (2 ** (365 / 31) - 1), 1, places=6)
        o = self.run_tool("calc_xnpv_xirr", {"cashflows": [-1000, 3600, -4310, 1716],
                                             "dates": ["2025-01-01", "2026-01-01", "2027-01-01", "2028-01-01"]})  # whole years, no 29 Feb
        self.assertEqual(o.data["xirr_status"], "multiple")
        self.assertIn("not unique", o.text)

    def test_rates_must_be_decimal_fractions(self) -> None:
        cases = [("calc_sales_tax", {"amount": 100, "rate": 15, "amount_includes_tax": False}),
                 ("calc_loan_schedule", {"principal": 1000, "annual_rate": 6.5, "periods": 12}),
                 ("calc_npv_irr", {"cashflows": [-100, 60, 60], "discount_rate": 8}),
                 ("calc_xnpv_xirr", {"cashflows": [-100, 110], "dates": ["2026-01-01", "2027-01-01"], "discount_rate": 10})]
        for name, args in cases:
            o = self.run_tool(name, args)
            self.assertIn("decimal fraction", o.text, name)
            self.assertIsNone(o.data, name)                  # nothing computed from the misread rate
        self.assertIn("cannot be negative", self.run_tool("calc_sales_tax", {"amount": 100, "rate": -0.1,
                                                                             "amount_includes_tax": False}).text)
        ok = self.run_tool("calc_sales_tax", {"amount": 100, "rate": 0.15, "amount_includes_tax": False})
        self.assertEqual(ok.data, {"net": 100.0, "tax": 15.0, "gross": 115.0})

    def test_depreciation_inputs_are_checked(self) -> None:
        for args, words in (({"cost": 1000, "salvage_value": 1500, "useful_life_years": 5}, "higher than the cost"),
                            ({"cost": 0, "useful_life_years": 5}, "greater than zero"),
                            ({"cost": 1000, "salvage_value": -1, "useful_life_years": 5}, "cannot be negative")):
            o = self.run_tool("calc_depreciation", args)
            self.assertIn(words, o.text)
            self.assertIsNone(o.data)
        o = self.run_tool("calc_depreciation", {"cost": 1000, "salvage_value": 1000, "useful_life_years": 2})
        self.assertEqual([row[1] for row in o.data], [0.0, 0.0])      # equal: nothing to depreciate, still valid

    def test_growth_from_a_negative_or_zero_base(self) -> None:
        o = self.run_tool("calc_growth", {"values": [-100, -50]})
        self.assertAlmostEqual(o.data["changes"][0], 0.5)              # a smaller loss is an improvement
        self.assertIn("negative", o.text)
        self.assertIn("CAGR: not meaningful", o.text)
        o = self.run_tool("calc_growth", {"values": [0, 50, 100]})
        self.assertIsNone(o.data["changes"][0])
        self.assertAlmostEqual(o.data["changes"][1], 1.0)
        o = self.run_tool("calc_growth", {"values": [100, 121], "years": 2})
        self.assertAlmostEqual(o.data["cagr"], 0.1)                    # positive series unchanged

    def test_ratios_leave_the_call_arguments_alone(self) -> None:
        args = {"revenue": 1000, "cogs": 400, "receivables": 100, "days_in_period": 90}
        sent = dict(args)
        o = self.run_tool("calc_financial_ratios", sent)
        self.assertEqual(sent, args)
        dso = next(r["value"] for r in o.data if r["key"] == "dso")
        self.assertAlmostEqual(dso, 100 / 1000 * 90)

    def test_a_month_outside_the_ledger_gets_no_figures(self) -> None:
        from .test_financial_analysis import ledger_lines
        conn = self.h.api("POST", f"/api/w/{self.ws}/connections/custom", json={"label": "ERP", "mode": "push"})
        r = self.h.client.post("/api/ingest/v1/accounting/journal-lines", json={"lines": ledger_lines()},
                               headers={"authorization": f"Bearer {conn['ingest_key']}"})
        self.assertEqual(r.status_code, 200, r.text)
        for month in ("2027-06", "2024-01"):
            o = self.run_tool("financial_kpis", {"month": month})
            self.assertTrue(o.text.startswith(f"No ledger data for {month}"), o.text)
            self.assertEqual(o.data, {"month": month, "available": False})
            o = self.run_tool("kpi_detail", {"kpi": "dso", "month": month})
            self.assertTrue(o.text.startswith(f"No ledger data for {month}"), o.text)
        o = self.run_tool("financial_kpis", {"month": "2026-03"})
        self.assertIn("month 2026-03", o.text)
        self.assertEqual(o.data["month"], "2026-03")


if __name__ == "__main__":
    unittest.main()
