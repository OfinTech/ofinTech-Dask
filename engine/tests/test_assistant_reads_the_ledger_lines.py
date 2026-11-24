"""What the ledger's own lines say, read in code (profit_drivers, account_activity, ledger_entries).

What this guards. On 24 Nov 2026 two models were given fifteen questions on an invented company and, after the
closed questions of the day before had been moved into code, three answers were still only half right. Asked what
caused the one loss-making month, each compared it with an average it had worked out itself (a different one on
each run) and named no account. Asked what zakat had been paid, one said the books held nothing while the entry
was there. Asked for a credit note's amount before VAT, one gave the total with the VAT in it. Each answer is in
the ledger whole: the month against the months before it, account by account; the lines on an account with the
bank line of the same entry; an entry's amount before tax, the tax and the total.

Every figure below is invented for this file: a company that does not exist, with entries numbered by hand. Some
of its accounts are written in Arabic and asked for in English, as the trial's were.
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
from desk_engine.analysis.activity import match_accounts, plain
from desk_engine.llm.base import ChatMessage

from .helpers import Harness

GL_HEAD = ["Date", "Entry", "Type", "Account code", "Account", "Account class", "Debit", "Credit", "Description",
           "Party", "Cost centre tags"]
BANK, DEBTORS, VAT = ("1000", "Bank current account", "Bank"), ("1100", "Trade debtors", "Receivable"), \
    ("2100", "ضريبة القيمة المضافة المستحقة", "Liability")
EQUITY, SALES, RETURNS = ("3000", "Share capital", "Equity"), ("4000", "Sales", "Income"), \
    ("4900", "مردودات المبيعات", "Income")
HAULAGE, RENT, ZAKAT = ("5100", "Haulage", "Cost of sales"), ("6100", "Rent", "Expense"), \
    ("7100", "الزكاة", "Other expense")


def line(day: str, ref: str, kind: str, account: tuple[str, str, str], debit: float, credit: float, text: str,
         party: str = "") -> list[Any]:
    return [day, ref, kind, *account, debit, credit, text, party, ""]


def invoice(day: str, ref: str, net: float, party: str = "Larkspur Fittings") -> list[list[Any]]:
    tax = round(net * 0.15, 2)
    return [line(day, ref, "Invoice", DEBTORS, net + tax, 0, f"Invoice {ref}", party),
            line(day, ref, "Invoice", SALES, 0, net, f"Invoice {ref}", party),
            line(day, ref, "Invoice", VAT, 0, tax, f"VAT on {ref}", party)]


def paid(day: str, ref: str, account: tuple[str, str, str], amount: float, text: str, party: str = "") -> list[list[Any]]:
    return [line(day, ref, "Payment", account, amount, 0, text, party), line(day, ref, "Payment", BANK, 0, amount, text, party)]


def months(plan: tuple[tuple[str, float, float], ...]) -> list[list[Any]]:
    """A sale, a haulage payment and the rent in each month: (month, sale before VAT, haulage)."""
    return [row for m, net, haul in plan
            for row in (*invoice(f"2026-{m}-05", f"INV-{int(m) - 5}", net),
                        *paid(f"2026-{m}-12", f"PAY-{m}A", HAULAGE, haul, "Haulage", "Tern Carriers"),
                        *paid(f"2026-{m}-28", f"PAY-{m}B", RENT, 1000, "Rent", "Quay Lettings"))]


# Four ordinary months and one bad one. September: a sale of 6,000.00, a return of 800.00 and haulage of 9,500.00
# where 2,000.00 is usual, so a loss of 5,300.00 against a usual profit of 7,000.00.
LEDGER = [GL_HEAD,
          line("2026-06-01", "JV-1", "Journal", BANK, 50000, 0, "Capital paid in"),
          line("2026-06-01", "JV-1", "Journal", EQUITY, 0, 50000, "Capital paid in"),
          *months((("06", 10000, 2000), ("07", 10000, 2000), ("08", 10000, 2000), ("09", 6000, 9500),
                   ("10", 10000, 2000))),
          # the credit note, typed as the ledger's own language types it
          line("2026-09-18", "CN-7", "إشعار دائن", RETURNS, 800, 0, "Goods returned", "Larkspur Fittings"),
          line("2026-09-18", "CN-7", "إشعار دائن", VAT, 120, 0, "VAT reversed on CN-7", "Larkspur Fittings"),
          line("2026-09-18", "CN-7", "إشعار دائن", DEBTORS, 0, 920, "Credit note CN-7", "Larkspur Fittings"),
          *paid("2026-10-20", "JV-9", VAT, 3000, "VAT paid for the quarter"),
          *paid("2026-10-15", "JV-10", ZAKAT, 1250, "Zakat for 2025")]


def as_csv(rows: list[list[Any]]) -> bytes:
    buf = io.StringIO()
    csv.writer(buf).writerows(rows)
    return buf.getvalue().encode()


class Base(unittest.TestCase):
    ledger = LEDGER

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

    def call(self, name: str, args: dict[str, Any]) -> Any:
        async def go() -> Any:
            tools = await registry.for_context(self.ctx)
            return await tools[name].handler(self.ctx, args)
        return asyncio.run(go())


class ProfitDrivers(Base):
    def test_the_loss_month_is_held_against_the_months_before_it_and_the_account_is_named(self) -> None:
        out = self.call("profit_drivers", {"month": "2026-09"})
        text, r = out.for_model(), out.data
        self.assertIn("against the average of the 3 months before it (2026-06 to 2026-08)", text)
        self.assertIn("Net profit: -5,300.00 against 7,000.00, lower by 12,300.00.", text)
        self.assertIn("- Cost of sales: 9,500.00 against 2,000.00 (higher by 7,500.00): profit lower by 7,500.00.", text)
        self.assertIn("- Revenue: 5,200.00 against 10,000.00 (lower by 4,800.00): profit lower by 4,800.00.", text)
        self.assertIn("- 5100 Haulage (Cost of sales): 9,500.00 against 2,000.00 (higher by 7,500.00): profit lower by "
                      "7,500.00.", text)
        self.assertEqual([a["account"] for a in r["accounts"]], ["5100 Haulage", "4000 Sales", "4900 مردودات المبيعات"])
        self.assertIn("Months with a loss between 2026-06 and 2026-10: 2026-09 (-5,300.00).", text)
        # a cost that did not move is not listed, and nothing was left for the model to subtract
        self.assertNotIn("Rent", text)

    def test_the_effects_add_up_to_the_change_in_net_profit(self) -> None:
        for args in ({"month": "2026-09"}, {"month": "2026-10", "against": "previous_month"}, {"month": "2026-10", "months": 2}):
            r = self.call("profit_drivers", args).data
            by_line = round(sum(x["effect"] for x in r["lines"] if x["effect"] is not None), 2)
            by_account = round(sum(x["effect"] for x in r["accounts"]), 2)
            self.assertEqual((by_line, by_account), (r["net_profit"]["change"], r["net_profit"]["change"]), args)

    def test_the_baseline_is_said_whichever_one_it_is(self) -> None:
        self.assertIn("against the month before it (2026-09)",
                      self.call("profit_drivers", {"against": "previous_month"}).for_model())
        self.assertIn("against the average of the 2 months before it (2026-08 to 2026-09)",
                      self.call("profit_drivers", {"month": "2026-10", "months": 2}).for_model())
        self.assertIn("against the month before it (2026-06)", self.call("profit_drivers", {"month": "2026-07"}).for_model())

    def test_a_baseline_the_ledger_does_not_hold_is_refused_and_no_figure_is_given(self) -> None:
        first = self.call("profit_drivers", {"month": "2026-06"}).for_model()
        self.assertIn("The ledger starts in 2026-06: it has no month before it to compare 2026-06 with.", first)
        year = self.call("profit_drivers", {"month": "2026-09", "against": "last_year"}).for_model()
        self.assertIn("not the same month a year earlier", year)
        outside = self.call("profit_drivers", {"month": "2025-01"}).for_model()
        self.assertIn("No ledger data for 2025-01", outside)
        for text in (first, year, outside):
            self.assertNotIn("Net profit:", text)


class AccountActivity(Base):
    def test_a_tax_account_named_in_english_is_found_in_arabic_with_what_was_paid_from_the_bank(self) -> None:
        out = self.call("account_activity", {"account": "VAT payable", "from_month": "2026-10", "to_month": "2026-10"})
        text, r = out.for_model(), out.data
        self.assertIn("Account 2100 ضريبة القيمة المضافة المستحقة (Liability), 2026-10,", text)
        self.assertIn("Balance at the start: 5,280.00 credit. Debits 3,000.00, credits 1,500.00. Balance at the end of "
                      "2026-10: 3,780.00 credit.", text)
        self.assertIn("Paid from the bank in the same entry: 3,000.00 of the debits.", text)
        self.assertIn("- 2026-10-20, JV-9, Payment: debit 3,000.00, VAT paid for the quarter; in the same entry: 1000 "
                      "Bank current account credit 3,000.00.", text)
        self.assertEqual([m["key"] for m in r["matches"]], ["2100|ضريبة القيمة المضافة المستحقة"])
        # the sale's own VAT line was not paid from the bank, and reads as the invoice it belongs to
        self.assertIn("- 2026-10-05, INV-5, Invoice, Larkspur Fittings: credit 1,500.00", text)
        self.assertIn("Before tax 10,000.00, tax 1,500.00, total 11,500.00.", text)

    def test_what_was_paid_on_an_expense_is_found_without_months_being_named(self) -> None:
        text = self.call("account_activity", {"account": "zakat"}).for_model()
        self.assertIn("Account 7100 الزكاة (Other expense), 2026-06 to 2026-10,", text)
        self.assertIn("Debits 1,250.00, credits 0.00 in the period (net 1,250.00 debit).", text)
        self.assertIn("Paid from the bank in the same entry: 1,250.00 of the debits.", text)
        self.assertIn("- 2026-10-15, JV-10, Payment: debit 1,250.00, Zakat for 2025;", text)
        self.assertNotIn("Balance at the start", text, "an expense account has no balance to carry")

    def test_by_code_by_part_of_the_name_and_for_one_party(self) -> None:
        for asked in ("5100", "haulage", "5100 Haulage", "HAULAGE "):
            r = self.call("account_activity", {"account": asked}).data
            self.assertEqual([m["label"] for m in r["matches"]], ["5100 Haulage"], asked)
            self.assertEqual((r["accounts"][0]["debit"], r["accounts"][0]["count"]), (17500.0, 5), asked)
        one = self.call("account_activity", {"account": "debtors", "party": "larkspur", "from_month": "2026-09"})
        self.assertIn("lines with larkspur only", one.for_model())
        self.assertEqual((one.data["accounts"][0]["debit"], one.data["accounts"][0]["credit"]), (6900.0, 920.0))
        self.assertIsNone(one.data["accounts"][0]["opening"], "one party's lines do not carry the account's balance")

    def test_an_account_that_is_not_there_gets_the_chart_and_no_figure(self) -> None:
        out = self.call("account_activity", {"account": "goodwill"})
        text = out.for_model()
        self.assertIn("No account in the general ledger “General ledger” matches “goodwill”.", text)
        for label in ("1000 Bank current account", "2100 ضريبة القيمة المضافة المستحقة", "7100 الزكاة"):
            self.assertIn(label, text)
        self.assertNotIn("Debits", text)
        self.assertEqual(out.data["accounts"], [])

    def test_a_long_account_is_given_by_month_and_a_month_outside_the_ledger_is_refused(self) -> None:
        import desk_engine.analysis.activity as activity
        keep = activity.MAX_LINES
        self.addCleanup(setattr, activity, "MAX_LINES", keep)
        activity.MAX_LINES = 5
        text = self.call("account_activity", {"account": "1000"}).for_model()
        self.assertIn("lines, too many to list; by month (ask for one month to see its lines):", text)
        self.assertIn("- 2026-06: debits 50,000.00, credits 3,000.00, balance at the month end 47,000.00 debit", text)
        self.assertNotIn("Paid from the bank", text, "the bank account is not paid from itself")
        self.assertIn("No ledger data for 2027-01: the ledger covers 2026-06 to 2026-10.",
                      self.call("account_activity", {"account": "1000", "from_month": "2027-01"}).for_model())
        self.assertIn("The period runs backwards", self.call(
            "account_activity", {"account": "1000", "from_month": "2026-09", "to_month": "2026-07"}).for_model())

    def test_the_more_specific_meaning_wins(self) -> None:
        accounts = {k: {"code": c, "name": n, "label": f"{c} {n}", "class": cls, "role": ""} for k, (c, n, cls) in {
            "a": ("2000", "الذمم الدائنة", "Payable"), "b": ("2100", "ضريبة القيمة المضافة المستحقة", "Liability"),
            "c": ("4000", "المبيعات", "Income"), "d": ("4900", "مردودات المبيعات", "Income"),
            "e": ("7100", "الزكاة", "Other expense")}.items()}
        for asked, want in (("VAT payable", ["b"]), ("accounts payable", ["a"]), ("sales returns", ["d"]),
                            ("zakat", ["e"]), ("2100", ["b"]), ("المبيعات", ["c"]), ("goodwill", []), ("", [])):
            self.assertEqual(match_accounts(accounts, asked), want, asked)
        # whole words only: "rent" is not found inside "current"
        bank = {"x": {"code": "1000", "name": "Bank current account", "label": "", "class": "Bank", "role": "cash"}}
        self.assertEqual(match_accounts(bank, "rent"), [])
        self.assertEqual(match_accounts(bank, "curr acc"), ["x"])
        self.assertEqual(plain("Stock-in-Hand (Raw)"), "stock in hand raw")


class LedgerEntries(Base):
    def test_a_credit_note_asked_for_in_english_is_found_and_read_before_and_after_tax(self) -> None:
        for search in ("credit notes", "sales returns", "إشعار دائن", "CN-7"):
            out = self.call("ledger_entries", {"search": search, "from_month": "2026-08", "to_month": "2026-10"})
            text = out.for_model()
            self.assertEqual([e["entry"] for e in out.data["entries"]], ["CN-7"], search)
            self.assertIn("- CN-7, 2026-09-18, إشعار دائن, Larkspur Fittings: 4900 مردودات المبيعات debit 800.00; 2100 "
                          "ضريبة القيمة المضافة المستحقة debit 120.00; 1100 Trade debtors credit 920.00. Before tax 800.00, "
                          "tax 120.00, total 920.00.", text)

    def test_an_entry_by_its_number_and_a_tax_payment_has_no_amount_before_tax(self) -> None:
        out = self.call("ledger_entries", {"entry": "jv-9"})
        self.assertIn("- JV-9, 2026-10-20, Payment: 2100 ضريبة القيمة المضافة المستحقة debit 3,000.00; 1000 Bank current "
                      "account credit 3,000.00.", out.for_model())
        self.assertNotIn("Before tax", out.for_model())
        self.assertIsNone(out.data["entries"][0]["amounts"])

    def test_the_words_as_asked_come_before_a_word_for_the_same_thing(self) -> None:
        # "rent" is in every month's rent payment as written; nothing else is pulled in by a related word
        out = self.call("ledger_entries", {"search": "rent", "from_month": "2026-09", "to_month": "2026-10"})
        self.assertEqual([e["entry"] for e in out.data["entries"]], ["PAY-09B", "PAY-10B"])
        self.assertIn("matching “rent” between 2026-09 and 2026-10: 2.", out.for_model())

    def test_nothing_found_says_where_it_looked_and_nothing_asked_is_refused(self) -> None:
        text = self.call("ledger_entries", {"search": "debenture"}).for_model()
        self.assertIn("No entry in the general ledger “General ledger” matches “debenture” between 2026-06 and 2026-10.", text)
        self.assertIn("Entry types in this ledger: Invoice, Journal, Payment, إشعار دائن.", text)
        self.assertIn("Give an entry number or words to search for.", self.call("ledger_entries", {}).for_model())

    def test_they_change_nothing(self) -> None:
        before = self.h.engine.db.one("SELECT COUNT(*) AS n FROM datasets WHERE workspace_id = ?", (self.ws,))["n"]
        for name, args in (("profit_drivers", {}), ("account_activity", {"account": "1000"}),
                           ("ledger_entries", {"search": "rent"})):
            self.call(name, args)
        self.assertEqual(self.h.engine.db.one("SELECT COUNT(*) AS n FROM datasets WHERE workspace_id = ?",
                                              (self.ws,))["n"], before)


class AnomaliesSayHowManyMonths(Base):
    # the bad month last, so that four months stand before it
    ledger = [GL_HEAD, *months((("06", 10000, 2000), ("07", 10000, 2000), ("08", 10000, 2000), ("09", 10000, 2000),
                                ("10", 10000, 9500)))]

    def test_an_average_over_four_months_is_not_called_six(self) -> None:
        text = self.call("ledger_anomalies", {"month": "2026-10"}).for_model()
        self.assertIn("- Unusual amount, 5100 Haulage (Cost of sales): 9,500.00 this month against an average of "
                      "2,000.00 over the previous 4 months (+375%).", text)
        self.assertNotIn("six months", text)


class Offered(Base):
    def test_they_are_core_tools_and_the_rules_send_the_model_to_them(self) -> None:
        tools = asyncio.run(registry.for_context(self.ctx))
        names = {"profit_drivers", "account_activity", "ledger_entries"}
        for name in names:
            self.assertIn(tools[name].category, CORE)
            self.assertFalse(tools[name].needs_approval())
        groups = select_groups([ChatMessage("user", "hello")], tools, has_datasets=True)
        self.assertLessEqual(names, {t.name for t in choose_tools(tools, groups)})
        rules = " ".join(RULES.split())
        for phrase in ("use profit_drivers and give its baseline as it states it: never average months yourself",
                       "For what was posted or paid on an account use account_activity",
                       "for a credit note, an entry or an amount before tax use ledger_entries",
                       "say the books do not show something only after one of these found nothing",
                       "Never do arithmetic yourself"):
            self.assertIn(phrase, rules)

    def test_a_workspace_with_no_ledger_is_not_offered_them(self) -> None:
        h = Harness()
        self.addCleanup(h.close)
        ws = h.setup_workspace(with_model=False)
        workspace = dict(h.engine.db.one("SELECT * FROM workspaces WHERE id = ?", (ws,)))
        user = h.engine.db.one("SELECT user_id FROM members WHERE workspace_id = ?", (ws,))["user_id"]
        tools = asyncio.run(registry.for_context(ToolContext(h.engine, workspace, user)))
        self.assertFalse({"profit_drivers", "account_activity", "ledger_entries"} & set(tools))


class TheAgentReportsThem(unittest.TestCase):
    def test_figures_copied_from_profit_drivers_are_sourced_and_an_average_of_the_models_own_is_not(self) -> None:
        h = Harness()
        self.addCleanup(h.close)
        ws = h.setup_workspace()
        up = h.api("POST", f"/api/w/{ws}/imports", files={"file": ("gl.csv", as_csv(LEDGER))})
        h.api("POST", f"/api/w/{ws}/imports/{up['upload_id']}/commit",
              json={"name": "General ledger", "sheet": None, "header_row": 0})
        t = h.api("POST", f"/api/w/{ws}/threads", json={})["id"]
        h.llm.script = [[("profit_drivers", {"month": "2026-09"})],
                        "September 2026 made a loss of -5,300.00 against a usual 7,000.00. The account behind it is "
                        "5100 Haulage: 9,500.00 against 2,000.00."]
        h.api("POST", f"/api/w/{ws}/threads/{t}/messages", json={"text": "Which month made a loss, and why?"})
        payload = h.wait_run(t)["messages"][-1]["payload"]
        self.assertEqual(payload["unverified_figures"], [])
        h.llm.script = [[("profit_drivers", {"month": "2026-09"})],
                        "Haulage was 9,500.00 against a four-month average of 3,875.00."]
        h.api("POST", f"/api/w/{ws}/threads/{t}/messages", json={"text": "Against what average?"})
        payload = h.wait_run(t)["messages"][-1]["payload"]
        self.assertEqual(payload["unverified_figures"], ["3,875.00"])


if __name__ == "__main__":
    unittest.main()
