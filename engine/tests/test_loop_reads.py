"""The agent loop keeps database reads off the event-loop thread (release blocker: a slow or locked database
stalled every conversation). Counts the reads made on the loop thread during agent turns."""

from __future__ import annotations

import asyncio
import threading
import time
import unittest
from unittest import mock

from desk_engine.db import Database

from .helpers import Harness


class LoopReads(unittest.TestCase):
    def setUp(self) -> None:
        self.h = Harness()
        self.addCleanup(self.h.close)
        self.ws = self.h.setup_workspace()
        self.loop_ident = self.h.client.portal.call(lambda: asyncio.sleep(0, threading.get_ident()))

    def count_loop_reads(self, turns: int = 5) -> float:
        seen = {"n": 0}
        ident = self.loop_ident
        wrapped = {}
        for name in ("one", "all", "scalar"):
            orig = getattr(Database, name)

            def counting(self_, *a, _orig=orig, **k):
                if threading.get_ident() == ident:
                    seen["n"] += 1
                return _orig(self_, *a, **k)
            wrapped[name] = counting
        threads = [self.h.api("POST", f"/api/w/{self.ws}/threads", json={})["id"] for _ in range(turns)]
        with mock.patch.multiple(Database, **wrapped):
            for t in threads:
                self.h.llm.script = [[("calc_growth", {"values": [100, 120]})], "Growth was 20.00%."]
                self.h.api("POST", f"/api/w/{self.ws}/threads/{t}/messages", json={"text": "growth?"})
                deadline = time.time() + 20
                while self.h.engine.agent.thread_busy(t) and time.time() < deadline:
                    time.sleep(0.01)
        return seen["n"] / turns

    def test_most_reads_leave_the_loop(self) -> None:
        # 60 reads per tool turn ran on the loop before (P3 profile); the context, history and tool-availability
        # reads now run in a worker thread; what remains are the run's own state and event payloads
        self.assertLessEqual(self.count_loop_reads(), 26)

    def test_a_slow_database_does_not_stall_the_loop_for_the_whole_turn(self) -> None:
        orig_all = Database.all

        def slow_all(self_, sql, params=()):
            if "FROM messages" in sql or "FROM connections" in sql or "FROM datasets" in sql:
                time.sleep(0.15)                        # a big history, a locked table
            return orig_all(self_, sql, params)
        lags = []
        stop = threading.Event()

        def probe() -> None:
            while not stop.is_set():
                t0 = time.perf_counter()
                self.h.client.get("/health")
                lags.append(time.perf_counter() - t0)
                time.sleep(0.01)
        t = self.h.api("POST", f"/api/w/{self.ws}/threads", json={})["id"]
        with mock.patch.object(Database, "all", slow_all):
            th = threading.Thread(target=probe)
            th.start()
            self.h.llm.script = [[("calc_growth", {"values": [1, 2]})], "Done."]
            self.h.api("POST", f"/api/w/{self.ws}/threads/{t}/messages", json={"text": "g"})
            deadline = time.time() + 30          # wait on the run itself; the thread API reads messages too
            while self.h.engine.agent.thread_busy(t) and time.time() < deadline:
                time.sleep(0.02)
            stop.set()
            th.join()
        self.assertLess(max(lags), 0.12, f"/health waited {max(lags):.2f}s behind the agent's reads")
        self.assertEqual(self.h.api("GET", f"/api/w/{self.ws}/threads/{t}")["run"]["status"], "done")


if __name__ == "__main__":
    unittest.main()


class HungToolsDoNotStarveOthers(unittest.TestCase):
    """Slow tools fill the default thread pool; another conversation's context and history reads must not queue
    behind them (found by the release load test: tool-timeout stage 9.6 -> 2.5 turns/s)."""

    def test_a_plain_question_is_answered_while_tools_hang(self) -> None:
        from desk_engine.agent.tools import registry
        h = Harness()
        self.addCleanup(h.close)
        ws = h.setup_workspace()
        tool = registry.get_static("calc_growth")
        real, old_timeout = tool.handler, tool.timeout

        async def hang(ctx, a):
            time.sleep(3)
            return await real(ctx, a)
        tool.handler, tool.timeout = hang, 1.0
        self.addCleanup(setattr, tool, "handler", real)
        self.addCleanup(setattr, tool, "timeout", old_timeout)

        class Responder:
            def __bool__(self):
                return True

            def pop(self, _i=0):
                msgs = h.llm.calls[-1]["messages"]
                if msgs[-1]["role"] == "tool":
                    return "It timed out."
                if "plain" in msgs[-1]["content"]:
                    return "Plain answer."
                return [("calc_growth", {"values": [1, 2]})]
        h.llm.script = Responder()
        busy = [h.api("POST", f"/api/w/{ws}/threads", json={})["id"] for _ in range(12)]
        for t in busy:                                   # 12 hung tool calls: twice the default pool on 2 CPUs
            h.api("POST", f"/api/w/{ws}/threads/{t}/messages", json={"text": "growth"})
        time.sleep(0.3)
        t = h.api("POST", f"/api/w/{ws}/threads", json={})["id"]
        started = time.perf_counter()
        h.api("POST", f"/api/w/{ws}/threads/{t}/messages", json={"text": "a plain question"})
        while h.engine.agent.thread_busy(t) and time.perf_counter() - started < 20:
            time.sleep(0.02)
        took = time.perf_counter() - started
        self.assertLess(took, 1.0, f"a plain question took {took:.2f}s behind hung tools")
        for b in busy:                                   # let the hung calls finish before shutdown
            while h.engine.agent.thread_busy(b) and time.perf_counter() - started < 30:
                time.sleep(0.05)
