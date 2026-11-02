"""How a figure is written must not change whether it is right."""

from __future__ import annotations

import unittest

from ofinbench.numbers import amounts_in, fold_digits, parse_amount, same_amount, states_amount


class Reading(unittest.TestCase):
    def test_every_form_a_gulf_document_uses(self) -> None:
        for text, expected in (
            ("SAR 1,234,567.89", 1234567.89),
            ("1,234,567.89 SAR", 1234567.89),
            ("12,500 ر.س", 12500.0),
            ("1.234.567,89", 1234567.89),
            ("4 500,25", 4500.25),
            ("١٢٣٤", 1234.0),
            ("٣٬٤٥٦٫٧٨", 3456.78),
            ("(2,500.00)", -2500.0),
            ("2500.00 DR", -2500.0),
            ("1,250.00-", -1250.0),
            ("−4,500.25", -4500.25),
            ("18.5%", 0.185),
            ("0", 0.0),
        ):
            with self.subTest(text=text):
                self.assertAlmostEqual(expected, parse_amount(text), places=6)

    def test_two_figures_is_not_a_figure(self) -> None:
        """Grading the first number in a hedged answer would reward hedging."""
        self.assertIsNone(parse_amount("either 1,000 or 2,000"))
        self.assertIsNone(parse_amount(""))
        self.assertIsNone(parse_amount("لا أعلم"))

    def test_fold_digits_leaves_everything_else_alone(self) -> None:
        self.assertEqual("1234 ريال", fold_digits("١٢٣٤ ريال"))

    def test_amounts_in_reads_a_whole_sentence(self) -> None:
        self.assertEqual([64200.0, 7480.25], amounts_in("رواتب 64,200.00 وكهرباء 7,480.25"))


class Tolerance(unittest.TestCase):
    def test_exact_and_rounded_to_the_riyal_both_pass(self) -> None:
        self.assertTrue(same_amount(196085.25, "196,085.25"))
        self.assertTrue(same_amount(196085.25, "196,085"))
        self.assertTrue(same_amount(196085.25, "SAR 196085.3"))

    def test_a_figure_that_is_merely_near_fails(self) -> None:
        """A blanket relative tolerance would accept an answer nearly two thousand riyals out on this balance."""
        self.assertFalse(same_amount(196085.25, "196,000"))
        self.assertFalse(same_amount(196085.25, "196,086"))
        self.assertFalse(same_amount(196085.25, "200,000"))

    def test_zero_is_not_satisfied_by_something_nearby(self) -> None:
        self.assertFalse(same_amount(0.0, "1.00"))
        self.assertTrue(same_amount(0.0, "0.00"))

    def test_a_relative_tolerance_applies_only_when_asked_for(self) -> None:
        self.assertFalse(same_amount(100000.0, "100,500"))
        self.assertTrue(same_amount(100000.0, "100,500", tolerance=0.01))

    def test_the_figure_is_found_inside_prose(self) -> None:
        self.assertTrue(same_amount(1234567.89, "The closing balance is SAR 1,234,567.89 as at 31 March."))

    def test_prose_direction_counts_as_the_sign(self) -> None:
        """"fees of 2,500 were charged" states the same fact as a ledger line of -2,500."""
        self.assertTrue(states_amount(-2500.0, "fees of 2,500 were charged"))
        self.assertFalse(states_amount(-2500.0, "fees of 2,500 were charged", signed=True))


if __name__ == "__main__":
    unittest.main()
