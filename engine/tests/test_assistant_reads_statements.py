"""The assistant reads the statements pages instead of rebuilding them (agent/analysis_tools.py, calc_arithmetic).

What this guards. On 22 Nov 2026 the assistant was asked, on the demo company, for October's net profit, for what
customers owed for more than 30 and 90 days, and for an average of six months. It had no tool for the Statements,
Receivables, Payables or Anomalies pages and no plain calculator, so it queried the ledger lines and did the sums
itself: a net profit that left out interest, an "over 30 days" total larger than everything customers owed, and a
correct formula with a wrong result after the equals sign. Each of those figures was on a page the user could open.

So the assertions here are of one kind: what a tool tells the model is, to the cent, what the page's own route
returns for the same month. They are computed from the demo ledger at test time, not typed in.
"""

from __future__ import annotations

import asyncio
import unittest
from decimal import Decimal
from typing import Any

from desk_engine.agent.context import CORE, choose_tools, select_groups
from desk_engine.agent.finance_tools import arithmetic
from desk_engine.agent.prompts import RULES
from desk_engine.agent.tools import ToolContext, registry
from desk_engine.llm.base import ChatMessage

from .helpers import Harness

NEW = ("financial_statements", "ageing_report", "ledger_anomalies", "calc_arithmetic")


def money(v: float) -> str:
    return f"{v:,.2f}"


class AssistantReadsStatementsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.h = Harness()
        self.ws = self.h.setup_workspace(with_model=False)
        self.h.api("POST", f"/api/w/{self.ws}/demo")
        workspace = dict(self.h.engine.db.one("SELECT * FROM workspaces WHERE id = ?", (self.ws,)))
        user = self.h.engine.db.one("SELECT user_id FROM members WHERE workspace_id = ?", (self.ws,))["user_id"]
        self.ctx = ToolContext(self.h.engine, workspace, user)

    def tearDown(self) -> None:
        self.h.close()

    def call(self, name: str, args: dict[str, Any]) -> Any:
        async def go() -> Any:
            tools = await registry.for_context(self.ctx)
            return await tools[name].handler(self.ctx, args)
        return asyncio.run(go())

    def page(self, path: str) -> dict[str, Any]:
        return self.h.api("GET", f"/api/w/{self.ws}/overview/{path}")

    # -- profit and loss -------------------------------------------------------------------------------------------
    def test_net_profit_is_the_pages_net_profit_and_counts_interest(self) -> None:
        page = self.page("statements?periods=3")
        lines = {ln["measure"]: ln["values"] for ln in page["profit_and_loss"]["lines"]}
        text = self.call("financial_statements", {"statement": "profit_and_loss"}).for_model()
        self.assertEqual(len(page["columns"]), 3)
        for i, month in enumerate(page["columns"]):
            self.assertIn(month, text)
            self.assertIn(money(lines["net_profit"][i]), text, f"net profit of {month} as the page has it")
            self.assertIn(money(lines["revenue"][i]), text)
        # the mistake of 22 Nov: revenue less cost of sales and operating costs, interest forgotten
        self.assertGreater(lines["interest"][-1], 0, "the demo month should carry interest, or this proves nothing")
        self.assertNotEqual(lines["operating_profit"][-1], lines["net_profit"][-1])
        self.assertIn(f"Net profit | {money(lines['net_profit'][0])}", text)

    def test_total_expenses_is_revenue_less_net_profit_as_on_the_overview(self) -> None:
        overview = self.h.api("GET", f"/api/w/{self.ws}/overview")
        expenses = next(c for c in overview["headline"] if c["id"] == "expenses")["value"] \
            if isinstance(overview.get("headline"), list) else overview["headline"]["expenses"]["value"]
        text = self.call("financial_statements", {"statement": "profit_and_loss", "months": 1}).for_model()
        self.assertIn(f"Total expenses (all cost lines) | {money(expenses)}", text)

    def test_margins_are_rounded_as_the_page_rounds_them(self) -> None:
        page = self.page("statements?periods=12")
        text = self.call("financial_statements", {"statement": "profit_and_loss", "months": 12}).for_model()
        row = next(ln for ln in text.splitlines() if ln.startswith("Net profit, % of revenue"))
        shown = [c.strip() for c in row.split("|")[1:1 + len(page["columns"])]]
        want = [f"{(Decimal(str(m)) * 100).quantize(Decimal('0.1'), rounding='ROUND_HALF_UP')}%"
                for m in page["profit_and_loss"]["margins"]["net_profit"]]
        self.assertEqual(shown, want)

    def test_balance_sheet_and_cash_flow_carry_the_pages_figures(self) -> None:
        page = self.page("statements?periods=1")
        bs = self.call("financial_statements", {"statement": "balance_sheet", "months": 1}).for_model()
        for key in ("total_assets", "total_liabilities", "equity", "working_capital"):
            self.assertIn(money(page["balance_sheet"]["totals"][key][0]), bs, key)
        cf = self.call("financial_statements", {"statement": "cash_flow", "months": 1}).for_model()
        self.assertIn(f"Closing cash | {money(page['cash_flow'][0]['closing'])}", cf)
        self.assertIn(f"Operating cash flow | {money(page['cash_flow'][0]['operating'])}", cf)

    def test_a_month_the_ledger_does_not_cover_gets_no_figures(self) -> None:
        for name, args in (("financial_statements", {"statement": "profit_and_loss", "month": "2019-03"}),
                           ("ageing_report", {"kind": "receivables", "month": "2019-03"}),
                           ("ledger_anomalies", {"month": "2019-03"})):
            out = self.call(name, args)
            self.assertIn("No ledger data for 2019-03", out.text, name)
            self.assertEqual(out.data, {"month": "2019-03", "available": False}, name)

    # -- ageing ----------------------------------------------------------------------------------------------------
    def test_ageing_is_the_receivables_page_to_the_cent(self) -> None:
        page = self.page("parties/receivables")
        text = self.call("ageing_report", {"kind": "receivables"}).for_model()
        t = page["totals"]
        self.assertIn(f"over 30 days {money(t['overdue_30'])}", text)
        self.assertIn(f"over 90 days {money(t['buckets'][3])}", text)
        self.assertLessEqual(t["overdue_30"], t["closing"], "what is old cannot exceed what is owed")
        self.assertGreater(t["buckets"][3], 0, "the demo should have something over 90 days, or this proves nothing")
        for row in page["rows"]:
            line = next(ln for ln in text.splitlines() if ln.startswith(row["party"] + " |"))
            self.assertIn(money(row["closing"]), line)
            if row["buckets"][3]:
                self.assertIn(money(row["buckets"][3]), line, f"{row['party']}: over 90 days")

    def test_payables_names_the_largest_supplier_first_and_calls_them_suppliers(self) -> None:
        page = self.page("parties/payables")
        text = self.call("ageing_report", {"kind": "payables"}).for_model()
        self.assertTrue(text.startswith("Owed to suppliers at "))
        self.assertNotIn("customers", text.lower())
        table = text.splitlines()[3:]
        self.assertTrue(table[0].startswith(page["rows"][0]["party"] + " |"), "largest balance first, as on the page")
        self.assertIn(money(page["totals"]["closing"]), text)

    def test_a_payment_matched_to_no_invoice_is_named_with_its_party(self) -> None:
        # On 23 Nov 2026 the assistant was asked whether any customer had paid in advance. The page knew (it keeps
        # an "unapplied" amount per party); the tool dropped that column, and the honest answer was "the data does
        # not show it". A ledger of its own here: one invoice of 1,000.00, then 1,500.00 received.
        from desk_engine.connectors.accounting import GL_COLUMNS
        from desk_engine.data import store

        h = Harness()
        try:
            ws = h.setup_workspace(with_model=False)
            user = h.engine.db.one("SELECT user_id FROM members WHERE workspace_id = ?", (ws,))["user_id"]
            rows = [["2026-10-05", "INV-1", "Invoice", "1100", "Receivables", "Receivable", 1000.0, 0.0, "", "Acme Ltd", None],
                    ["2026-10-05", "INV-1", "Invoice", "4000", "Sales", "Income", 0.0, 1000.0, "", "Acme Ltd", None],
                    ["2026-10-20", "RCT-1", "Receipt", "1000", "Bank", "Bank", 1500.0, 0.0, "", "Acme Ltd", None],
                    ["2026-10-20", "RCT-1", "Receipt", "1100", "Receivables", "Receivable", 0.0, 1500.0, "", "Acme Ltd", None],
                    ["2026-10-22", "INV-2", "Invoice", "1100", "Receivables", "Receivable", 300.0, 0.0, "", "Birch & Co", None],
                    ["2026-10-22", "INV-2", "Invoice", "4000", "Sales", "Income", 0.0, 300.0, "", "Birch & Co", None]]
            store.create_dataset(h.engine, ws, name="General ledger", source="excel", columns=GL_COLUMNS, rows=rows,
                                 source_ref={"kind": "general_ledger"}, created_by=user)
            workspace = dict(h.engine.db.one("SELECT * FROM workspaces WHERE id = ?", (ws,)))
            ctx = ToolContext(h.engine, workspace, user)
            page = h.api("GET", f"/api/w/{ws}/overview/parties/receivables")
            acme = next(r for r in page["rows"] if r["party"] == "Acme Ltd")
            self.assertEqual((acme["unapplied"], acme["closing"]), (500.0, -500.0), "what the page says")

            async def go() -> Any:
                tools = await registry.for_context(ctx)
                return await tools["ageing_report"].handler(ctx, {"kind": "receivables"})
            text = asyncio.run(go()).for_model()
            self.assertIn("Unapplied payments", text.splitlines()[1], "a column of its own")
            self.assertIn("received from a party and matched to no document; the closing balance is net of them): "
                          "Acme Ltd 500.00.", text)
            self.assertNotIn("Birch & Co 0.00", text.splitlines()[0], "a party with nothing unapplied is not listed")
        finally:
            h.close()

    def test_no_unapplied_payment_is_said_and_not_left_to_be_guessed(self) -> None:
        page = self.page("parties/payables")
        self.assertEqual(page["totals"]["unapplied"], 0, "the demo's suppliers have none, or this proves nothing")
        self.assertIn(" No unapplied payments.", self.call("ageing_report", {"kind": "payables"}).text)

    # -- anomalies -------------------------------------------------------------------------------------------------
    def test_anomalies_are_the_pages_anomalies(self) -> None:
        page = self.page("anomalies")
        out = self.call("ledger_anomalies", {})
        self.assertGreater(page["count"], 0, "the demo month should have anomalies, or this proves nothing")
        self.assertIn(f"{page['count']} found", out.text)
        for item in page["items"]:
            self.assertIn(item["account"], out.text)
            self.assertIn(money(item.get("value") if item.get("value") is not None else item["average"]), out.text)

    # -- arithmetic ------------------------------------------------------------------------------------------------
    def test_arithmetic_is_exact(self) -> None:
        self.assertEqual(arithmetic("add", [0.1, 0.2]), Decimal("0.3"))
        self.assertEqual(arithmetic("subtract", [197947.81, 87228.30, 93037.92, 947.92]), Decimal("16733.67"))
        self.assertEqual(arithmetic("average", [0, 11200, 4200, 8600, 7300, 4200]).quantize(Decimal("0.01")), Decimal("5916.67"))
        self.assertEqual(arithmetic("percent_change", [110, 100]), Decimal("10"))
        self.assertEqual(arithmetic("percent_change", [-50, -100]), Decimal("50"))
        self.assertEqual(arithmetic("multiply", [1.5, 4]), Decimal("6.0"))
        for bad in (("divide", [1, 0]), ("divide", [1, 2, 3]), ("percent_of", [1]), ("add", []), ("power", [2, 3])):
            with self.assertRaises(ValueError, msg=str(bad)):
                arithmetic(*bad)

    def test_the_calculator_says_what_it_computed_and_survives_a_bad_step(self) -> None:
        out = self.call("calc_arithmetic", {"steps": [
            {"label": "Net profit", "operation": "subtract", "values": [197947.81, 87228.30, 93037.92, 947.92]},
            {"label": "Nonsense", "operation": "divide", "values": [1, 0]},
            {"label": "Net margin", "operation": "percent_of", "values": [16733.67, 197947.81]}]})
        lines = out.text.splitlines()
        self.assertEqual(lines[0], "Net profit: subtract(197,947.81, 87,228.30, 93,037.92, 947.92) = 16,733.67")
        self.assertEqual(lines[1], "Nonsense: not computed (division by zero).")
        self.assertEqual(lines[2], "Net margin: percent_of(16,733.67, 197,947.81) = 8.45% (8.5% to one decimal)")
        self.assertEqual(out.data, [["Net profit", 16733.67], ["Net margin", 8.45]])

    # -- offered without the right words -----------------------------------------------------------------------------
    def test_the_tools_are_offered_whatever_words_the_user_chose(self) -> None:
        async def tools() -> dict[str, Any]:
            return await registry.for_context(self.ctx)
        available = asyncio.run(tools())
        for text in ("hello", "كم ندين للموردين؟", "what is 2 + 2"):
            groups = select_groups([ChatMessage("user", text)], available, has_datasets=False)
            offered = {t.name for t in choose_tools(available, groups)}
            for name in NEW:
                self.assertIn(name, offered, f"{name} for “{text}”")
        for name in NEW:
            self.assertIn(registry.get_static(name).category, CORE)
            self.assertEqual(registry.get_static(name).effect, "read")
            self.assertFalse(registry.get_static(name).needs_approval())

    def test_a_workspace_without_a_ledger_is_not_offered_pages_it_does_not_have(self) -> None:
        h = Harness()
        try:
            ws = h.setup_workspace(with_model=False)
            workspace = dict(h.engine.db.one("SELECT * FROM workspaces WHERE id = ?", (ws,)))
            user = h.engine.db.one("SELECT user_id FROM members WHERE workspace_id = ?", (ws,))["user_id"]
            offered = asyncio.run(registry.for_context(ToolContext(h.engine, workspace, user)))
            self.assertIn("calc_arithmetic", offered, "a calculator needs no ledger")
            for name in NEW[:3]:
                self.assertNotIn(name, offered)
        finally:
            h.close()

    def test_in_a_conversation_the_model_is_sent_the_tools_and_gets_the_pages_figures(self) -> None:
        h = Harness(script=[[("financial_statements", {"statement": "profit_and_loss", "months": 1})],
                            [("calc_arithmetic", {"steps": [{"label": "Net margin", "operation": "percent_of",
                                                             "values": [1, 4]}]})], "Done."])
        try:
            ws = h.setup_workspace()
            h.api("POST", f"/api/w/{ws}/demo")
            page = h.api("GET", f"/api/w/{ws}/overview/statements?periods=1")
            net = next(ln for ln in page["profit_and_loss"]["lines"] if ln["measure"] == "net_profit")["values"][0]
            t = h.api("POST", f"/api/w/{ws}/threads", json={})
            h.api("POST", f"/api/w/{ws}/threads/{t['id']}/messages", json={"text": "hello"})
            tools = [m for m in h.wait_run(t["id"])["messages"] if m["role"] == "tool"]
            self.assertIn(f"Net profit | {money(net)}", tools[0]["content"])
            self.assertIn("Net margin: percent_of(1.00, 4.00) = 25.00%", tools[1]["content"])
            sent = {x["function"]["name"] for x in h.llm.calls[0]["tools"]}
            self.assertLessEqual(set(NEW), sent, "sent with the first request, for a message with none of the trigger words")
        finally:
            h.close()

    def test_the_rules_name_the_tools(self) -> None:
        for name in ("financial_statements", "ageing_report", "calc_arithmetic"):
            self.assertIn(name, RULES)
        self.assertIn("Never do arithmetic yourself", RULES)


if __name__ == "__main__":
    unittest.main()
