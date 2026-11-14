"""P4 — event-loop responsiveness while agent turns run against a slower database (before/after comparison).

20 concurrent tool turns (calc_growth then an answer), repeated for 15 s, with every Database read delayed by
DELAY seconds inside the call (a large table, a cold disk, a busy lock). /health is probed every 20 ms. Reported:
/health latency, turn latency, throughput, CPU, loop reads per turn are in P3. Run from engine/ of each checkout:

    python3 p4_loop_benchmark.py out.json [delay_seconds]
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from unittest import mock

sys.path.insert(0, ".")
from desk_engine.db import Database  # noqa: E402
from tests.helpers import Harness  # noqa: E402

DELAY = float(sys.argv[2]) if len(sys.argv) > 2 else 0.002
USERS, SECONDS = 20, 15


def pct(xs, q):
    xs = sorted(xs)
    return round(xs[min(len(xs) - 1, int(round(q * (len(xs) - 1))))] * 1000, 1) if xs else None


class Responder:
    def __init__(self, llm):
        self.llm = llm

    def __bool__(self):
        return True

    def pop(self, _i=0):
        if len(self.llm.calls) > 400:
            del self.llm.calls[:-100]
        return "Growth was 20.00%." if self.llm.calls[-1]["messages"][-1]["role"] == "tool" else \
            [("calc_growth", {"values": [100, 120]})]


def main() -> None:
    h = Harness()
    ws = h.setup_workspace()
    h.llm.script = Responder(h.llm)
    import asyncio
    loop = h.client.portal.call(asyncio.get_running_loop)
    originals = {n: getattr(Database, n) for n in ("one", "all", "scalar")}

    def slowed(name):
        orig = originals[name]

        def f(self, *a, **k):
            if DELAY:
                time.sleep(DELAY)
            return orig(self, *a, **k)
        return f
    turns, probes, stop = [], [], threading.Event()

    def probe():
        while not stop.is_set():
            t0 = time.perf_counter()
            h.client.get("/health")
            probes.append(time.perf_counter() - t0)
            time.sleep(0.02)

    def user(i):
        end = time.perf_counter() + SECONDS
        while time.perf_counter() < end:
            tid = h.api("POST", f"/api/w/{ws}/threads", json={})["id"]
            t0 = time.perf_counter()
            h.client.post(f"/api/w/{ws}/threads/{tid}/messages", json={"text": "growth?"})
            done = threading.Event()
            task = h.engine.agent.tasks.get(tid)
            if task is not None:
                loop.call_soon_threadsafe(task.add_done_callback, lambda _t: done.set())
                done.wait(60)
            turns.append(time.perf_counter() - t0)
    cpu0 = time.process_time()
    with mock.patch.multiple(Database, **{n: slowed(n) for n in originals}):
        pt = threading.Thread(target=probe)
        pt.start()
        t0 = time.perf_counter()
        with ThreadPoolExecutor(USERS) as ex:
            list(ex.map(user, range(USERS)))
        wall = time.perf_counter() - t0
        stop.set()
        pt.join()
    lag = [line for line in h.api("GET", "/api/admin/metrics").splitlines()
           if line.startswith("desk_event_loop_lag_seconds_") and ("_sum" in line or "_count" in line)]
    status = h.engine.db.all("SELECT status, COUNT(*) AS n FROM agent_runs GROUP BY status")
    out = {"delay_s_per_read": DELAY, "users": USERS, "seconds": round(wall, 1), "turns": len(turns),
           "turns_per_s": round(len(turns) / wall, 1),
           "turn_ms": {"p50": pct(turns, .5), "p95": pct(turns, .95), "p99": pct(turns, .99)},
           "health_ms": {"n": len(probes), "p50": pct(probes, .5), "p95": pct(probes, .95), "p99": pct(probes, .99),
                         "max": round(max(probes) * 1000, 1) if probes else None},
           "cpu_cores": round((time.process_time() - cpu0) / wall, 2), "db_connections": len(h.engine.db._all),
           "event_loop_lag": lag, "runs_by_status": {r["status"]: r["n"] for r in status}, "cpus": os.cpu_count()}
    h.close()
    print(json.dumps(out))
    json.dump(out, open(sys.argv[1], "w"), indent=1)


if __name__ == "__main__":
    main()
