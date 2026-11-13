"""P1 — staged concurrency: turn latency, throughput, errors and event-loop responsiveness.

The model is scripted with zero latency, so the figures are the engine's own cost. Four workloads, each at 1, 5, 20
and 50 concurrent conversations: an answer, a calculator call, a KPI scorecard call on the demo ledger, and a
database read that takes 200 ms (a slow query on a large table, simulated by delaying the dataset listing). While
each stage runs, /health is probed every 20 ms; its latency is how long any other user would wait. Run from engine/:

    python3 ../reports/ai-agent-production-readiness/evals/p1_staged_load.py [out.json]
"""
from __future__ import annotations

import json
import os
import platform
import resource
import statistics
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from unittest import mock

sys.path.insert(0, ".")
from tests.helpers import Harness  # noqa: E402
from tests.test_overview import GL  # noqa: E402

STAGES = (1, 5, 20, 50)


def pct(xs, q):
    xs = sorted(xs)
    return round(xs[min(len(xs) - 1, int(round(q * (len(xs) - 1))))], 1) if xs else None


class Responder:
    """Answers each model request from its own conversation: a tool call first, then the answer once the tool's
    result is in (a shared script would hand one conversation several tool calls under concurrency)."""

    def __init__(self, llm, script):
        self.llm, self.script = llm, script

    def __bool__(self):
        return True

    def pop(self, _i=0):
        last = self.llm.calls[-1]["messages"][-1]
        return self.script[-1] if last["role"] == "tool" or len(self.script) == 1 else self.script[0]


def main() -> None:
    h = Harness()
    ws = h.setup_workspace()
    h.api("POST", f"/api/w/{ws}/cost-centres/ledger/upload", files={"file": ("GL.xlsx", GL.read_bytes())})
    from desk_engine.agent.tools import registry
    tool = registry.get_static("list_datasets")
    real_handler = tool.handler

    async def slow_handler(ctx, a):          # the tool's own query is slow; nothing else is
        time.sleep(0.2)
        return await real_handler(ctx, a)

    workloads = {
        "answer": lambda: ["Revenue is in the ledger."],
        "calculator": lambda: [[("calc_growth", {"values": [100, 120, 150]})], "Growth was 20.00% then 25.00%."],
        "kpi_scorecard": lambda: [[("financial_kpis", {"month": "2026-10"})], "Gross margin was 56.6%."],
        "slow_db_read": lambda: [[("list_datasets", {})], "You have one ledger."],
    }
    out = {"environment": {"python": platform.python_version(), "cpus": os.cpu_count(), "platform": platform.platform()},
           "model": "scripted, zero latency", "stages": {}}
    for name, script in workloads.items():
        for n in STAGES:
            patcher = mock.patch.object(tool, "handler", slow_handler) if name == "slow_db_read" else None
            if patcher:
                patcher.start()
            threads = [h.api("POST", f"/api/w/{ws}/threads", json={})["id"] for _ in range(n)]
            h.llm.script = Responder(h.llm, script())
            probes, stop = [], threading.Event()

            def probe():
                while not stop.is_set():
                    t = time.perf_counter()
                    h.client.get("/health")
                    probes.append((time.perf_counter() - t) * 1000)
                    time.sleep(0.02)
            pt = threading.Thread(target=probe)
            pt.start()
            cpu0, wall0 = time.process_time(), time.perf_counter()

            def turn(tid):
                r = h.client.post(f"/api/w/{ws}/threads/{tid}/messages", json={"text": "q"})
                return r.status_code
            with ThreadPoolExecutor(max_workers=n) as ex:
                codes = list(ex.map(turn, threads))
            deadline = time.time() + 120
            while time.time() < deadline:
                runs = [h.engine.db.one("SELECT status, error, state, created_at, updated_at FROM agent_runs WHERE thread_id = ? "
                                        "ORDER BY created_at DESC LIMIT 1", (t,)) for t in threads]
                if all(r and r["status"] in ("done", "error") for r in runs):
                    break
                time.sleep(0.01)
            wall = time.perf_counter() - wall0
            cpu = time.process_time() - cpu0
            stop.set()
            pt.join()
            if patcher:
                patcher.stop()
            lat = [(r["updated_at"] - r["created_at"]) * 1000 for r in runs if r]
            errors = sum(1 for r in runs if not r or r["status"] != "done") + sum(c != 200 for c in codes)
            out["stages"][f"{name}@{n}"] = {
                "turns": n, "errors": errors,
                "error_codes": sorted({json.loads(r["state"] or "{}").get("error_code") or (r["error"] or "")[:60]
                                       for r in runs if r and r["status"] != "done"}), "turn_ms": {"p50": pct(lat, .5), "p95": pct(lat, .95), "p99": pct(lat, .99)},
                "turns_per_s": round(n / wall, 1), "cpu_s": round(cpu, 2),
                "health_ms": {"n": len(probes), "p50": pct(probes, .5), "p95": pct(probes, .95),
                              "max": round(max(probes), 1) if probes else None,
                              "mean": round(statistics.fmean(probes), 1) if probes else None}}
            print(name, n, json.dumps(out["stages"][f"{name}@{n}"]))
    out["max_rss_mb"] = round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1)
    h.close()
    if len(sys.argv) > 1:
        json.dump(out, open(sys.argv[1], "w"), indent=1)


if __name__ == "__main__":
    main()
