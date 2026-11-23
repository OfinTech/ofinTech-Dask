"""Closed questions answered in code, and a turn that ends (bank_reconciliation, calc_what_if, the figure check's
view of a calculator, the rule on zakat and tax).

What this guards. On 23 and 24 Nov 2026 two models were given the same fifteen questions on an invented company.
Both got a what-if on net profit wrong, in opposite directions. Asked to hold a bank statement against the books,
one took the statement's highest balance for its closing balance and one stopped after sixteen steps with no
answer. One, asked for the zakat due, chose a base and a rate itself and asked the calculator for the product
fifteen times. None of those is a question a model should be answering by itself: the two files hold the whole
reconciliation, the direction of a change follows from what the change is, and a rate nobody gave has no source.

Every figure below is invented for this file: a company that does not exist, with entries numbered by hand.
"""

from __future__ import annotations

import asyncio
import csv
import io
import unittest
from typing import Any

from desk_engine.agent.context import CORE, choose_tools, select_groups
from desk_engine.agent.figures import classify, report, sound_calculations
from desk_engine.agent.finance_tools import what_if
from desk_engine.agent.prompts import RULES
from desk_engine.agent.tools import ToolContext, registry
from desk_engine.analysis.reconcile import detect_columns, reconcile, statement_balances, statement_lines
from desk_engine.errors import Invalid
from desk_engine.llm.base import ChatMessage

from .helpers import Harness

GL_HEAD = ["Date", "Entry", "Type", "Account code", "Account", "Account class", "Debit", "Credit", "Description",
           "Party", "Cost centre tags"]
BANK, SALES, RENT, SUPPLIES, EQUITY = (("1000", "Bank current account", "Bank"), ("4000", "Sales", "Income"),
                                       ("6100", "Rent", "Expense"), ("6200", "Supplies", "Expense"),
                                       ("3000", "Share capital", "Equity"))


def entry(day: str, ref: str, debit: tuple[str, str, str], credit: tuple[str, str, str], amount: float, text: str,
          party: str = "") -> list[list[Any]]:
    return [[day, ref, "Journal", *debit, amount, 0, text, party, ""], [day, ref, "Journal", *credit, 0, amount, text, party, ""]]


# September opens the bank at 54,000.00; October moves it to 52,450.25
LEDGER = [GL_HEAD,
          *entry("2026-09-01", "JV-1", BANK, EQUITY, 50000.00, "Capital paid in"),
          *entry("2026-09-10", "RCT-1", BANK, SALES, 4000.00, "Receipt", "Larkspur Fittings"),
          *entry("2026-10-03", "RCT-2", BANK, SALES, 2500.00, "Receipt", "Larkspur Fittings"),
          *entry("2026-10-05", "PAY-1", RENT, BANK, 800.00, "October rent", "Quay Lettings"),
          *entry("2026-10-08", "PAY-4", SUPPLIES, BANK, 1000.00, "Packing materials", "Tern Paper"),
          *entry("2026-10-12", "RCT-3", BANK, SALES, 1200.50, "Receipt", "Mossbank Stores"),
          *entry("2026-10-20", "PAY-2", SUPPLIES, BANK, 3100.25, "Shelving", "Tern Paper"),
          *entry("2026-10-30", "PAY-3", SUPPLIES, BANK, 950.00, "Cheque 0412", "Harrow Timber"),
          *entry("2026-10-31", "RCT-4", BANK, SALES, 600.00, "Takings banked on the 31st", "Mossbank Stores")]

# The bank's October: PAY-2 two days late, PAY-4 seventeen days late, a charge and interest the books do not have;
# the cheque PAY-3 and the deposit RCT-4 have not reached it. The highest balance is 56,900.50, not the closing one.
STATEMENT = [["Date", "Description", "Money out", "Money in", "Balance"],
             ["2026-10-01", "Balance brought forward", "", "", "54000.00"],
             ["2026-10-03", "Credit Larkspur Fittings", "", "2500.00", "56500.00"],
             ["2026-10-05", "Standing order Quay Lettings", "800.00", "", "55700.00"],
             ["2026-10-12", "Credit Mossbank Stores", "", "1200.50", "56900.50"],
             ["2026-10-22", "Card Tern Paper", "3100.25", "", "53800.25"],
             ["2026-10-25", "Transfer Tern Paper", "1000.00", "", "52800.25"],
             ["2026-10-28", "Account charge", "12.50", "", "52787.75"],
             ["2026-10-29", "Interest", "", "75.00", "52862.75"]]


def as_csv(rows: list[list[Any]]) -> bytes:
    buf = io.StringIO()
    csv.writer(buf).writerows(rows)
    return buf.getvalue().encode()


class Base(unittest.TestCase):
    def setUp(self) -> None:
        self.h = Harness()
        self.addCleanup(self.h.close)
        self.ws = self.h.setup_workspace(with_model=False)
        self.load("gl.csv", "General ledger", LEDGER)
        workspace = dict(self.h.engine.db.one("SELECT * FROM workspaces WHERE id = ?", (self.ws,)))
        user = self.h.engine.db.one("SELECT user_id FROM members WHERE workspace_id = ?", (self.ws,))["user_id"]
        self.ctx = ToolContext(self.h.engine, workspace, user)

    def load(self, file: str, name: str, rows: list[list[Any]]) -> dict[str, Any]:
        up = self.h.api("POST", f"/api/w/{self.ws}/imports", files={"file": (file, as_csv(rows))})
        return self.h.api("POST", f"/api/w/{self.ws}/imports/{up['upload_id']}/commit",
                          json={"name": name, "sheet": None, "header_row": 0})

    def call(self, name: str, args: dict[str, Any]) -> Any:
        async def go() -> Any:
            tools = await registry.for_context(self.ctx)
            return await tools[name].handler(self.ctx, args)
        return asyncio.run(go())


class BankReconciliation(Base):
    def test_the_closing_balance_is_the_last_row_and_the_difference_is_explained_to_the_cent(self) -> None:
        self.load("bank.csv", "Bank statement October", STATEMENT)
        out = self.call("bank_reconciliation", {})
        text, r = out.for_model(), out.data
        self.assertEqual((r["statement_closing"], r["book_closing"], r["difference"]), (52862.75, 52450.25, 412.50))
        self.assertIn("Statement closing balance at 2026-10-29: 52,862.75", text)
        self.assertNotIn("56,900.50", text, "the highest balance on the statement is not its closing balance")
        self.assertIn("Ledger balance at 2026-10-31: 52,450.25", text)
        self.assertIn("Difference, statement less ledger: 412.50", text)
        self.assertIn("Opening balances: statement 54,000.00, ledger 54,000.00 (they agree)", text)
        self.assertEqual([x["entry"] for x in r["book_only"]], ["PAY-3", "RCT-4"])
        self.assertIn("- PAY-3, 2026-10-30, paid 950.00", text)
        self.assertIn("- RCT-4, 2026-10-31, received 600.00", text)
        self.assertEqual([(x["row"], x["in"], x["out"]) for x in r["bank_only"]], [(7, 0.0, 12.5), (8, 75.0, 0.0)])
        self.assertIn("paid 12.50, Account charge", text)
        self.assertIn("received 75.00, Interest", text)
        self.assertEqual(r["matched_by"], {"same date": 3, "within 3 days": 1, "amount only": 1})
        self.assertIn("PAY-4 (2026-10-08) with row 6 (2026-10-25), paid 1,000.00", text)
        # 950.00 the bank has not paid, less 600.00 it has not received, plus 75.00 and less 12.50 only it has
        self.assertEqual((r["explained"], r["unexplained"]), (412.50, 0.0))
        self.assertIn("explain 412.50 of the difference; unexplained: 0.00", text)
        self.assertIn("Nothing was posted or changed.", text)
        self.assertEqual(out.blocks[0]["rows"][0][:3], ["Ledger only", "2026-10-30", "PAY-3"])

    def test_it_changes_nothing(self) -> None:
        self.load("bank.csv", "Bank statement October", STATEMENT)
        before = self.h.engine.db.scalar("SELECT COUNT(*) FROM datasets WHERE workspace_id = ?", (self.ws,))
        audit = self.h.engine.db.scalar("SELECT COUNT(*) FROM audit_log")
        self.call("bank_reconciliation", {"statement": "Bank statement October", "month": "2026-10"})
        self.assertEqual(self.h.engine.db.scalar("SELECT COUNT(*) FROM datasets WHERE workspace_id = ?", (self.ws,)), before)
        self.assertEqual(self.h.engine.db.scalar("SELECT COUNT(*) FROM audit_log"), audit)

    def test_a_statement_printed_newest_first_closes_on_the_same_balance(self) -> None:
        self.load("bank.csv", "Bank statement October", [STATEMENT[0], *reversed(STATEMENT[1:])])
        r = self.call("bank_reconciliation", {}).data
        self.assertEqual((r["statement_opening"], r["statement_closing"], r["unexplained"]), (54000.0, 52862.75, 0.0))

    def test_one_signed_amount_column_and_no_balance(self) -> None:
        rows = [["Fecha", "Concepto", "Importe"]] + [
            [d, text, (f"-{out}" if out else money_in)] for d, text, out, money_in, _ in STATEMENT[2:]]
        self.load("banco.csv", "Extracto octubre", rows)
        out = self.call("bank_reconciliation", {})
        self.assertIsNone(out.data["statement_closing"])
        self.assertIn("no balance column, so balances are not compared", out.for_model())
        self.assertEqual([x["entry"] for x in out.data["book_only"]], ["PAY-3", "RCT-4"])
        self.assertEqual(len(out.data["bank_only"]), 2)

    def test_an_opening_balance_that_differs_is_said_and_not_spread_over_the_month(self) -> None:
        shifted = [STATEMENT[0]] + [[d, t, o, i, f"{float(b) + 100:.2f}"] for d, t, o, i, b in STATEMENT[1:]]
        self.load("bank.csv", "Bank statement October", shifted)
        out = self.call("bank_reconciliation", {})
        self.assertEqual((out.data["opening_difference"], out.data["difference"], out.data["unexplained"]),
                         (100.0, 512.50, 0.0))
        self.assertIn("they differ by 100.00: items from before 2026-10 are still open", out.for_model())

    def test_which_statement_and_what_is_not_a_statement(self) -> None:
        self.assertNotIn("bank_reconciliation", asyncio.run(registry.for_context(self.ctx)),
                         "a ledger alone has nothing to be held against")
        self.load("notes.csv", "Price list", [["Item", "Price"], ["Bracket", "4.20"]])
        self.assertIn("No dataset in this workspace reads as a bank statement", self.call("bank_reconciliation", {}).for_model())
        self.load("bank.csv", "Bank statement October", STATEMENT)
        self.assertEqual(self.call("bank_reconciliation", {}).data["statement"]["name"], "Bank statement October")
        self.load("bank2.csv", "Savings statement", STATEMENT)
        self.assertIn("More than one dataset reads as a bank statement", self.call("bank_reconciliation", {}).for_model())
        self.assertIn("is a general ledger, not a bank statement",
                      self.call("bank_reconciliation", {"statement": "General ledger"}).for_model())
        self.assertIn("has no rows in 2026-07: it covers 2026-10 to 2026-10",
                      self.call("bank_reconciliation", {"statement": "Savings statement", "month": "2026-07"}).for_model())
        self.assertIn("No bank or cash account in the ledger matches “petty”. The ledger's bank and cash accounts are: "
                      "1000 Bank current account",
                      self.call("bank_reconciliation", {"statement": "Savings statement", "account": "petty"}).for_model())

    def test_it_is_offered_with_the_core_tools_and_the_rules_send_the_model_to_it(self) -> None:
        self.load("bank.csv", "Bank statement October", STATEMENT)
        tools = asyncio.run(registry.for_context(self.ctx))
        for name in ("bank_reconciliation", "calc_what_if"):
            self.assertIn(tools[name].category, CORE)
        groups = select_groups([ChatMessage("user", "hello")], tools, has_datasets=True)
        offered = {t.name for t in choose_tools(tools, groups)}
        self.assertLessEqual({"bank_reconciliation", "calc_what_if"}, offered)
        rules = " ".join(RULES.split())
        self.assertIn("compare the two with bank_reconciliation and report what it lists", rules)
        self.assertIn("use calc_what_if", rules)


class Matching(unittest.TestCase):
    """analysis/reconcile.py by itself."""

    def test_two_payments_of_the_same_amount_are_ticked_one_for_one(self) -> None:
        book = [{"date": "2026-10-04", "entry": "PAY-7", "in": 0.0, "out": 250.0},
                {"date": "2026-10-04", "entry": "PAY-8", "in": 0.0, "out": 250.0},
                {"date": "2026-10-09", "entry": "PAY-9", "in": 0.0, "out": 250.0}]
        bank = [{"row": 1, "date": "2026-10-04", "in": 0.0, "out": 250.0, "balance": None, "text": ""},
                {"row": 2, "date": "2026-10-06", "in": 0.0, "out": 250.0, "balance": None, "text": ""}]
        r = reconcile(bank, book, statement_opening=None, statement_closing=None, book_opening=0.0, book_closing=-750.0)
        self.assertEqual(r["matched"], 2)
        self.assertEqual([x["entry"] for x in r["book_only"]], ["PAY-9"], "the one furthest from any statement row")
        self.assertEqual(r["bank_only"], [])
        self.assertEqual((r["explained"], r["difference"], r["unexplained"]), (250.0, None, None))

    def test_money_in_never_ticks_off_money_out(self) -> None:
        r = reconcile([{"row": 1, "date": "2026-10-04", "in": 90.0, "out": 0.0, "balance": 90.0, "text": "refund"}],
                      [{"date": "2026-10-04", "entry": "PAY-1", "in": 0.0, "out": 90.0}],
                      statement_opening=0.0, statement_closing=90.0, book_opening=0.0, book_closing=-90.0)
        self.assertEqual((r["matched"], len(r["book_only"]), len(r["bank_only"])), (0, 1, 1))
        self.assertEqual((r["difference"], r["explained"], r["unexplained"]), (180.0, 180.0, 0.0))

    def test_columns_are_found_by_the_names_banks_use_in_four_languages(self) -> None:
        def cols(*names: str) -> list[dict[str, str]]:
            return [{"name": n, "type": "text"} for n in names]
        self.assertEqual(detect_columns(cols("Date", "Details", "Debit", "Credit", "Balance")),
                         {"date": 0, "money_in": 3, "money_out": 2, "balance": 4, "description": 1})
        self.assertEqual(detect_columns(cols("التاريخ", "البيان", "مدين", "دائن", "الرصيد")),
                         {"date": 0, "money_in": 3, "money_out": 2, "balance": 4, "description": 1})
        self.assertEqual(detect_columns(cols("Fecha", "Concepto", "Importe", "Saldo")),
                         {"date": 0, "amount": 2, "balance": 3, "description": 1})
        self.assertEqual(detect_columns(cols("Date", "Libellé", "Débit", "Crédit", "Solde"))["money_out"], 2)
        self.assertEqual(detect_columns(cols("When", "What", "Out", "In"), {"date": "When"})["date"], 0)
        with self.assertRaises(Invalid) as none:
            detect_columns(cols("Item", "Price"))
        self.assertIn("Its columns are: Item, Price", none.exception.message)
        with self.assertRaises(Invalid):
            detect_columns(cols("Date", "Amount"), {"balance": "Running total"})

    def test_dates_and_amounts_as_spreadsheets_write_them(self) -> None:
        lines, unread = statement_lines([["03/10/2026", "x", "1,250.50", "", "(40.00)"], ["soon", "y", "", "5", "1"]],
                                        {"date": 0, "description": 1, "money_out": 2, "money_in": 3, "balance": 4})
        self.assertEqual(unread, 1)
        self.assertEqual((lines[0]["date"], lines[0]["out"], lines[0]["balance"]), ("2026-10-03", 1250.5, -40.0))
        self.assertEqual(statement_balances(lines, "2026-10"), (1210.5, -40.0, "2026-10-03"))


class WhatIf(Base):
    def test_a_missing_cost_lowers_profit_and_a_removed_duplicate_raises_it(self) -> None:
        out = self.call("calc_what_if", {"profit_label": "Net profit, October 2026", "profit": 41250.40, "revenue": 180000.00,
                                         "changes": [{"label": "Warehouse rent not booked", "amount": 9000, "kind": "cost_added"},
                                                     {"label": "Duplicate courier bill", "amount": 1310.25, "kind": "cost_removed"}]})
        text = out.for_model()
        self.assertEqual(out.data["profit_after"], 33560.65)        # 41,250.40 - 9,000.00 + 1,310.25
        self.assertIn("- 9,000.00  Warehouse rent not booked (cost_added: lowers profit)", text)
        self.assertIn("+ 1,310.25  Duplicate courier bill (cost_removed: raises profit)", text)
        self.assertIn("Net profit, October 2026 after the changes: 33,560.65 (a change of -7,689.75)", text)
        self.assertIn("Margin: 22.9% before, 18.6% after.", text)
        self.assertNotEqual(out.data["profit_after"], 41250.40 - 9000 - 1310.25, "both subtracted: 24 Nov 2026")
        self.assertNotEqual(out.data["profit_after"], 41250.40 + 9000 - 1310.25, "both reversed: 23 Nov 2026")

    def test_income_moves_revenue_too_and_a_sign_typed_by_the_model_is_ignored(self) -> None:
        r = what_if(1000, [{"label": "Sale not invoiced", "amount": -400, "kind": "income_added"},
                           {"label": "Sale to reverse", "amount": 150, "kind": "income_removed"}], revenue=5000)
        self.assertEqual((float(r["profit_after"]), float(r["revenue_after"])), (1250.0, 5250.0))
        self.assertEqual(what_if(-200, [{"amount": 50, "kind": "cost_added"}])["profit_after"], -250)
        self.assertIn("Not computed", self.call("calc_what_if", {"profit_label": "p", "profit": 1, "changes": [
            {"label": "x", "amount": 1, "kind": "makes_it_better"}]}).for_model())


BALANCE_SHEET = "Line | 2026-10\nEquity (assets less liabilities) | 3,400,000.00\nBank and cash | 52,450.25"
ANOMALIES = "Anomalies in 2026-10: 4 found; amounts under 1,200.00 are ignored."


class WhatWentIntoTheCalculator(unittest.TestCase):
    def test_a_rate_nobody_gave_makes_the_result_unsupported(self) -> None:
        # equity read from the balance sheet, times a 2.5% the model supplied: "Zakat due 85,000.00", 24 Nov 2026
        calc = ({"steps": [{"label": "Zakat due", "operation": "multiply", "values": [3400000.00, 0.025]}]},
                "Zakat due: multiply(3,400,000.00, 0.03) = 85,000.00")
        r = report("The zakat due is 85,000.00.", [BALANCE_SHEET], ["Work out the zakat we owe for 2026."], [], calcs=[calc])
        self.assertEqual(r["unverified_figures"], ["85,000.00"])
        self.assertEqual(r["unsourced_inputs"], ["0.025"])

    def test_the_same_sum_on_the_users_own_rate_is_their_arithmetic(self) -> None:
        calc = ({"steps": [{"label": "2.5% of equity", "operation": "multiply", "values": [3400000.00, 0.025]}]},
                "2.5% of equity: multiply(3,400,000.00, 0.03) = 85,000.00")
        r = report("2.5% of equity is 85,000.00.", [BALANCE_SHEET], ["What is 2.5% of our equity?"], [], calcs=[calc])
        self.assertEqual(r["unverified_figures"], [])
        self.assertNotIn("unsourced_inputs", r)

    def test_a_result_feeds_the_next_calculation_and_plain_constants_need_no_source(self) -> None:
        first = ({"steps": [{"label": "Total", "operation": "add", "values": [52450.25, 3400000]}]},
                 "Total: add(52,450.25, 3,400,000.00) = 3,452,450.25")
        second = ({"steps": [{"label": "A twelfth", "operation": "divide", "values": [3452450.25, 12]},
                             {"label": "Share", "operation": "multiply", "values": [0.5, 100]}]},
                  "A twelfth: divide(3,452,450.25, 12.00) = 287,704.19")
        sound, orphans = sound_calculations([first, second], [BALANCE_SHEET])
        self.assertEqual(len(sound), 1, "0.5 came from nowhere, so the second call is not evidence")
        self.assertEqual(orphans, ["0.5"])
        sound, orphans = sound_calculations([first, (second[0] | {"steps": second[0]["steps"][:1]}, second[1])], [BALANCE_SHEET])
        self.assertEqual((len(sound), orphans), (2, []))

    def test_a_financial_calculator_with_parameters_a_person_chooses_is_not_held_to_this(self) -> None:
        from desk_engine.agent.figures import is_calculator
        self.assertTrue(is_calculator("calc_arithmetic") and is_calculator("calc_what_if"))
        self.assertFalse(is_calculator("calc_npv_irr") or is_calculator("financial_statements"))

    def test_a_count_in_the_answer_is_traced_to_a_bare_number_in_a_tool_result(self) -> None:
        # "Check before relying on: 4 items", on an answer that listed the four anomalies the tool had found
        figs = {f.raw: f.provenance for f in classify("The report lists 4 items.", [ANOMALIES], [], [])}
        self.assertEqual(figs, {"4 items": "tool"})
        self.assertEqual({f.raw: f.provenance for f in classify("The report lists 7 items.", [ANOMALIES], [], [])},
                         {"7 items": "unsupported"})
        # a bare 4 supports a count, never a ratio or a percentage
        self.assertEqual({f.raw: f.provenance for f in classify("Cover is 4× and growth 4%.", [ANOMALIES], [], [])},
                         {"4×": "unsupported", "4%": "unsupported"})


class StoredWithTheAnswer(unittest.TestCase):
    def test_the_agent_loop_keeps_calculator_calls_apart_and_stores_what_had_no_source(self) -> None:
        h = Harness()
        self.addCleanup(h.close)
        ws = h.setup_workspace()
        t = h.api("POST", f"/api/w/{ws}/threads", json={})["id"]
        h.llm.script = [[("calc_arithmetic", {"steps": [{"label": "Tax due", "operation": "multiply",
                                                         "values": [640000, 0.2]}]})],
                        "The tax due on 640,000 is 128,000.00."]
        h.api("POST", f"/api/w/{ws}/threads/{t}/messages", json={"text": "Profit was 640,000. What tax do we owe?"})
        payload = h.wait_run(t)["messages"][-1]["payload"]
        self.assertEqual(payload["unverified_figures"], ["128,000.00"])
        self.assertEqual(payload["unsourced_inputs"], ["0.2"])
        self.assertEqual(payload["user_figures"], ["640,000"])
        # the next turn of the same conversation, with a sound calculation of its own: the 0.2 of the turn before
        # is not shown under it again, and its result is still no evidence
        h.llm.script = [[("calc_arithmetic", {"steps": [{"label": "Half", "operation": "divide", "values": [640000, 2]}]})],
                        "Half of 640,000 is 320,000.00; the tax figure before was 128,000.00."]
        h.api("POST", f"/api/w/{ws}/threads/{t}/messages", json={"text": "And half of that profit?"})
        payload = h.wait_run(t)["messages"][-1]["payload"]
        self.assertNotIn("unsourced_inputs", payload)
        self.assertEqual(payload["unverified_figures"], ["128,000.00"])


class Rules(unittest.TestCase):
    def test_no_tax_or_zakat_due_on_a_base_or_rate_of_the_models_choosing(self) -> None:
        rules = " ".join(RULES.split())
        for phrase in ("never say whether a return or filing is correct",
                       "Never work out the zakat, income tax or other tax due for a period on a base or a rate of your "
                       "own choosing",
                       "when the user gives both the base and the rate",
                       "every figure you give a calculator is one you read in a tool result or in the user's messages"):
            self.assertIn(phrase, rules)


if __name__ == "__main__":
    unittest.main()
