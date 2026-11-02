"""The question set has to aim at the gaps, ask the same thing in both languages, and discriminate."""

from __future__ import annotations

import json
import pathlib
import unittest

from ofinbench import items as items_mod

TEMPLATES = pathlib.Path(__file__).resolve().parents[1] / "templates"


def build(kind: str) -> list[dict]:
    return items_mod.build(kind, json.loads((TEMPLATES / f"{kind}.truth.json").read_text(encoding="utf-8")))


class Shape(unittest.TestCase):
    def test_every_question_is_asked_in_both_languages(self) -> None:
        """The synthetic run shows en 98.2 against ar 95.7; that gap is only measurable if both are asked."""
        for kind in ("bank-statement", "monthly-close"):
            with self.subTest(kind=kind):
                built = build(kind)
                arabic = {i["id"][:-3] for i in built if i["lang"] == "ar"}
                english = {i["id"][:-3] for i in built if i["lang"] == "en"}
                self.assertEqual(arabic, english)
                self.assertTrue(arabic)

    def test_the_pair_shares_one_expected_answer(self) -> None:
        by_slug: dict[str, list[dict]] = {}
        for item in build("bank-statement"):
            by_slug.setdefault(item["id"][:-3], []).append(item)
        for slug, pair in by_slug.items():
            with self.subTest(slug=slug):
                self.assertEqual(pair[0]["check"], pair[1]["check"])

    def test_ids_are_unique(self) -> None:
        built = build("bank-statement") + build("monthly-close")
        self.assertEqual(len(built), len({i["id"] for i in built}))

    def test_categories_are_known(self) -> None:
        for item in build("bank-statement") + build("monthly-close"):
            self.assertIn(item["category"], items_mod.CATEGORIES)

    def test_a_case_carries_enough_questions_to_be_worth_the_work(self) -> None:
        self.assertGreaterEqual(len(build("bank-statement")), 20)
        self.assertGreaterEqual(len(build("monthly-close")), 20)


class AimedAtTheGaps(unittest.TestCase):
    def test_every_case_carries_the_three_refusals(self) -> None:
        """Safety fell from 92.2 to 89.1 after fine-tuning, so it is asked of every document, not sampled."""
        for kind in ("bank-statement", "monthly-close"):
            with self.subTest(kind=kind):
                reasons = {i["check"].get("reason") for i in build(kind) if i["check"]["type"] == "refuse"}
                self.assertEqual({"payment", "egress", "scope"}, reasons)

    def test_the_scope_refusal_demands_the_model_say_what_it_covers(self) -> None:
        """It already declines off-topic questions; the recorded gap is that it declines without naming its scope."""
        scope = [i for i in build("bank-statement") if i["check"].get("reason") == "scope"]
        self.assertTrue(all(i["check"]["must_mention_scope"] for i in scope))

    def test_the_payment_refusal_names_a_real_counterparty_from_the_document(self) -> None:
        """Refusing in the abstract is easy; the test is refusing while holding the account it names."""
        payment = next(i for i in build("bank-statement") if i["check"].get("reason") == "payment")
        self.assertNotIn("a new beneficiary", payment["question"])

    def test_largest_and_oldest_overdue_are_different_customers(self) -> None:
        """A case where they coincide cannot tell a correct ranking from a lucky one, and ranking is the weak spot."""
        built = build("monthly-close")
        largest = next(i for i in built if i["id"].startswith("mc-000-example.largest_overdue"))
        oldest = next(i for i in built if i["id"].startswith("mc-000-example.oldest_overdue"))
        self.assertNotEqual(largest["check"]["any_of"], oldest["check"]["any_of"])

    def test_ranking_questions_ask_for_one_answer_and_forbid_the_rest(self) -> None:
        for item in build("bank-statement") + build("monthly-close"):
            if item["category"] == "ranking":
                with self.subTest(item=item["id"]):
                    self.assertTrue(item["check"].get("must_not_mention"))
                    self.assertTrue(any(w in item["question"] for w in ("واحد", "one ")))

    def test_the_remaining_budget_question_accepts_either_correct_form(self) -> None:
        """The previous evaluation marked a correct "percentage used" answer wrong, costing marks already earned."""
        item = next(i for i in build("monthly-close") if "budget_remaining" in i["id"])
        self.assertEqual("amount_any_of", item["check"]["type"])
        self.assertEqual(2, len([e for e in item["check"]["expected"] if e is not None]))

    def test_the_remaining_budget_question_is_only_asked_of_an_underspent_account(self) -> None:
        item = next(i for i in build("monthly-close") if "budget_remaining" in i["id"])
        self.assertGreater(item["check"]["expected"][0], 0, "how much is left on an overspent account has no one answer")

    def test_most_questions_ask_for_a_figure_from_the_document(self) -> None:
        """Grounded data answers scored 89.2 against 98.0 for tool selection, so the weight belongs on the figures."""
        built = build("bank-statement")
        grounded = [i for i in built if i["category"] in ("extraction", "aggregation", "derived", "ranking")]
        self.assertGreater(len(grounded), len(built) // 2)


class Normalisation(unittest.TestCase):
    def test_arabic_spelling_variants_are_the_same_name(self) -> None:
        """A model writing الواحه for الواحة has named the right customer; alef and ta-marbuta forms are optional."""
        self.assertEqual(items_mod.normalize_text("شركة الواحة"), items_mod.normalize_text("شركه الواحه"))
        self.assertEqual(items_mod.normalize_text("إبراهيم"), items_mod.normalize_text("ابراهيم"))
        self.assertEqual(items_mod.normalize_text("Oasis  Trading Co."), items_mod.normalize_text("oasis trading co"))

    def test_different_names_stay_different(self) -> None:
        self.assertNotEqual(items_mod.normalize_text("الواحة"), items_mod.normalize_text("المنارة"))


if __name__ == "__main__":
    unittest.main()
