"""The provenance-aware figure check (audit finding AI-05). The classes measured in the agent audit's E4 evaluation, each
pinned here with a deterministic example, plus the agent loop storing what the app shows."""

from __future__ import annotations

import unittest

from desk_engine.agent.figures import classify, parse, report

from .helpers import Harness

SCORECARD = ("KPI | Value | Previous month\nGross margin | 45.0% | 44.8%\nCurrent ratio | 1.85× | 1.80×\n"
             "Days sales outstanding (DSO) | 52 days | 49 days\nRevenue from top 5 customers | 83.7% | 80.1%")
LEDGER = "Month | Revenue\n2026-09 | 120,450.37\n2026-10 | 98,210.00"


def sources(answer: str, tools=(SCORECARD, LEDGER), users=(), memories=()) -> dict[str, str]:
    return {f.raw: f.provenance for f in classify(answer, list(tools), list(users), list(memories))}


class Parsing(unittest.TestCase):
    def test_kinds_units_and_precision(self) -> None:
        figs = {f.raw: f for f in parse("Margin 45.2%, ratio 1.85x, DSO 52 days, revenue 1.2m and 98,210.00; "
                                        "FY2026, 2026-10-31, 31 October, step 3, see [2].")}
        self.assertEqual(set(figs), {"45.2%", "1.85x", "52 days", "1.2m", "98,210.00"})
        self.assertEqual((figs["45.2%"].kind, figs["45.2%"].half_unit), ("percent", 0.05))
        self.assertEqual(figs["1.85x"].kind, "ratio")
        self.assertEqual((figs["52 days"].kind, figs["52 days"].unit), ("count", "day"))
        self.assertEqual((figs["1.2m"].value, figs["1.2m"].half_unit), (1_200_000, 50_000))
        self.assertEqual(figs["98,210.00"].half_unit, 0.005)


class ArabicAnswers(unittest.TestCase):
    """Seen on 23 Nov 2026: a correct Arabic answer marked "check before relying on: 605.55, 502.47, 216.84"."""

    AGEING = ("Customer | Closing balance | 0-30 days | 31-60 days | 61-90 days | Over 90 days\n"
              "A | 486,688.55 | 101,363.69 | 79,605.55 | 76,502.47 | 229,216.84")
    ANSWER = ("486,688.55 USD موزعة على 101,363.69 USD (0-30 يومًا) و79,605.55 USD (31-60 يومًا) "
              "و76,502.47 USD (61-90 يومًا) و229,216.84 USD (أكثر من 90 يومًا). أقدم فاتورة مفتوحة: 2026-05-11.")

    def test_a_figure_joined_to_an_arabic_particle_is_read_whole(self) -> None:
        self.assertEqual([f.raw for f in parse(self.ANSWER)],
                         ["486,688.55", "101,363.69", "79,605.55", "76,502.47", "229,216.84"])
        for particle in ("و", "بـ", "لـ", "ف", "ك"):
            self.assertEqual([f.raw for f in parse(f"المبلغ {particle}15,000.00 ريال")], ["15,000.00"], particle)

    def test_the_correct_answer_is_no_longer_marked(self) -> None:
        r = report(self.ANSWER, [self.AGEING], [], [])
        self.assertEqual(r["unverified_figures"], [])
        self.assertEqual({p["source"] for p in r["figure_provenance"]}, {"tool"})

    def test_a_wrong_figure_after_the_particle_is_still_caught(self) -> None:
        r = report(self.ANSWER.replace("و79,605.55", "و79,650.55"), [self.AGEING], [], [])
        self.assertEqual(r["unverified_figures"], ["79,650.55"], "the whole figure, not its tail")

    def test_no_figure_starts_in_the_middle_of_another(self) -> None:
        # whatever stands in front of it: a letter of any script, or nothing the pattern knows
        for text in ("و79,605.55", "x79,605.55", "№79,605.55", "ـ79,605.55"):
            self.assertNotIn("605.55", [f.raw for f in parse(text)], text)

    def test_latin_ids_are_still_not_figures(self) -> None:
        self.assertEqual([f.raw for f in parse("INV-1104, Q3, v1.2, item2 and x12: 1,234.50")], ["1,234.50"])

    def test_the_arabic_percent_sign(self) -> None:
        figs = parse("الهامش 45.0٪")
        self.assertEqual([(f.raw, f.kind) for f in figs], [("45.0٪", "percent")])
        self.assertEqual(sources("الهامش 45.0٪")["45.0٪"], "tool")


class Provenance(unittest.TestCase):
    def test_copied_and_rounded_figures_are_verified(self) -> None:
        s = sources("Revenue was 120,450.37 (about 120k, or 0.12m), margin 45.0%, ratio 1.85x, DSO 52 days.")
        self.assertEqual(set(s.values()), {"tool"}, s)

    def test_a_mis_copy_within_half_a_percent_is_caught(self) -> None:
        self.assertEqual(sources("Revenue was 120,850.37.")["120,850.37"], "unsupported")   # 0.33% off
        self.assertEqual(sources("Revenue was 120,450.36.")["120,450.36"], "unsupported")   # one cent off

    def test_units_must_agree(self) -> None:
        self.assertEqual(sources("Margin was 185.0%.")["185.0%"], "unsupported")       # the ratio 1.85x as a percentage
        self.assertEqual(sources("Margin was 52.0%.")["52.0%"], "unsupported")         # the day count as a percentage
        self.assertEqual(sources("DSO is 5 days.")["5 days"], "unsupported")           # 5 customers is not 5 days

    def test_small_counts_with_a_unit_are_checked(self) -> None:
        self.assertEqual(sources("DSO is 47 days.")["47 days"], "unsupported")
        self.assertEqual(sources("DSO is 49 days last month.")["49 days"], "tool")

    def test_the_users_figures_are_never_verified_by_themselves(self) -> None:
        r = report("As you said, revenue was 500,000.00.", [SCORECARD, LEDGER], ["Revenue was 500,000.00, right?"], [])
        self.assertEqual(r["unverified_figures"], [])
        self.assertEqual(r["user_figures"], ["500,000.00"])
        self.assertEqual(r["figure_provenance"][0]["source"], "user")

    def test_derived_figures_are_recomputed(self) -> None:
        s = sources("September 120,450.37 and October 98,210.00: down 22,240.37, a change of -18.5%, "
                    "and 218,660.37 in total.")
        self.assertEqual(s["22,240.37"], "derived")
        self.assertEqual(s["-18.5%"], "derived")
        self.assertEqual(s["218,660.37"], "derived")
        wrong = sources("September 120,450.37 and October 98,210.00: down 22,340.37.")
        self.assertEqual(wrong["22,340.37"], "unsupported")
        # a result whose operands the answer does not state is not taken on trust (profit = revenue - unstated cost)
        self.assertEqual(sources("Revenue 1,250,000.00, profit 450,000.00.",
                                 tools=["Revenue | 1,250,000.00\nCost of sales | 800,000.00"])["450,000.00"], "unsupported")

    def test_memory_counts_as_a_source(self) -> None:
        self.assertEqual(sources("Rent is 14,000.00 a month.", memories=["Office rent is 14,000.00 a month."])["14,000.00"],
                         "memory")


class InTheLoop(unittest.TestCase):
    def test_the_answer_payload_separates_the_users_figures(self) -> None:
        h = Harness(script=[[("calc_growth", {"values": [100, 120]})], "Growth was 20.00%; your target was 35,000.00 and "
                                                                          "the board figure is 77,777.77."])
        self.addCleanup(h.close)
        ws = h.setup_workspace()
        t = h.api("POST", f"/api/w/{ws}/threads", json={})["id"]
        h.api("POST", f"/api/w/{ws}/threads/{t}/messages", json={"text": "Growth from 100 to 120? Our target is 35,000.00."})
        d = h.wait_run(t)
        p = [m for m in d["messages"] if m["role"] == "assistant"][-1]["payload"]
        self.assertEqual(p["unverified_figures"], ["77,777.77"])
        self.assertEqual(p["user_figures"], ["35,000.00"])
        self.assertEqual({x["figure"]: x["source"] for x in p["figure_provenance"]}["20.00%"], "tool")


if __name__ == "__main__":
    unittest.main()
