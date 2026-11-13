"""Closing the partly resolved audit findings AI-10, AI-11 and AI-12.

AI-10  a tool that reads the database runs on the event loop: a slow query stalls every conversation and its timeout
       cannot fire.
AI-11  the history sent to the model has a message window but no size budget, and nothing of the earlier answers
       survives once the window moves on.
AI-12  there is no metrics endpoint and no per-run trace of model and tool steps.
"""

from __future__ import annotations

import asyncio
import json
import time
import unittest
from unittest import mock

from desk_engine.agent.tools import ToolContext, ToolOutput, registry
from desk_engine.db import dumps, new_id

from .helpers import Harness


def _ctx(h: Harness, ws: str) -> ToolContext:
    workspace = h.engine.db.one("SELECT * FROM workspaces WHERE id = ?", (ws,))
    user = h.engine.db.scalar("SELECT id FROM users WHERE is_local = 1")
    return ToolContext(h.engine, workspace, user)


async def _run_with_ticker(coro) -> tuple[object, float]:
    """Runs ``coro`` while a 10 ms ticker measures the longest stall of the event loop."""
    worst = 0.0
    done = False

    async def tick() -> None:
        nonlocal worst
        last = time.perf_counter()
        while not done:
            await asyncio.sleep(0.01)
            t = time.perf_counter()
            worst = max(worst, t - last - 0.01)
            last = t
    ticker = asyncio.create_task(tick())
    await asyncio.sleep(0.05)                   # the ticker is running before the tool starts
    try:
        result = await coro
    finally:
        done = True
        await ticker
    return result, worst


class DatabaseToolsOffTheLoop(unittest.TestCase):
    """AI-10."""

    def setUp(self) -> None:
        self.h = Harness()
        self.addCleanup(self.h.close)
        self.ws = self.h.setup_workspace()

    def slow_list(self, seconds: float):
        def slow(engine, ws_id):            # a slow SQLite read (a big table, a lock, a cold disk)
            time.sleep(seconds)
            return []
        return mock.patch("desk_engine.data.store.list_datasets", slow)

    def test_a_slow_database_read_does_not_stall_the_loop(self) -> None:
        tool = registry.get_static("list_datasets")
        with self.slow_list(0.8):
            out, worst = asyncio.run(_run_with_ticker(tool.run(_ctx(self.h, self.ws), {})))
        self.assertIn("no datasets", out.text)
        self.assertLess(worst, 0.3, f"the event loop stalled for {worst:.2f}s")

    def test_the_timeout_of_a_database_tool_holds(self) -> None:
        tool = registry.get_static("list_datasets")
        old = tool.timeout
        tool.timeout = 0.3
        self.addCleanup(setattr, tool, "timeout", old)
        async def timed():
            started = time.perf_counter()
            out = await tool.run(_ctx(self.h, self.ws), {})
            return out, time.perf_counter() - started          # when the conversation gets its answer back
        with self.slow_list(1.5):
            out, took = asyncio.run(timed())
        self.assertIn("did not finish within", out.text)
        self.assertLess(took, 1.0)

    def test_offloaded_tools_are_read_only_and_local(self) -> None:
        offloaded = [t for t in registry.static_tools() if t.offload]
        self.assertGreaterEqual(len(offloaded), 15)
        for t in offloaded:
            self.assertFalse(t.needs_approval(), t.name)               # writes stay on the loop, approval-bound
            self.assertNotIn(t.category, {"mcp", "google", "accounting"}, t.name)          # no network clients
            self.assertNotIn(t.name, {"search_documents", "search_workspace", "inspect_file"})  # embeddings, storage


class HistoryBudget(unittest.TestCase):
    """AI-11."""

    def setUp(self) -> None:
        self.h = Harness()
        self.addCleanup(self.h.close)
        self.ws = self.h.setup_workspace()
        self.tid = self.h.api("POST", f"/api/w/{self.ws}/threads", json={})["id"]

    def seed(self, pairs: int, user_chars: int, answer_chars: int) -> None:
        ts = time.time() - 10_000
        rows = []
        for i in range(pairs):
            fact = "Our fiscal year ends on 31 March." if i == 0 else ""
            rows.append(("user", (fact + f" Question {i}. " + "u" * user_chars)[:max(user_chars, 60)], {}))
            rows.append(("assistant", f"Answer {i}: revenue line {i} reviewed. " + "a" * answer_chars, {}))
        for n, (role, content, payload) in enumerate(rows):
            self.h.engine.db.insert("messages", {"id": new_id("m_"), "thread_id": self.tid, "role": role,
                                                 "content": content, "payload": dumps(payload), "created_at": ts + n})

    def last_request(self) -> list[dict]:
        self.h.llm.script = ["Noted."]
        self.h.api("POST", f"/api/w/{self.ws}/threads/{self.tid}/messages", json={"text": "And now?"})
        self.h.wait_run(self.tid, 20)
        return self.h.llm.calls[-1]["messages"]

    def test_history_stays_within_the_budget(self) -> None:
        self.seed(pairs=29, user_chars=20_000, answer_chars=8_000)      # 60-message window of maximum-size messages
        msgs = self.last_request()
        history_chars = sum(len(m.get("content") or "") for m in msgs if m["role"] != "system")
        from desk_engine.agent.harness import history_budget_chars
        self.assertLessEqual(history_chars, history_budget_chars() + 6_000)   # + the notes and the workspace context
        self.assertEqual(msgs[-1]["content"], "And now?")

    def test_dropped_turns_keep_the_users_facts_and_a_trace_of_the_answers(self) -> None:
        self.seed(pairs=29, user_chars=20_000, answer_chars=8_000)
        msgs = self.last_request()
        notes = "\n".join(m["content"] for m in msgs if m["role"] == "user" and m["content"].startswith("[Earlier"))
        self.assertIn("fiscal year ends on 31 March", notes)
        self.assertIn("Answer 0", notes)                             # the assistant's earlier answers, shortened
        self.assertIn("not re-checked", notes)

    def test_a_short_conversation_is_sent_whole(self) -> None:
        self.seed(pairs=3, user_chars=100, answer_chars=100)
        msgs = self.last_request()
        self.assertFalse(any(m["role"] == "user" and m["content"].startswith("[Earlier") for m in msgs))
        self.assertEqual(sum(1 for m in msgs if m["role"] == "user" and m["content"].startswith("Our fiscal")), 1)


class MetricsAndTrace(unittest.TestCase):
    """AI-12."""

    def setUp(self) -> None:
        self.h = Harness()
        self.addCleanup(self.h.close)
        self.ws = self.h.setup_workspace()

    def turn(self) -> str:
        tid = self.h.api("POST", f"/api/w/{self.ws}/threads", json={})["id"]
        self.h.llm.script = [[("calc_growth", {"values": [100, 120]})], "Growth was 20.00%."]
        self.h.api("POST", f"/api/w/{self.ws}/threads/{tid}/messages", json={"text": "growth?"})
        self.h.wait_run(tid, 20)
        return tid

    def test_metrics_endpoint(self) -> None:
        self.turn()
        text = self.h.api("GET", "/api/admin/metrics")
        for line in ('desk_llm_calls_total{outcome="ok"} 2',
                     'desk_tool_calls_total{status="ok",tool="calc_growth"} 1',
                     'desk_agent_runs_total{outcome="completed"} 1'):
            self.assertIn(line, text)
        for name in ("desk_llm_call_seconds_bucket", "desk_llm_ttfb_seconds_count", "desk_tool_seconds_sum",
                     "desk_event_loop_lag_seconds"):
            self.assertIn(name, text)
        self.assertNotIn(self.ws, text)                               # nothing that names a tenant

    def test_metrics_need_the_instance_administrator(self) -> None:
        self.h.engine.db.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('instance_admin', ?)",
                                 (json.dumps("u_someone_else"),))
        self.h.api("GET", "/api/admin/metrics", expect=403)

    def test_failures_are_counted_by_class(self) -> None:
        tid = self.h.api("POST", f"/api/w/{self.ws}/threads", json={})["id"]
        self.h.llm.script = [""]                                      # an empty reply
        self.h.api("POST", f"/api/w/{self.ws}/threads/{tid}/messages", json={"text": "hi"})
        self.h.wait_run(tid, 20)
        self.assertIn('desk_agent_runs_total{outcome="failed"} 1', self.h.api("GET", "/api/admin/metrics"))

    def test_run_trace(self) -> None:
        tid = self.turn()
        trace = self.h.api("GET", f"/api/w/{self.ws}/threads/{tid}/trace")
        kinds = [s["kind"] for s in trace["steps"]]
        self.assertEqual(kinds, ["model", "tool", "model"])
        model, tool = trace["steps"][0], trace["steps"][1]
        self.assertEqual(model["tool_calls"], ["calc_growth"])
        for k in ("ttfb_ms", "total_ms", "attempts", "finish"):
            self.assertIn(k, model)
        self.assertEqual((tool["tool"], tool["status"]), ("calc_growth", "ok"))
        self.assertIsInstance(tool["ms"], float)
        self.assertEqual(trace["run"]["outcome"], "completed")

    def test_cached_repeat_calls_are_counted(self) -> None:
        async def fast(ctx, a):
            return ToolOutput("Period changes: 20.00%", [], {})
        tool = registry.get_static("calc_growth")
        old = tool.handler
        tool.handler = fast
        self.addCleanup(setattr, tool, "handler", old)
        tid = self.h.api("POST", f"/api/w/{self.ws}/threads", json={})["id"]
        self.h.llm.script = [[("calc_growth", {"values": [1, 2]})]] * 3 + ["Done."]
        self.h.api("POST", f"/api/w/{self.ws}/threads/{tid}/messages", json={"text": "g"})
        self.h.wait_run(tid, 20)
        self.assertIn('desk_tool_calls_total{status="reused",tool="calc_growth"} 1',
                      self.h.api("GET", "/api/admin/metrics"))


if __name__ == "__main__":
    unittest.main()
