"""Resource lifecycle and loop hygiene (audit findings AI-10, AI-11, AI-18, AI-19)."""

from __future__ import annotations

import json
import time
import unittest

from desk_engine.agent.tools import ToolOutput, registry

from .helpers import Harness


class Resources(unittest.TestCase):
    def setUp(self) -> None:
        self.h = Harness()
        self.addCleanup(self.h.close)
        self.ws = self.h.setup_workspace()

    def turn(self, text: str, thread: str | None = None) -> dict:
        thread = thread or self.h.api("POST", f"/api/w/{self.ws}/threads", json={})["id"]
        self.h.api("POST", f"/api/w/{self.ws}/threads/{thread}/messages", json={"text": text})
        d = self.h.wait_run(thread, 20)
        d["thread_id"] = thread
        return d

    def patch(self, name: str, handler, timeout: float | None = None):
        tool = registry.get_static(name)
        old = (tool.handler, tool.timeout)
        tool.handler = handler
        if timeout is not None:
            tool.timeout = timeout

        def restore() -> None:
            tool.handler, tool.timeout = old
        self.addCleanup(restore)

    def test_finished_runs_release_their_task(self) -> None:
        threads = []
        for i in range(5):
            self.h.llm.script = [f"Answer {i}."]
            threads.append(self.turn(f"q{i}")["thread_id"])
        time.sleep(0.1)
        for t in threads:
            self.assertNotIn(t, self.h.engine.agent.tasks)
            self.assertFalse(self.h.engine.agent.thread_busy(t))

    def test_identical_read_calls_are_not_run_again_and_again(self) -> None:
        runs = []

        async def counting(ctx, a):
            runs.append(1)
            return ToolOutput("Period changes: 100.00%", [], {"changes": [1.0]})
        self.patch("calc_growth", counting)
        self.h.llm.script = [[("calc_growth", {"values": [1, 2]})]] * 6 + ["Growth was 100.00%."]
        d = self.turn("growth")
        results = [m for m in d["messages"] if m["role"] == "tool"]
        self.assertEqual(len(results), 6)                   # every call still gets a result (the APIs require it)
        self.assertEqual(len(runs), 2)                      # but the tool itself ran twice
        self.assertIn("Same call with the same arguments", results[2]["content"])
        self.assertEqual(d["run"]["status"], "done")

    def test_a_write_is_never_answered_from_an_earlier_result(self) -> None:
        self.h.llm.script = [[("message_colleague", {"member": "me@this-computer.local", "message": "Post the accruals."})]] * 3
        d = self.turn("remind the team")
        self.assertEqual(d["run"]["status"], "waiting_approval")
        approvals = self.h.api("GET", f"/api/w/{self.ws}/approvals")
        self.assertGreaterEqual(len(approvals), 1)
        self.assertFalse(any("Same call" in m["content"] for m in d["messages"] if m["role"] == "tool"))

    def test_the_users_earlier_messages_survive_a_long_conversation(self) -> None:
        thread = self.h.api("POST", f"/api/w/{self.ws}/threads", json={})["id"]
        self.h.llm.script = ["Noted."]
        self.turn("Remember for this conversation: our fiscal year ends on 30 June and we report in SAR thousands.", thread)
        for i in range(30):
            self.h.llm.script = [[("calc_growth", {"values": [1, 2 + i]})], f"ok {i}"]
            self.turn(f"Question {i}", thread)
        last = self.h.llm.calls[-1]["messages"]
        note = [m for m in last if m["role"] == "user" and m["content"].startswith("[Earlier in this conversation")]
        self.assertEqual(len(note), 1)
        self.assertIn("30 June", note[0]["content"])
        self.assertLessEqual(len(note[0]["content"]), 3300)
        ids = set()                                          # pairing still valid
        for m in last:
            for c in m.get("tool_calls") or []:
                ids.add(c["id"])
            if m["role"] == "tool":
                self.assertIn(m["tool_call_id"], ids)
        self.assertNotIn("30 June", json.dumps([m for m in last if m["role"] == "system"]))   # not in the system role

    def test_pure_computation_runs_off_the_event_loop(self) -> None:
        async def blocking(ctx, a):
            time.sleep(1.5)            # synchronous work that never yields
            return ToolOutput("done")
        self.patch("calc_growth", blocking, timeout=0.4)
        self.h.llm.script = [[("calc_growth", {"values": [1, 2]})], "ok"]
        thread = self.h.api("POST", f"/api/w/{self.ws}/threads", json={})["id"]
        self.h.api("POST", f"/api/w/{self.ws}/threads/{thread}/messages", json={"text": "growth"})
        time.sleep(0.1)
        t0 = time.perf_counter()
        self.h.client.get("/health")
        self.assertLess(time.perf_counter() - t0, 0.3)
        d = self.h.wait_run(thread, 20)
        result = [m for m in d["messages"] if m["role"] == "tool"][0]
        self.assertIn("did not finish", result["content"])
        self.assertTrue(all(t.pure for t in registry.static_tools() if t.name.startswith("calc_")))
        self.assertFalse(any(t.pure for t in registry.static_tools() if not t.name.startswith("calc_")))


if __name__ == "__main__":
    unittest.main()
