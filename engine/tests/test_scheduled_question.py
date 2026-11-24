"""A question on a timetable: the workflow template that asks the assistant one question on a schedule and sends
the person the answer.

What this guards. The template is filled in from four things a person chooses on the Workflows page (the
question, how often, the hour, e-mail or not) and from nothing else: the request cannot carry a step, a tool or a
recipient. The workflow is saved switched off like every template, is named after the question, and when it runs
the answer the assistant gave is what the person is told. The company and every figure below are invented.
"""

from __future__ import annotations

import unittest

from desk_engine.workflows.engine import upcoming
from desk_engine.workflows.templates import LOCALIZED, RHYTHMS, TEMPLATES, get_template, scheduled_question

from .helpers import Harness

QUESTION = "Which five customers owe the most, and how old is each balance?"


class Filled(unittest.TestCase):
    def test_what_is_left_out_stays_as_the_template_has_it(self) -> None:
        t = get_template("scheduled_question")
        self.assertEqual(scheduled_question(t, None, None, None, None), t)
        self.assertEqual(scheduled_question(t, "   ", None, None, None), t)

    def test_the_question_becomes_the_prompt_and_the_name_and_the_template_itself_is_not_changed(self) -> None:
        t = get_template("scheduled_question")
        before = repr(t)
        got = scheduled_question(t, f"  {QUESTION}\n", "weekday", 7, False)
        ask, tell = got["definition"]["steps"]
        self.assertEqual(ask["prompt"], "As of {{today}}: " + QUESTION)
        self.assertEqual(got["name"], QUESTION)
        self.assertEqual(got["definition"]["trigger"], {"type": "schedule", "cron": "0 7 * * 1-5"})
        self.assertEqual((tell["recipients"], tell["channels"], tell["message"]), ("me", ["in_app"], "{{steps.ask.output}}"))
        self.assertEqual(repr(t), before)

    def test_a_long_question_is_asked_whole_and_named_short(self) -> None:
        long = "How did revenue, gross margin and operating costs move against the same month last year " * 3
        got = scheduled_question(get_template("scheduled_question"), long, None, None, None)
        self.assertTrue(got["definition"]["steps"][0]["prompt"].endswith(long.strip()))
        self.assertEqual((len(got["name"]), got["name"][-1]), (78, "…"))

    def test_every_rhythm_at_every_hour_is_a_timetable_the_scheduler_reads(self) -> None:
        for every, line in RHYTHMS.items():
            for hour in (0, 8, 23):
                got = scheduled_question(get_template("scheduled_question"), QUESTION, every, hour, None)
                cron = got["definition"]["trigger"]["cron"]
                self.assertEqual(cron, line.format(hour=hour))
                times = upcoming(cron, "Asia/Riyadh", 3)
                self.assertEqual(len(times), 3, cron)
                self.assertTrue(all(f"T{hour:02d}:00" in x for x in times), (cron, times))
        # an hour alone keeps the weekly rhythm; a rhythm alone keeps 8 o'clock
        t = get_template("scheduled_question")
        self.assertEqual(scheduled_question(t, None, None, 17, None)["definition"]["trigger"]["cron"], "0 17 * * 1")
        self.assertEqual(scheduled_question(t, None, "month", None, None)["definition"]["trigger"]["cron"], "0 8 1 * *")

    def test_it_is_offered_last_and_in_every_language_the_others_are(self) -> None:
        self.assertEqual(TEMPLATES[-1]["key"], "scheduled_question")
        for lang, words in LOCALIZED.items():
            self.assertEqual(len(words["scheduled_question"]["steps"]), 2, lang)


class Scheduled(unittest.TestCase):
    def setUp(self) -> None:
        self.h = Harness()
        self.addCleanup(self.h.close)
        self.ws = self.h.setup_workspace()

    def make(self, **body: object) -> dict:
        return self.h.api("POST", f"/api/w/{self.ws}/workflows/from-template", json={"key": "scheduled_question", **body})

    def test_saved_switched_off_with_the_question_the_rhythm_and_the_hour(self) -> None:
        wf = self.make(question=QUESTION, every="month", hour=6, email=True)
        self.assertEqual((wf["name"], wf["enabled"]), (QUESTION, False))
        d = wf["definition"]
        self.assertEqual(d["trigger"]["cron"], "0 6 1 * *")
        self.assertEqual([(s["id"], s["type"]) for s in d["steps"]], [("ask", "agent"), ("tell", "notify")])
        self.assertEqual(d["steps"][0]["prompt"], "As of {{today}}: " + QUESTION)
        self.assertEqual(d["steps"][1]["channels"], ["in_app", "email"])

    def test_without_a_question_it_is_the_template_as_written(self) -> None:
        wf = self.make()
        self.assertEqual((wf["name"], wf["definition"]["trigger"]["cron"]), ("A question on a timetable", "0 8 * * 1"))
        self.assertIn("cash, receivables and payables", wf["definition"]["steps"][0]["prompt"])

    def test_in_the_persons_language_the_steps_are_named_in_it_and_the_question_is_theirs(self) -> None:
        wf = self.make(lang="ar", question="ما أكبر خمسة أرصدة مدينة؟", every="week", hour=9)
        self.assertEqual(wf["name"], "ما أكبر خمسة أرصدة مدينة؟")
        self.assertEqual([s["name"] for s in wf["definition"]["steps"]], ["طرح السؤال", "إرسال الجواب"])
        self.assertEqual(wf["definition"]["trigger"]["cron"], "0 9 * * 1")

    def test_an_hour_or_a_rhythm_that_does_not_exist_is_refused_and_nothing_is_saved(self) -> None:
        before = len(self.h.api("GET", f"/api/w/{self.ws}/workflows"))
        for bad in ({"hour": 24}, {"hour": -1}, {"every": "hourly"}, {"question": "x" * 2001}):
            self.h.api("POST", f"/api/w/{self.ws}/workflows/from-template", expect=422,
                       json={"key": "scheduled_question", "question": QUESTION, **bad})
        self.assertEqual(len(self.h.api("GET", f"/api/w/{self.ws}/workflows")), before)

    def test_the_fields_are_for_this_template_only(self) -> None:
        wf = self.h.api("POST", f"/api/w/{self.ws}/workflows/from-template",
                        json={"key": "weekly_ar", "question": QUESTION, "every": "day", "hour": 3, "email": False})
        self.assertEqual(wf["name"], "Weekly receivables follow-up")
        self.assertEqual(wf["definition"]["trigger"]["cron"], get_template("weekly_ar")["definition"]["trigger"]["cron"])
        self.assertNotIn(QUESTION, repr(wf["definition"]))

    def test_when_it_runs_the_person_is_told_what_the_assistant_answered(self) -> None:
        h, ws = self.h, self.ws
        wf = self.make(question=QUESTION, every="week", hour=8, email=False)
        answer = "Northgate owes 42,000.00 (61 days); the other four are under 30 days."
        h.llm.script = [answer]
        rid = h.api("POST", f"/api/w/{ws}/workflows/{wf['id']}/run", json={})["run_id"]
        run = h.wait_workflow(rid)
        self.assertEqual(run["status"], "done", run)
        asked = h.llm.calls[-1]["messages"][-1]["content"]
        self.assertTrue(asked.startswith("As of 20") and asked.endswith(QUESTION), asked)
        self.assertNotIn("{{", asked)
        self.assertEqual(run["context"]["steps"]["ask"]["output"], answer)
        note = h.api("GET", f"/api/w/{ws}/notifications")["items"][0]
        self.assertEqual(note["title"], f"{QUESTION}: {answer}")


if __name__ == "__main__":
    unittest.main()
