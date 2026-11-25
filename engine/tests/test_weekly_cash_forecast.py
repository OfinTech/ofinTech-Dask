"""Thirteen weeks of cash by the direct method (weekly_cash_forecast): every week worked out by hand.

What this guards. A lender's 13-week cash forecast is built from what is owed, what is owing and what is paid every
month, and each figure has to trace to an entry. Left to a model it is a table of sums nobody can check. It is worked
out in code (analysis/weekly_cash.py), and this file holds it to a company small enough to do on paper.

The company and every figure are invented for this file. It starts in February 2026 with 200,000.00 of capital and
the forecast starts from 31 October 2026, a Saturday, so week 1 is 1 to 7 November and week 13 ends on 30 January.

Customers
- Northgate: three invoices each paid 40 days after its date, and one closed by a credit note 15 days after
  (which is not a payment). Open: 12,000.00 of 10 Oct (expected 19 Nov, week 3) and 8,000.00 of 25 Oct (4 Dec, week 5).
- Marlow: one invoice paid, so no history: terms of 30 days. Open: 4,000.00 of 10 Sep (due 10 Oct, already past:
  week 1) and 6,000.00 of 20 Oct (19 Nov, week 3).
- Quay: open 9,000.00 of 30 Oct (29 Nov, week 5).
- Pier Nine: paid 2,000.00 on 5 Oct ahead of an invoice of 5,000.00 on 15 Oct, so 3,000.00 is open (14 Nov, week 2).
Receivables in the ledger: 42,000.00. Receipts by week: 4,000 | 3,000 | 18,000 | 0 | 17,000 | 0 ...

Suppliers
- Tide: three bills each paid after 20 days. Open: 7,000.00 of 22 Oct (11 Nov, week 2).
- Grey: no history. Open: 2,500.00 of 12 Oct (11 Nov, week 2).
Payables in the ledger: 9,500.00, all in week 2.

Paid straight from the bank
- Rent 3,000.00 on the 1st, June to October: carried to 1 Nov (week 1), 1 Dec (week 5), 1 Jan (week 9).
- Salaries on the 25th: 20,000.00 from June to September and 22,000.00 in October: carried at 22,000.00 to
  25 Nov (week 4), 25 Dec (week 8), 25 Jan (week 13).
- Corporation tax 6,000.00 on 20 Feb, 20 May and 20 Aug: every quarter, so 20 Nov (week 3); 20 Feb is past week 13.
- Marketing 1,000.00, 5,000.00 and 2,000.00 in August to October: every month, too far apart to carry.
- Equipment 12,000.00 once in September, and one cash sale of 1,500.00 in October: no pattern.

Cash at 31 October: 248,500.00 received less 167,000.00 paid = 81,500.00.
Balance at the end of each week: 82,500 | 76,000 | 88,000 | 66,000 | 80,000 | 80,000 | 80,000 | 58,000 | 55,000 |
55,000 | 55,000 | 55,000 | 33,000. And 81,500 + 42,000 - 9,500 - 81,000 = 33,000.
"""

from __future__ import annotations

import asyncio
import unittest
from datetime import date
from typing import Any

from desk_engine.agent.context import CORE, choose_tools, select_groups
from desk_engine.agent.prompts import RULES
from desk_engine.agent.tools import ToolContext, registry
from desk_engine.analysis.ledger import Ledger
from desk_engine.analysis.roles import account_key
from desk_engine.analysis.weekly_cash import _shift, _week, habit, weekly_cash
from desk_engine.errors import Invalid
from desk_engine.llm.base import ChatMessage

from .helpers import Harness
from .test_cash_runway import GL_HEAD, as_csv

BANK, AR, AP = ("1000", "Bank current account", "Bank"), ("1100", "Trade receivables", "Receivable"), ("2000", "Trade payables", "Payable")
EQUITY, SALES, MATERIALS = ("3000", "Share capital", "Equity"), ("4000", "Sales", "Income"), ("5000", "Materials", "Cost of sales")
RENT, SALARIES, TAX = ("6100", "Office rent", "Expense"), ("6200", "Salaries", "Expense"), ("6900", "Corporation tax", "Expense")
MARKETING, EQUIPMENT = ("6300", "Marketing", "Expense"), ("1500", "Office equipment", "Asset")


def entry(day: str, ref: str, debit: tuple[str, str, str], credit: tuple[str, str, str], amount: float, text: str,
          party: str = "") -> list[list[Any]]:
    return [[day, ref, "Journal", *debit, amount, 0, text, party, ""], [day, ref, "Journal", *credit, 0, amount, text, party, ""]]


def invoice(day: str, ref: str, party: str, amount: float) -> list[list[Any]]:
    return entry(day, ref, AR, SALES, amount, "Invoice", party)


def receipt(day: str, ref: str, party: str, amount: float) -> list[list[Any]]:
    return entry(day, ref, BANK, AR, amount, "Received", party)


def bill(day: str, ref: str, party: str, amount: float) -> list[list[Any]]:
    return entry(day, ref, MATERIALS, AP, amount, "Bill", party)


def paid(day: str, ref: str, party: str, amount: float) -> list[list[Any]]:
    return entry(day, ref, AP, BANK, amount, "Paid", party)


def company() -> list[list[Any]]:
    rows = [GL_HEAD, *entry("2026-02-01", "JV-1", BANK, EQUITY, 200000, "Capital paid in")]
    for n, (raised, settled, amount) in enumerate((("2026-06-01", "2026-07-11", 10000), ("2026-07-01", "2026-08-10", 10000),
                                                   ("2026-08-01", "2026-09-10", 20000)), 1):
        rows += invoice(raised, f"INV-{n}", "Northgate", amount) + receipt(settled, f"REC-{n}", "Northgate", amount)
    rows += invoice("2026-08-15", "INV-4", "Marlow", 5000) + receipt("2026-09-01", "REC-4", "Marlow", 5000)
    rows += invoice("2026-09-10", "INV-5", "Marlow", 4000)
    rows += invoice("2026-09-05", "INV-6", "Northgate", 3000) + entry("2026-09-20", "CN-1", SALES, AR, 3000, "Credit note", "Northgate")
    rows += invoice("2026-10-10", "INV-7", "Northgate", 12000) + invoice("2026-10-25", "INV-8", "Northgate", 8000)
    rows += invoice("2026-10-20", "INV-9", "Marlow", 6000) + invoice("2026-10-30", "INV-10", "Quay", 9000)
    rows += receipt("2026-10-05", "REC-5", "Pier Nine", 2000) + invoice("2026-10-15", "INV-11", "Pier Nine", 5000)
    for n, m in enumerate(("06", "07", "08"), 1):
        rows += bill(f"2026-{m}-05", f"BILL-{n}", "Tide", 4000) + paid(f"2026-{m}-25", f"PAY-{n}", "Tide", 4000)
    rows += bill("2026-10-22", "BILL-6", "Tide", 7000) + bill("2026-10-12", "BILL-7", "Grey", 2500)
    for m, salaries in (("06", 20000), ("07", 20000), ("08", 20000), ("09", 20000), ("10", 22000)):
        rows += entry(f"2026-{m}-01", f"RENT-{m}", RENT, BANK, 3000, "Rent", "Harbour Estates")
        rows += entry(f"2026-{m}-25", f"SAL-{m}", SALARIES, BANK, salaries, "Salaries")
    for m in ("02", "05", "08"):
        rows += entry(f"2026-{m}-20", f"TAX-{m}", TAX, BANK, 6000, "Corporation tax instalment")
    for m, amount in (("08", 1000), ("09", 5000), ("10", 2000)):
        rows += entry(f"2026-{m}-18", f"MKT-{m}", MARKETING, BANK, amount, "Campaign")
    rows += entry("2026-09-15", "EQ-1", EQUIPMENT, BANK, 12000, "Laptops")
    rows += entry("2026-10-03", "CS-1", BANK, SALES, 1500, "Cash sale")
    return rows


def lines(rows: list[list[Any]]) -> list[dict[str, Any]]:
    keys = ["date", "entry", "type", "account_code", "account", "account_class", "debit", "credit", "description", "party"]
    return [dict(zip(keys, r[:10], strict=True)) | {"row": n} for n, r in enumerate(rows[1:], 2)]


CLOSING = [82500.0, 76000.0, 88000.0, 66000.0, 80000.0, 80000.0, 80000.0, 58000.0, 55000.0, 55000.0, 55000.0, 55000.0, 33000.0]
RECEIPTS = [4000.0, 3000.0, 18000.0, 0.0, 17000.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
BILLS = [0.0, 9500.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
STANDING = [3000.0, 0.0, 6000.0, 22000.0, 3000.0, 0.0, 0.0, 22000.0, 3000.0, 0.0, 0.0, 0.0, 22000.0]


class ByHand(unittest.TestCase):
    def setUp(self) -> None:
        self.led = Ledger(lines(company()), {})
        self.r = weekly_cash(self.led, "2026-10")

    def test_the_thirteen_weeks_are_the_ones_worked_out_on_paper(self) -> None:
        r, weeks = self.r, self.r["weeks"]
        self.assertEqual((r["as_of"], r["opening_cash"], r["complete"]), ("2026-10-31", 81500.0, True))
        self.assertEqual((weeks[0]["start"], weeks[0]["end"], weeks[12]["start"], weeks[12]["end"]),
                         ("2026-11-01", "2026-11-07", "2027-01-24", "2027-01-30"))
        self.assertEqual([w["receipts"] for w in weeks], RECEIPTS)
        self.assertEqual([w["bills"] for w in weeks], BILLS)
        self.assertEqual([w["standing"] for w in weeks], STANDING)
        self.assertEqual([w["closing"] for w in weeks], CLOSING)
        self.assertEqual(r["totals"], {"receipts": 42000.0, "bills": 9500.0, "standing": 81000.0, "payments": 90500.0,
                                       "closing": 33000.0})
        self.assertIsNone(r["first_week_below"])
        self.assertEqual(r["could_not_work_out"], [])

    def test_every_week_reconciles_and_each_opens_where_the_last_closed(self) -> None:
        for terms in ((None, None), (0, 0), (45, 10), (90, 60), (365, 365)):
            r = weekly_cash(self.led, "2026-10", *terms)
            balance = r["opening_cash"]
            for w in r["weeks"]:
                self.assertEqual(w["opening"], balance, terms)
                self.assertEqual(w["payments"], round(w["bills"] + w["standing"], 2))
                balance = round(balance + w["receipts"] - w["payments"], 2)
                self.assertEqual(w["closing"], balance, (terms, w["week"]))
            t = r["totals"]
            self.assertEqual(round(r["opening_cash"] + t["receipts"] - t["bills"] - t["standing"], 2), t["closing"])
            # nothing owed is lost: what is in the weeks and what falls after them is what the ledger says is open
            for part, kind in ((r["receipts"], "receivables"), (r["bills"], "payables")):
                self.assertEqual(round(part["placed"] + part["later"], 2), part["open"])
                self.assertEqual((part["open"], part["unplaced"]), (round(self.led.value(kind, "2026-10"), 2), 0.0))
                self.assertEqual(round(sum(part["by_week"]), 2), part["placed"])

    def test_a_customer_is_placed_by_its_own_history_and_a_credit_note_is_not_a_payment(self) -> None:
        who = {p["party"]: p for p in self.r["receipts"]["parties"]}
        self.assertEqual((who["Northgate"]["basis"], who["Northgate"]["days"], who["Northgate"]["settled_documents"]),
                         ("history", 40, 3))
        self.assertEqual((who["Marlow"]["basis"], who["Marlow"]["days"], who["Marlow"]["settled_documents"]), ("terms", 30, 1))
        self.assertEqual((self.r["receipts"]["by_history"], self.r["receipts"]["by_terms"]), (1, 3))
        item = {x["entry"]: x for x in self.r["receipts"]["items"]}
        self.assertEqual((item["INV-7"]["expected"], item["INV-7"]["week"], item["INV-7"]["basis"]), ("2026-11-19", 3, "history"))
        self.assertEqual((item["INV-8"]["expected"], item["INV-8"]["week"]), ("2026-12-04", 5))
        self.assertNotIn("INV-6", item)  # closed by the credit note

    def test_an_invoice_past_its_date_is_put_in_week_one_and_said_to_be_overdue(self) -> None:
        item = {x["entry"]: x for x in self.r["receipts"]["items"]}
        self.assertEqual((item["INV-5"]["expected"], item["INV-5"]["overdue"], item["INV-5"]["week"]), ("2026-10-10", True, 1))
        self.assertEqual(self.r["receipts"]["overdue"], 4000.0)
        self.assertFalse(item["INV-9"]["overdue"])

    def test_money_received_ahead_of_an_invoice_covers_it(self) -> None:
        item = {x["entry"]: x for x in self.r["receipts"]["items"]}
        self.assertEqual((item["INV-11"]["amount"], item["INV-11"]["expected"], item["INV-11"]["week"]), (3000.0, "2026-11-14", 2))
        self.assertEqual(self.r["receipts"]["credit"], 0.0)

    def test_the_terms_given_move_only_the_parties_without_a_history(self) -> None:
        r = weekly_cash(self.led, "2026-10", 90, 60)
        item = {x["entry"]: x for x in r["receipts"]["items"] + r["bills"]["items"]}
        self.assertEqual((item["INV-7"]["expected"], item["INV-7"]["week"]), ("2026-11-19", 3))        # Northgate: history
        self.assertEqual((item["INV-10"]["expected"], item["INV-10"]["week"]), ("2027-01-28", 13))     # Quay: 90 days
        self.assertEqual((item["INV-5"]["expected"], item["INV-5"]["overdue"], item["INV-5"]["week"]), ("2026-12-09", False, 6))
        self.assertEqual((item["BILL-6"]["expected"], item["BILL-6"]["week"]), ("2026-11-11", 2))      # Tide: history
        self.assertEqual((item["BILL-7"]["expected"], item["BILL-7"]["week"]), ("2026-12-11", 6))      # Grey: 60 days
        # at 120 days Marlow's September invoice lands on 8 January (week 10); its October one (6,000.00), Pier
        # Nine's (3,000.00) and Quay's (9,000.00) fall after 30 January: named, counted, and not in any week
        later = weekly_cash(self.led, "2026-10", 120)
        item = {x["entry"]: x for x in later["receipts"]["items"]}
        self.assertEqual((item["INV-5"]["expected"], item["INV-5"]["week"]), ("2027-01-08", 10))
        self.assertEqual([item[e]["week"] for e in ("INV-9", "INV-10", "INV-11")], [None, None, None])
        self.assertEqual((later["receipts"]["later"], later["receipts"]["placed"]), (18000.0, 24000.0))
        self.assertEqual(sum(w["receipts"] for w in later["weeks"]), 24000.0)

    def test_standing_payments_are_found_with_the_entries_they_came_from(self) -> None:
        st = self.r["standing"]
        found = {x["account"]: x for x in st["found"]}
        self.assertEqual(set(found), {"6100 Office rent", "6200 Salaries", "6900 Corporation tax"})
        rent, salaries, tax = found["6100 Office rent"], found["6200 Salaries"], found["6900 Corporation tax"]
        self.assertEqual((rent["every"], rent["amount"], rent["party"], rent["label"]),
                         ("month", 3000.0, "Harbour Estates", "6100 Office rent (Harbour Estates)"))
        self.assertEqual(rent["next"], [{"date": "2026-11-01", "amount": 3000.0, "week": 1},
                                        {"date": "2026-12-01", "amount": 3000.0, "week": 5},
                                        {"date": "2027-01-01", "amount": 3000.0, "week": 9}])
        self.assertEqual([x["entry"] for x in rent["from"]], ["RENT-08", "RENT-09", "RENT-10"])
        # the latest amount is the one carried, not the average of three
        self.assertEqual((salaries["amount"], [b["amount"] for b in salaries["by_month"]]), (22000.0, [20000.0, 20000.0, 22000.0]))
        self.assertEqual([(x["date"], x["week"]) for x in salaries["next"]], [("2026-11-25", 4), ("2026-12-25", 8), ("2027-01-25", 13)])
        self.assertEqual((tax["every"], [b["month"] for b in tax["by_month"]]), ("quarter", ["2026-02", "2026-05", "2026-08"]))
        self.assertEqual(tax["next"], [{"date": "2026-11-20", "amount": 6000.0, "week": 3}])
        self.assertEqual(st["in_forecast"], 81000.0)

    def test_a_payment_every_month_for_amounts_far_apart_is_named_and_not_carried(self) -> None:
        st = self.r["standing"]
        self.assertEqual([(x["account"], [b["amount"] for b in x["by_month"]]) for x in st["varying"]],
                         [("6300 Marketing", [1000.0, 5000.0, 2000.0])])
        # what the forecast does not contain, as a monthly average of August to October, for scale
        self.assertEqual(st["window"], ["2026-08", "2026-09", "2026-10"])
        self.assertEqual(st["recent"], {"customer_receipts": 12333.33, "supplier_payments": 1333.33,
                                        "other_receipts": 500.0, "other_payments": 6666.67})

    def test_the_first_week_below_a_floor(self) -> None:
        r = weekly_cash(self.led, "2026-10", floor=60000)
        self.assertEqual(r["first_week_below"], {"week": 8, "start": "2026-12-20", "end": "2026-12-26", "closing": 58000.0})
        self.assertIsNone(weekly_cash(self.led, "2026-10", floor=33000)["first_week_below"])   # at the floor is not below it
        self.assertEqual(weekly_cash(self.led, "2026-10", floor=33000.01)["first_week_below"]["week"], 13)

    def test_from_an_earlier_month_end_only_what_was_known_then_is_used(self) -> None:
        r = weekly_cash(self.led, "2026-09")
        self.assertEqual(r["as_of"], "2026-09-30")
        entries = {x["entry"] for x in r["receipts"]["items"]}
        self.assertEqual(entries, {"INV-5"})                 # October's invoices did not exist yet
        self.assertEqual(r["bills"]["open"], 0.0)
        salaries = next(x for x in r["standing"]["found"] if x["account"] == "6200 Salaries")
        self.assertEqual(salaries["amount"], 20000.0)

    def test_values_that_are_not_days_or_an_amount_are_refused(self) -> None:
        for args in ((-1, None, None), (366, None, None), (None, "soon", None), (None, None, -5), (None, None, "low"),
                     (None, None, float("inf"))):
            with self.assertRaises(Invalid, msg=args):
                weekly_cash(self.led, "2026-10", *args)


class TooLittle(unittest.TestCase):
    def test_two_months_of_ledger_give_receipts_and_bills_and_no_balance(self) -> None:
        rows = [r for r in company() if r[0] == "Date" or r[0] >= "2026-09-01"]
        r = weekly_cash(Ledger(lines(rows), {}), "2026-10")
        self.assertFalse(r["complete"])
        self.assertIsNone(r["standing"])
        self.assertTrue(all(w["closing"] is None and w["opening"] is None and w["standing"] is None and w["payments"] is None
                            for w in r["weeks"]))
        self.assertEqual((r["totals"]["standing"], r["totals"]["payments"], r["totals"]["closing"]), (None, None, None))
        self.assertIsNone(r["first_week_below"])
        self.assertEqual(sum(w["bills"] for w in r["weeks"]), 9500.0)
        self.assertGreater(sum(w["receipts"] for w in r["weeks"]), 0)
        self.assertEqual(len(r["could_not_work_out"]), 1)
        self.assertIn("the ledger has 2 month(s) up to 2026-10 and 3 are needed", r["could_not_work_out"][0])
        self.assertIn("withheld", r["could_not_work_out"][0])

    def test_a_balance_brought_forward_without_documents_is_said_and_given_no_week(self) -> None:
        opening = {account_key(AR[0], AR[1]): 7000.0}
        r = weekly_cash(Ledger(lines(company()), {}, opening=opening), "2026-10")
        self.assertEqual((r["receipts"]["ledger_balance"], r["receipts"]["open"], r["receipts"]["unplaced"]), (49000.0, 42000.0, 7000.0))
        self.assertEqual([w["receipts"] for w in r["weeks"]], RECEIPTS)
        self.assertIn("7,000.00 of the balance with customers has no document in the ledger", r["could_not_work_out"][0])

    def test_a_habit_needs_three_settled_documents_within_a_year(self) -> None:
        def one(raised: str, settled: str, amount: float, n: int) -> dict[str, Any]:
            return {"raised": raised, "paid": settled, "amount": amount, "document": (raised, f"D-{n}")}
        as_of = date(2026, 10, 31)
        two = [one("2026-08-01", "2026-08-11", 100, 1), one("2026-09-01", "2026-09-11", 100, 2)]
        self.assertIsNone(habit(two, as_of))
        # weighted by amount: (100 x 10 + 100 x 10 + 800 x 50) / 1000 = 42 days
        self.assertEqual(habit([*two, one("2026-07-01", "2026-08-20", 800, 3)], as_of), {"days": 42, "documents": 3})
        # one document paid in three parts is one document
        parts = [one("2026-08-01", f"2026-08-{d}", 100, 1) for d in ("05", "10", "15")]
        self.assertIsNone(habit(parts, as_of))
        # and settlements more than a year back say nothing about today
        old = [one("2025-01-01", "2025-01-11", 100, n) for n in (1, 2, 3)]
        self.assertIsNone(habit(old, as_of))

    def test_the_week_of_a_date_and_the_same_day_in_a_shorter_month(self) -> None:
        as_of = date(2026, 10, 31)
        self.assertEqual([_week(date(2026, 11, d), as_of) for d in (1, 7, 8, 14, 15)], [1, 1, 2, 2, 3])
        self.assertEqual(_week(date(2027, 1, 30), as_of), 13)
        self.assertEqual(_week(date(2027, 1, 31), as_of), 14)
        self.assertEqual((_shift(date(2026, 10, 31), 1), _shift(date(2026, 10, 31), 4), _shift(date(2026, 11, 25), 2)),
                         (date(2026, 11, 30), date(2027, 2, 28), date(2027, 1, 25)))


class Tool(unittest.TestCase):
    def setUp(self) -> None:
        self.h = Harness()
        self.addCleanup(self.h.close)
        self.ws = self.h.setup_workspace(with_model=False)
        up = self.h.api("POST", f"/api/w/{self.ws}/imports", files={"file": ("gl.csv", as_csv(company()))})
        self.h.api("POST", f"/api/w/{self.ws}/imports/{up['upload_id']}/commit",
                   json={"name": "General ledger", "sheet": None, "header_row": 0})
        workspace = dict(self.h.engine.db.one("SELECT * FROM workspaces WHERE id = ?", (self.ws,)))
        user = self.h.engine.db.one("SELECT user_id FROM members WHERE workspace_id = ?", (self.ws,))["user_id"]
        self.ctx = ToolContext(self.h.engine, workspace, user)

    def call(self, args: dict[str, Any]) -> Any:
        async def go() -> Any:
            tools = await registry.for_context(self.ctx)
            return await tools["weekly_cash_forecast"].handler(self.ctx, args)
        return asyncio.run(go())

    def test_the_imported_ledger_gives_the_same_weeks_and_the_answer_says_them(self) -> None:
        out = self.call({})
        text, r = out.for_model(), out.data
        self.assertEqual([w["closing"] for w in r["weeks"]], CLOSING)
        self.assertIn("Cash for the 13 weeks from 2026-11-01 to 2027-01-30, by the direct method", text)
        self.assertIn("as it stood at 2026-10-31. Cash at that date: 81,500.00.", text)
        self.assertIn("Over the 13 weeks: receipts 42,000.00, less bills 9,500.00 and standing payments 81,000.00, "
                      "leaves 33,000.00 at 2027-01-30.", text)
        self.assertIn("The balance stays at or above zero in every week.", text)
        self.assertIn("1 to 2026-11-07: 4,000.00; 0.00; 3,000.00; 82,500.00 | 2 to 2026-11-14: 3,000.00; 9,500.00; 0.00; "
                      "76,000.00", text)
        self.assertIn("13 to 2027-01-30: 0.00; 0.00; 22,000.00; 33,000.00", text)
        table = out.blocks[0]
        self.assertEqual((table["title"], len(table["rows"]), table["rows"][7]),
                         ("Cash by week", 13, [8, "2026-12-26", 80000.0, 0.0, 0.0, 22000.0, 58000.0]))

    def test_it_says_what_it_assumed_and_what_it_leaves_out(self) -> None:
        text = self.call({}).for_model()
        self.assertIn("1 customer(s) placed by the days they have taken to pay: Northgate (40 days over 3 settled). 3 "
                      "placed by terms of 30 days, an assumption: the ledger carries no due date. 4,000.00 is already "
                      "past that date and is put in week 1.", text)
        self.assertIn("1 supplier(s) placed by the days they have taken to be paid: Tide (20 days over 3 settled).", text)
        self.assertIn("6200 Salaries 22,000.00 every month (paid 2026-08 20,000.00, 2026-09 20,000.00, 2026-10 22,000.00)", text)
        self.assertIn("6900 Corporation tax 6,000.00 every quarter (paid 2026-02 6,000.00, 2026-05 6,000.00, 2026-08 "
                      "6,000.00)", text)
        self.assertIn("too far apart to carry forward, so not in the forecast: 6300 Marketing (1,000.00, 5,000.00, "
                      "2,000.00).", text)
        self.assertNotIn("Could not be worked out", text)

    def test_the_closing_balance_is_never_a_paragraph_without_what_it_leaves_out(self) -> None:
        # Asked for this forecast on 25 Nov 2026, a model gave the closing balance and dropped the monthly averages,
        # which stood in a paragraph of their own further down. They are one paragraph now.
        said = [line for line in self.call({}).for_model().split("\n") if "leaves 33,000.00 at 2027-01-30" in line]
        self.assertEqual(len(said), 1)
        self.assertEqual(said[0], (
            "Over the 13 weeks: receipts 42,000.00, less bills 9,500.00 and standing payments 81,000.00, leaves 33,000.00 "
            "at 2027-01-30. That balance counts only what is already invoiced, already billed or paid every month: it is "
            "not what the bank will hold. Sales not yet invoiced, bills not yet received and payments with no pattern are "
            "not in it. For scale, a month of 2026-08 to 2026-10 on average: 1,333.33 paid to suppliers and 6,666.67 in "
            "other payments that are not carried forward, against 12,333.33 received from customers and 500.00 of other "
            "money received."))
        tool = asyncio.run(registry.for_context(self.ctx))["weekly_cash_forecast"]
        self.assertIn("never give its closing balance without the sentence that follows it", " ".join(tool.description.split()))

    def test_without_a_balance_it_still_says_what_the_weeks_leave_out(self) -> None:
        h = Harness()
        self.addCleanup(h.close)
        ws = h.setup_workspace(with_model=False)
        rows = [r for r in company() if r[0] == "Date" or r[0] >= "2026-09-01"]
        up = h.api("POST", f"/api/w/{ws}/imports", files={"file": ("gl.csv", as_csv(rows))})
        h.api("POST", f"/api/w/{ws}/imports/{up['upload_id']}/commit", json={"name": "General ledger", "sheet": None, "header_row": 0})
        workspace = dict(h.engine.db.one("SELECT * FROM workspaces WHERE id = ?", (ws,)))
        user = h.engine.db.one("SELECT user_id FROM members WHERE workspace_id = ?", (ws,))["user_id"]
        ctx = ToolContext(h.engine, workspace, user)

        async def go() -> Any:
            return await (await registry.for_context(ctx))["weekly_cash_forecast"].handler(ctx, {})
        text = asyncio.run(go()).for_model()
        self.assertIn("No weekly balance is given: see what could not be worked out.", text)
        self.assertIn("These are the open invoices and bills only: sales not yet invoiced, bills not yet received and "
                      "payments made straight from the bank are not in them.", text)
        self.assertNotIn("leaves", text)
        self.assertIn("Could not be worked out: Standing payments", text)

    def test_a_floor_terms_and_a_month_the_ledger_does_not_cover(self) -> None:
        self.assertIn("The balance first falls below the floor of 60,000.00 in week 8 (2026-12-20 to 2026-12-26), which "
                      "ends at 58,000.00.", self.call({"floor": 60000}).for_model())
        out = self.call({"floor": 60000, "customer_terms_days": 120})
        # 81,500 - 3,000 = 78,500; - 9,500 = 69,000; + 12,000 - 6,000 = 75,000; - 22,000 = 53,000 in week 4
        self.assertIn("The balance first falls below the floor of 60,000.00 in week 4 (2026-11-22 to 2026-11-28), which "
                      "ends at 53,000.00.", out.for_model())
        self.assertIn("3 placed by terms of 120 days", out.for_model())
        self.assertIn("18,000.00 falls after week 13 and is not in the weeks.", out.for_model())
        self.assertEqual((out.data["totals"]["receipts"], out.data["totals"]["closing"]), (24000.0, 15000.0))
        gone = self.call({"month": "2027-03"})
        self.assertIn("No ledger data for 2027-03", gone.for_model())
        self.assertEqual(gone.data, {"month": "2027-03", "available": False})
        self.assertIn("between 0 and 365 days", self.call({"supplier_terms_days": 900}).for_model())

    def test_it_reads_and_does_not_write_and_the_words_for_it_bring_it_in(self) -> None:
        tools = asyncio.run(registry.for_context(self.ctx))
        tool = tools["weekly_cash_forecast"]
        self.assertEqual((tool.category, tool.needs_approval()), ("analysis", False))
        self.assertNotIn(tool.category, CORE)
        for asked in ("give me a 13-week cash forecast", "what does liquidity look like over the coming weeks",
                      "أعطني توقع النقد لـ 13 أسبوع"):
            groups = select_groups([ChatMessage("user", asked)], tools, has_datasets=True)
            self.assertIn("weekly_cash_forecast", {t.name for t in choose_tools(tools, groups)}, asked)
        self.assertIn("For cash week by week over the coming weeks use weekly_cash_forecast, and say what it leaves out "
                      "as it says it: its closing balance is never given without that and the monthly averages beside it.",
                      " ".join(RULES.split()))
        before = self.h.engine.db.one("SELECT COUNT(*) AS n FROM audit_log")["n"]
        self.call({})
        self.assertEqual(self.h.engine.db.one("SELECT COUNT(*) AS n FROM audit_log")["n"], before)

if __name__ == "__main__":
    unittest.main()
