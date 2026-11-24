"""How long the cash lasts, and what a change does to the month it runs out in (cash_runway).

What this guards. The KPI scorecard gives "months of cash". The question a founder asks is one step further: which
month, and what happens to it if we hire two people or raise money. Left to a model that is a balance, an average
and a division done in its head, on figures it has to pick out of two statements. It is worked out in code
instead, on the same two measures the KPI uses, so that the Overview page, the assistant and the scorecard cannot
disagree; and it says, wherever it is shown, that it is the current rate carried forward and not a forecast.

Every figure below is invented for this file: two companies that do not exist. The first raises 300,000.00 and
spends more than it earns (burn of 30,000.00, 30,000.00, 20,000.00, 30,000.00 and 40,000.00 from June to October),
ending October with 150,000.00. The second earns 10,000.00 a month more than it spends.
"""

from __future__ import annotations

import asyncio
import csv
import io
import unittest
from typing import Any

from desk_engine.agent.context import CORE, choose_tools, select_groups
from desk_engine.agent.prompts import RULES
from desk_engine.agent.tools import ToolContext, registry
from desk_engine.analysis.runway import add_months, position
from desk_engine.llm.base import ChatMessage

from .helpers import Harness

GL_HEAD = ["Date", "Entry", "Type", "Account code", "Account", "Account class", "Debit", "Credit", "Description",
           "Party", "Cost centre tags"]
BANK, EQUITY = ("1000", "Bank current account", "Bank"), ("3000", "Share capital", "Equity")
SALES, RENT = ("4000", "Sales", "Income"), ("6100", "Studio rent and staff", "Expense")


def entry(day: str, ref: str, debit: tuple[str, str, str], credit: tuple[str, str, str], amount: float,
          text: str) -> list[list[Any]]:
    return [[day, ref, "Journal", *debit, amount, 0, text, "", ""], [day, ref, "Journal", *credit, 0, amount, text, "", ""]]


def company(sales: float, costs: tuple[float, ...], capital: float) -> list[list[Any]]:
    """Capital paid in on the first day, then cash sales and cash costs in each month from June 2026."""
    rows = [GL_HEAD, *entry("2026-06-01", "JV-1", BANK, EQUITY, capital, "Capital paid in")]
    for n, cost in enumerate(costs):
        m = f"2026-{6 + n:02d}"
        rows += entry(f"{m}-10", f"REC-{n + 1}", BANK, SALES, sales, "Sales received")
        rows += entry(f"{m}-25", f"PAY-{n + 1}", RENT, BANK, cost, "Rent and staff paid")
    return rows


BURNING = company(10000, (40000, 40000, 30000, 40000, 50000), 300000)
EARNING = company(50000, (40000, 40000, 40000, 40000, 40000), 100000)


def as_csv(rows: list[list[Any]]) -> bytes:
    buf = io.StringIO()
    csv.writer(buf).writerows(rows)
    return buf.getvalue().encode()


class Base(unittest.TestCase):
    ledger = BURNING

    def setUp(self) -> None:
        self.h = Harness()
        self.addCleanup(self.h.close)
        self.ws = self.h.setup_workspace(with_model=False)
        up = self.h.api("POST", f"/api/w/{self.ws}/imports", files={"file": ("gl.csv", as_csv(self.ledger))})
        self.h.api("POST", f"/api/w/{self.ws}/imports/{up['upload_id']}/commit",
                   json={"name": "General ledger", "sheet": None, "header_row": 0})
        workspace = dict(self.h.engine.db.one("SELECT * FROM workspaces WHERE id = ?", (self.ws,)))
        user = self.h.engine.db.one("SELECT user_id FROM members WHERE workspace_id = ?", (self.ws,))["user_id"]
        self.ctx = ToolContext(self.h.engine, workspace, user)

    def call(self, args: dict[str, Any]) -> Any:
        async def go() -> Any:
            tools = await registry.for_context(self.ctx)
            return await tools["cash_runway"].handler(self.ctx, args)
        return asyncio.run(go())


class Burning(Base):
    def test_cash_the_average_burn_the_months_left_and_the_month_it_runs_out_in(self) -> None:
        out = self.call({})
        text, r = out.for_model(), out.data
        self.assertEqual((r["cash"], r["burn"], r["months_left"], r["runs_out"]), (150000.0, 30000.0, 5.0, "2027-03"))
        self.assertEqual(r["window"], ["2026-08", "2026-09", "2026-10"])
        self.assertIn("averaged over the 3 months 2026-08 to 2026-10.", text)
        self.assertIn("At the current rate: burn 30,000.00 a month, cash 150,000.00, which lasts 5.0 months and runs "
                      "out in 2027-03.", text)
        self.assertIn("Burn by month (negative is cash coming in): 2026-06 30,000.00; 2026-07 30,000.00; 2026-08 "
                      "20,000.00; 2026-09 30,000.00; 2026-10 40,000.00.", text)
        self.assertIn("not a forecast", text)
        # the capital paid in is financing: it is cash, and it is not burn
        self.assertEqual(r["by_month"][0], {"month": "2026-06", "burn": 30000.0, "cash": 270000.0})

    def test_it_is_the_number_the_scorecard_and_the_overview_page_give(self) -> None:
        o = self.h.api("GET", f"/api/w/{self.ws}/overview")
        self.assertEqual(o["kpis"]["cash_runway"]["value"], 5.0)
        self.assertEqual((o["runway"]["months_left"], o["runway"]["runs_out"], o["runway"]["burn"]), (5.0, "2027-03", 30000.0))
        self.assertEqual(o["runway"]["cash"], o["headline"]["cash"]["value"])

    def test_a_hire_money_raised_and_both_together(self) -> None:
        hire = self.call({"changes": [{"kind": "cost_added", "amount": 20000, "label": "two engineers"}]})
        self.assertIn("With a cost of 20,000.00 a month added (two engineers): burn 50,000.00 a month, cash 150,000.00, "
                      "which lasts 3.0 months and runs out in 2027-01.", hire.for_model())
        self.assertIn("That is 2.0 months sooner.", hire.for_model())
        raised = self.call({"changes": [{"kind": "cash_in", "amount": 150000}]}).data["scenario"]
        self.assertEqual((raised["cash"], raised["burn"], raised["months_left"], raised["runs_out"], raised["months_change"]),
                         (300000.0, 30000.0, 10.0, "2027-08", 5.0))
        both = self.call({"changes": [{"kind": "cost_added", "amount": 20000}, {"kind": "cash_in", "amount": 150000}]})
        self.assertIn("With a cost of 20,000.00 a month added; 150,000.00 received once: burn 50,000.00 a month, cash "
                      "300,000.00, which lasts 6.0 months and runs out in 2027-04.", both.for_model())
        self.assertIn("That is 1.0 months later.", both.for_model())
        # the figures as they stand are still given first, unchanged
        self.assertIn("At the current rate: burn 30,000.00 a month, cash 150,000.00", both.for_model())

    def test_a_cut_that_ends_the_burn_has_no_month_to_give(self) -> None:
        out = self.call({"changes": [{"kind": "cost_removed", "amount": 20000}, {"kind": "income_added", "amount": 15000}]})
        s = out.data["scenario"]
        self.assertEqual((s["burning"], s["burn"], s["months_left"], s["runs_out"], s["months_change"]), (False, -5000.0, None, None, None))
        self.assertIn("not burning cash: 5,000.00 a month comes in on average", out.for_model())
        self.assertNotIn("sooner", out.for_model())

    def test_another_window_another_month_and_a_ledger_shorter_than_the_window(self) -> None:
        one = self.call({"months": 1}).data
        self.assertEqual((one["burn"], one["months_left"], one["runs_out"], one["window"]), (40000.0, 3.75, "2027-02", ["2026-10"]))
        august = self.call({"month": "2026-08"}).data
        self.assertEqual((august["cash"], august["burn"], august["months_left"], august["runs_out"]),
                         (220000.0, 26666.67, 8.25, "2027-05"))
        long = self.call({"months": 12})
        self.assertEqual(len(long.data["window"]), 5)
        self.assertIn("The ledger starts in 2026-06, so the average is over 5 and not 12.", long.for_model())
        self.assertIn("No ledger data for 2025-01", self.call({"month": "2025-01"}).for_model())

    def test_a_change_that_cannot_be_read_is_refused_and_no_figure_is_given(self) -> None:
        for changes, said in (([{"kind": "hire", "amount": 5}], "A change is cost_added"),
                              ([{"kind": "cost_added", "amount": -5}], "amount is positive"),
                              ([{"kind": "cost_added"}], "Each change needs an amount."),
                              ([{"kind": "cash_in", "amount": 1}] * 13, "At most 12 changes")):
            text = self.call({"changes": changes}).for_model()
            self.assertIn(said, text)
            self.assertNotIn("burn", text.lower().replace("burning", ""))


class NotBurning(Base):
    ledger = EARNING

    def test_a_company_that_earns_its_keep_gets_no_run_out_month(self) -> None:
        out = self.call({})
        r = out.data
        self.assertEqual((r["burning"], r["burn"], r["cash"], r["months_left"], r["runs_out"]), (False, -10000.0, 150000.0, None, None))
        self.assertIn("At the current rate: not burning cash: 10,000.00 a month comes in on average, so there is no "
                      "month in which the cash of 150,000.00 runs out at this rate.", out.for_model())
        o = self.h.api("GET", f"/api/w/{self.ws}/overview")
        self.assertIsNone(o["kpis"]["cash_runway"]["value"])
        self.assertFalse(o["runway"]["burning"])

    def test_a_new_cost_can_start_the_burn(self) -> None:
        out = self.call({"changes": [{"kind": "cost_added", "amount": 25000}]})
        s = out.data["scenario"]
        self.assertEqual((s["burn"], s["months_left"], s["runs_out"], s["months_change"]), (15000.0, 10.0, "2027-08", None))
        self.assertNotIn("sooner", out.for_model())


class Months(unittest.TestCase):
    def test_the_month_the_balance_reaches_zero_in(self) -> None:
        self.assertEqual([add_months("2026-10", n) for n in (0, 2, 3, 14)], ["2026-10", "2026-12", "2027-01", "2027-12"])
        for cash, burn, months, month in ((72000, 30000, 2.4, "2027-01"), (60000, 30000, 2.0, "2026-12"),
                                          (1, 30000, 0.0, "2026-11"), (0, 30000, 0.0, "2026-10"), (-500, 30000, 0.0, "2026-10")):
            p = position(cash, burn, "2026-10")
            self.assertEqual((p["months_left"], p["runs_out"]), (months, month), (cash, burn))
        self.assertEqual(position(100, 0, "2026-10")["burning"], False)


class Offered(Base):
    def test_it_is_a_core_tool_and_the_rules_send_the_model_to_it(self) -> None:
        tools = asyncio.run(registry.for_context(self.ctx))
        self.assertIn(tools["cash_runway"].category, CORE)
        self.assertFalse(tools["cash_runway"].needs_approval())
        groups = select_groups([ChatMessage("user", "hello")], tools, has_datasets=True)
        self.assertIn("cash_runway", {t.name for t in choose_tools(tools, groups)})
        rules = " ".join(RULES.split())
        for phrase in ("use cash_runway with the change as the user gave it: never divide cash by burn yourself",
                       "say it is the current rate, not a forecast", "Never do arithmetic yourself"):
            self.assertIn(phrase, rules)


class TheAgentReportsIt(unittest.TestCase):
    def test_figures_copied_from_it_are_sourced(self) -> None:
        h = Harness()
        self.addCleanup(h.close)
        ws = h.setup_workspace()
        up = h.api("POST", f"/api/w/{ws}/imports", files={"file": ("gl.csv", as_csv(BURNING))})
        h.api("POST", f"/api/w/{ws}/imports/{up['upload_id']}/commit",
              json={"name": "General ledger", "sheet": None, "header_row": 0})
        t = h.api("POST", f"/api/w/{ws}/threads", json={})["id"]
        h.llm.script = [[("cash_runway", {"changes": [{"kind": "cost_added", "amount": 20000}]})],
                        "At the current rate the cash of 150,000.00 lasts 5.0 months, to 2027-03. With 20,000.00 a month "
                        "more it is 50,000.00 a month and runs out in 2027-01."]
        h.api("POST", f"/api/w/{ws}/threads/{t}/messages", json={"text": "Can we afford two hires at 20,000 a month?"})
        self.assertEqual(h.wait_run(t)["messages"][-1]["payload"]["unverified_figures"], [])
        h.llm.script = [[("cash_runway", {})], "The burn is 30,000.00 a month, so about 41,250.00 would be safer."]
        h.api("POST", f"/api/w/{ws}/threads/{t}/messages", json={"text": "And a safe level?"})
        self.assertEqual(h.wait_run(t)["messages"][-1]["payload"]["unverified_figures"], ["41,250.00"])


if __name__ == "__main__":
    unittest.main()
