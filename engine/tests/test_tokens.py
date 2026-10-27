"""Token use: tools chosen per request, a stable cacheable prefix, and shortened old tool results — measured."""

from __future__ import annotations

import json
import os
import unittest
from unittest import mock

from .helpers import Harness


def request_chars(call: dict) -> int:
    """Characters in one request as the model API receives it (messages plus tool schemas)."""
    return len(json.dumps(call.get("messages", []))) + len(json.dumps(call.get("tools") or []))


def tool_names(call: dict) -> set[str]:
    return {t["function"]["name"] for t in call.get("tools") or []}


class Tokens(unittest.TestCase):
    def setUp(self) -> None:
        self.h = Harness()
        self.ws = self.h.setup_workspace()

    def tearDown(self) -> None:
        self.h.close()

    def ask(self, text: str, thread: str | None = None) -> str:
        h, ws = self.h, self.ws
        tid = thread or h.api("POST", f"/api/w/{ws}/threads", json={})["id"]
        h.api("POST", f"/api/w/{ws}/threads/{tid}/messages", json={"text": text})
        h.wait_run(tid)
        return tid

    def test_selected_tools_cut_the_request(self) -> None:
        h = self.h
        question = "What is the NPV of -1000, 600, 600 at 5%?"
        h.llm.script = ["NPV is 115.65."]
        self.ask(question)
        small = h.llm.calls[-1]
        with mock.patch.dict(os.environ, {"DESK_TOOL_SELECTION": "off"}):
            h.llm.script = ["NPV is 115.65."]
            self.ask(question)
        full = h.llm.calls[-1]
        self.assertIn("calc_npv_irr", tool_names(small))
        self.assertIn("more_tools", tool_names(small))
        self.assertNotIn("create_workbook", tool_names(small))
        saved = 1 - request_chars(small) / request_chars(full)
        print(f"\n[tokens] simple question: {request_chars(full):,} → {request_chars(small):,} characters "
              f"({saved:.0%} smaller); tools {len(tool_names(full))} → {len(tool_names(small))}")
        self.assertGreater(saved, 0.35)
        # the stable part of the system prompt comes first and carries no date; the date is in the short part
        system = [m for m in small["messages"] if m["role"] == "system"]
        self.assertEqual(len(system), 1)  # OpenAI-compatible servers get one system message
        stable, _, dynamic = system[0]["content"].partition("Today is ")
        self.assertIn("You are OfinTech Desk", stable)
        self.assertTrue(dynamic)

    def test_request_words_and_more_tools_bring_groups(self) -> None:
        h = self.h
        h.llm.script = ["ok"]
        self.ask("Build an Excel workbook of our fees")
        self.assertIn("create_workbook", tool_names(h.llm.calls[-1]))
        h.llm.script = ["ok"]
        self.ask("أنشئ تقرير الانحرافات")  # Arabic: "build the variance report"
        self.assertIn("create_report", tool_names(h.llm.calls[-1]))
        # a request with no file words: the model asks for the files group, and gets it on the next step
        h.llm.script = [[("more_tools", {"groups": ["files"]})], "Loaded."]
        tid = self.ask("Prepare something for the board")
        first, second = h.llm.calls[-2], h.llm.calls[-1]
        self.assertNotIn("create_workbook", tool_names(first))
        self.assertIn("create_workbook", tool_names(second))
        # and keeps it for the rest of the conversation
        h.llm.script = ["ok"]
        self.ask("and another one", tid)
        self.assertIn("create_workbook", tool_names(h.llm.calls[-1]))
        # a tool that was not sent still runs when the model names it
        h.llm.script = [[("get_file_review", {"file_id": "f_missing"})], "None."]
        tid = self.ask("hello")
        tool_msg = [m for m in h.api("GET", f"/api/w/{self.ws}/threads/{tid}")["messages"] if m["role"] == "tool"][0]
        self.assertEqual(tool_msg["content"], "Error: File not found.")

    def test_old_tool_results_are_shortened(self) -> None:
        h = self.h
        import io

        from openpyxl import Workbook

        wb = Workbook()
        for i in range(1, 200):
            wb.active.append([f"Account {i}", i * 1000.5, i * 990.25])
        buf = io.BytesIO()
        wb.save(buf)
        fid = h.client.post(f"/api/w/{self.ws}/files/upload", files={"file": ("tb.xlsx", buf.getvalue())}).json()["file"]["id"]
        h.llm.script = [[("inspect_file", {"file_id": fid})], "The trial balance has 199 accounts."]
        tid = self.ask("Read my trial balance file")
        long_result = [m for m in h.llm.calls[-1]["messages"] if m["role"] == "tool"][0]["content"]
        self.assertGreater(len(long_result), 3000)
        h.llm.script = ["ok"]
        self.ask("thanks", tid)
        self.assertNotIn("shortened from", json.dumps(h.llm.calls[-1]["messages"]))  # still among the last 2 turns
        h.llm.script = ["ok"]
        self.ask("one more question", tid)
        resent = [m for m in h.llm.calls[-1]["messages"] if m["role"] == "tool"][0]["content"]
        self.assertIn("shortened from", resent)
        self.assertLess(len(resent), 800)


if __name__ == "__main__":
    unittest.main()
