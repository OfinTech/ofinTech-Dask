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
