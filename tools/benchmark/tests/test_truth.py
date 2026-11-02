"""A truth file that does not foot marks correct answers wrong, so every invariant gets a negative test."""

from __future__ import annotations

import copy
import json
import pathlib
import unittest

from ofinbench import truth as truth_mod

TEMPLATES = pathlib.Path(__file__).resolve().parents[1] / "templates"


def load(kind: str) -> dict:
    return json.loads((TEMPLATES / f"{kind}.truth.json").read_text(encoding="utf-8"))


class Templates(unittest.TestCase):
    def test_both_templates_foot(self) -> None:
        """They are what every case is copied from; a broken template propagates into every case built from it."""
        for kind in ("bank-statement", "monthly-close"):
            with self.subTest(kind=kind):
                self.assertEqual([], truth_mod.validate(kind, load(kind)))


class BankStatementBreaks(unittest.TestCase):
    def check(self, mutate, expected: str) -> None:
        data = copy.deepcopy(load("bank-statement"))
        mutate(data)
        problems = truth_mod.validate_bank_statement(data)
        self.assertTrue(problems, "the break was not caught at all")
        self.assertTrue(any(expected in p for p in problems), f"expected {expected!r} in {problems}")

    def test_statement_that_does_not_foot(self) -> None:
        self.check(lambda d: d["balances"].__setitem__("closing", 1.0), "does not foot")

    def test_line_totals_disagreeing_with_the_header(self) -> None:
        def mutate(d):
            d["lines"][0]["credit"] = 1.0
            d["lines"][0]["balance"] = None
            d["balances"]["closing"] -= 48499.0
            for line in d["lines"][1:]:
                line["balance"] = None
        self.check(mutate, "in credits but totals.credits says")

    def test_transaction_count_that_does_not_match(self) -> None:
        self.check(lambda d: d["totals"].__setitem__("transaction_count", 99), "transaction_count says 99")

    def test_a_running_balance_that_breaks(self) -> None:
        self.check(lambda d: d["lines"][2].__setitem__("balance", 999.0), "but the running balance is")

    def test_a_date_outside_the_period(self) -> None:
        self.check(lambda d: d["lines"][0].__setitem__("date", "2025-12-31"), "outside the period")

    def test_a_line_carrying_both_columns(self) -> None:
        self.check(lambda d: d["lines"][0].__setitem__("debit", 5.0), "either a debit or a credit")

    def test_a_negative_column(self) -> None:
        self.check(lambda d: d["lines"][1].__setitem__("debit", -12300.00), "both written positive")

    def test_missing_currency(self) -> None:
        self.check(lambda d: d["account"].pop("currency"), "account.currency is required")

    def test_an_end_before_the_start(self) -> None:
        self.check(lambda d: d["period"].__setitem__("end", "2026-02-01"), "before period.start")

    def test_a_bad_language_or_format(self) -> None:
        self.check(lambda d: d["meta"].__setitem__("language", "Arabic"), "meta.language must be one of")
        self.check(lambda d: d["meta"].__setitem__("source_format", "scan"), "meta.source_format must be one of")

    def test_text_where_a_number_belongs(self) -> None:
        self.check(lambda d: d["balances"].__setitem__("opening", "125000"), "expected a number")

    def test_a_missing_field_is_reported_not_raised(self) -> None:
        data = copy.deepcopy(load("bank-statement"))
        data.pop("balances")
        self.assertTrue(any("balances" in p for p in truth_mod.validate_bank_statement(data)))


class MonthlyCloseBreaks(unittest.TestCase):
    def check(self, mutate, expected: str) -> None:
        data = copy.deepcopy(load("monthly-close"))
        mutate(data)
        problems = truth_mod.validate_monthly_close(data)
        self.assertTrue(problems, "the break was not caught at all")
        self.assertTrue(any(expected in p for p in problems), f"expected {expected!r} in {problems}")

    def test_a_trial_balance_that_does_not_balance(self) -> None:
        self.check(lambda d: d["trial_balance"][0].__setitem__("debit", 1.0), "does not balance")

    def test_a_reported_figure_that_does_not_follow_from_the_roles(self) -> None:
        self.check(lambda d: d["reported"].__setitem__("net_profit", 1.0), "reported.net_profit says")

    def test_a_role_assigned_wrongly_shows_up_as_a_reported_break(self) -> None:
        """This is the check that earns its keep: the roles are a judgement, and a wrong one is invisible otherwise."""
        self.check(lambda d: next(r for r in d["trial_balance"] if r["account"] == "6100").__setitem__("role", "cogs"),
                   "reported.cogs says")

    def test_an_unknown_role(self) -> None:
        self.check(lambda d: d["trial_balance"][0].__setitem__("role", "banky"), "role must be one of")

    def test_a_duplicated_account(self) -> None:
        self.check(lambda d: d["trial_balance"].append(dict(d["trial_balance"][0])), "appears twice")

    def test_a_budget_line_for_an_account_that_does_not_exist(self) -> None:
        self.check(lambda d: d["budget"].append({"account": "9999", "amount": 1.0}), "not in the trial balance")

    def test_a_receivable_that_is_not_yet_due(self) -> None:
        self.check(lambda d: d["receivables"][0].__setitem__("due", "2026-06-30"), "cannot be overdue")

    def test_a_negative_receivable(self) -> None:
        self.check(lambda d: d["receivables"][0].__setitem__("amount", -1.0), "must be positive")

    def test_missing_currency(self) -> None:
        self.check(lambda d: d.pop("currency"), "currency is required")


class RoleTotals(unittest.TestCase):
    def test_revenue_reads_from_the_credit_column(self) -> None:
        """Revenue and payables carry credit balances; costs and cash carry debit balances. Getting this backwards
        would make every derived figure in the question set wrong in the same direction, which is hard to spot."""
        totals = truth_mod.role_totals([
            {"account": "4100", "debit": 0, "credit": 520000.0, "role": "revenue"},
            {"account": "5100", "debit": 286000.0, "credit": 0, "role": "cogs"},
            {"account": "1010", "debit": 196085.25, "credit": 0, "role": "cash"},
            {"account": "2010", "debit": 0, "credit": 148900.0, "role": "ap"},
        ])
        self.assertAlmostEqual(520000.0, totals["revenue"])
        self.assertAlmostEqual(286000.0, totals["cogs"])
        self.assertAlmostEqual(196085.25, totals["cash"])
        self.assertAlmostEqual(148900.0, totals["ap"])


if __name__ == "__main__":
    unittest.main()
