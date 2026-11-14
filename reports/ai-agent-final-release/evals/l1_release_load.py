"""L1 — release load test: scenarios A–E against the real engine in-process (scripted model, zero model latency).

k6 and Locust are not installable in the environment this was written in, so the load is driven by threads through
the engine's own ASGI test client (every request takes the real HTTP stack: routing, auth, validation, the agent
loop, SQLite). Run from engine/:

    python3 ../reports/ai-agent-final-release/evals/l1_release_load.py out.json [--quick]

A  normal mix (25 users): financial question, KPI tool turn, calculator turn, dashboard KPIs, dashboard insights
B  concurrency stages: 10, 25, 50, 100 users, the same mix
C  peak: 200 users arriving at once
D  long conversations: 10 users x 60 turns in one conversation each (history budget, memory, latency drift)
E  failure recovery: model 503 burst, model timeouts, network errors, tool timeouts, slow database; then recovery
"""
from __future__ import annotations

import asyncio
import json
import os
import random
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from unittest import mock

sys.path.insert(0, ".")
import httpx2 as httpx  # noqa: E402

from desk_engine.metrics import process_cpu_seconds, process_rss_bytes  # noqa: E402
from tests.helpers import FakeLLM, Harness  # noqa: E402
from tests.test_overview import GL  # noqa: E402

QUICK = "--quick" in sys.argv
DUR = 8 if QUICK else 20                       # seconds per stage
URL = f"{FakeLLM.BASE}/chat/completions"
KINDS = {"question": "[q]", "kpi": "[k]", "calc": "[c]"}


def pct(xs, q):
    xs = sorted(xs)
    return round(xs[min(len(xs) - 1, int(round(q * (len(xs) - 1))))] * 1000, 1) if xs else None


class Model:
    """Answers per conversation from its first user message; can be switched to fail."""

    def __init__(self, llm: FakeLLM, router):
        self.llm, self.router, self.mode = llm, router, "ok"

    def __bool__(self):
        return True

    def pop(self, _i=0):
        body = self.llm.calls[-1]
        if len(self.llm.calls) > 400:          # the fake model and router keep every request; a test artefact, so trim
            del self.llm.calls[:-200]
        rq = getattr(self.router, "requests", None)
        if rq is not None and len(rq) > 400:
            del rq[:-200]
        msgs = body["messages"]
        if self.mode == "503":
            raise _Fail(httpx.Response(503, text="overloaded"))
        if self.mode == "timeout":
            raise httpx.ReadTimeout("timed out")
        if self.mode == "network":
            raise httpx.ConnectError("connection refused")
        if "chief executive" in msgs[0]["content"]:
            return "- Revenue is up on last year.\n- Cash covers two months.\n- Watch receivables."
        first = next((m["content"] for m in msgs if m["role"] == "user" and m["content"][:3] in KINDS.values()), "")
        if msgs[-1]["role"] == "tool":
            return "Here is what the ledger shows."
        if first.startswith("[k]"):
            return [("financial_kpis", {"month": "2026-10"})]
        if first.startswith("[c]"):
            return [("calc_npv_irr", {"cashflows": [-1000, 300, 400, 500], "discount_rate": 0.08})]
        return "Revenue and costs are in the ledger; ask me about a month."


class _Fail(Exception):
    def __init__(self, resp):
        self.resp = resp


def main() -> None:
    out_path = sys.argv[1]
    h = Harness()
    ws = h.setup_workspace()
    h.api("POST", f"/api/w/{ws}/cost-centres/ledger/upload", files={"file": ("GL.xlsx", GL.read_bytes())})
    model = Model(h.llm, h.router)
    real = h.llm.handle

    def handler(request):
        try:
            return real(request)
        except _Fail as f:
            return f.resp
    h.router.add("POST", URL, handler)
    h.llm.script = model
    os.environ.update(DESK_LLM_RETRY_BASE_S="0.05", DESK_LLM_RETRY_MAX_WAIT_S="0.2")
    db = h.engine.db
    loop = h.client.portal.call(asyncio.get_running_loop)

    def run_turn(tid: str, text: str) -> tuple[float, str]:
        t0 = time.perf_counter()
        r = h.client.post(f"/api/w/{ws}/threads/{tid}/messages", json={"text": text})
        if r.status_code != 200:
            return time.perf_counter() - t0, f"http_{r.status_code}"
        # wait for the run's task to finish (a done-callback on the engine's loop), not by polling the database:
        # polling from 100+ client threads in the same process would load the engine with the harness's own reads
        done = threading.Event()
        task = h.engine.agent.tasks.get(tid)
        if task is not None:
            loop.call_soon_threadsafe(task.add_done_callback, lambda _t: done.set())
            done.wait(60)
        row = db.one("SELECT status, state FROM agent_runs WHERE thread_id = ? ORDER BY created_at DESC LIMIT 1",
                     (tid,))
        while row and row["status"] not in ("done", "error") and time.perf_counter() - t0 < 60:
            time.sleep(0.05)                     # the task was gone before we looked: rare, poll briefly
            row = db.one("SELECT status, state FROM agent_runs WHERE thread_id = ? ORDER BY created_at DESC LIMIT 1",
                         (tid,))
        if not row or row["status"] not in ("done", "error"):
            return time.perf_counter() - t0, "client_timeout"
        code = json.loads(row["state"] or "{}").get("error_code") if row["status"] == "error" else "ok"
        return time.perf_counter() - t0, code or "error"

    def one_action(rnd: random.Random, tid: str) -> tuple[str, float, str]:
        kind = rnd.choices(["question", "kpi", "calc", "dashboard", "insights"], [30, 30, 20, 15, 5])[0]
        if kind in KINDS:
            secs, res = run_turn(tid, f"{KINDS[kind]} How did October 2026 go?")
            return kind, secs, res
        t0 = time.perf_counter()
        if kind == "dashboard":
            r = h.client.get(f"/api/w/{ws}/overview?month=2026-10")
        else:
            r = h.client.post(f"/api/w/{ws}/overview/insights?month=2026-10&lang=en", json={})
        return kind, time.perf_counter() - t0, "ok" if r.status_code == 200 else f"http_{r.status_code}"

    def stage(name: str, users: int, seconds: float, *, arrive_at_once: bool = False, turns_per_user: int | None = None,
              same_thread: bool = False, text: str | None = None):
        results: list[tuple[str, float, str]] = []
        lock = threading.Lock()
        stop = time.perf_counter() + seconds
        cpu0, rss0, t0 = process_cpu_seconds(), process_rss_bytes(), time.perf_counter()
        active_peak = [0]

        def user(i: int):
            rnd = random.Random(i)
            tid = h.api("POST", f"/api/w/{ws}/threads", json={})["id"]
            n = 0
            while (turns_per_user is None and time.perf_counter() < stop) or (turns_per_user is not None and n < turns_per_user):
                if not same_thread and n:
                    tid = h.api("POST", f"/api/w/{ws}/threads", json={})["id"]
                if text:
                    secs, res = run_turn(tid, text)
                    r = ("turn", secs, res)
                else:
                    r = one_action(rnd, tid)
                with lock:
                    results.append(r)
                    active_peak[0] = max(active_peak[0], sum(1 for t in h.engine.agent.tasks.values() if not t.done()))
                n += 1
        with ThreadPoolExecutor(max_workers=users) as ex:
            list(ex.map(user, range(users)))
        wall = time.perf_counter() - t0
        by: dict[str, list] = {}
        for k, s, r in results:
            by.setdefault(k, []).append((s, r))
        res = {"users": users, "seconds": round(wall, 1), "actions": len(results),
               "actions_per_s": round(len(results) / wall, 1),
               "errors": sum(1 for _, _, r in results if r != "ok"),
               "error_rate": round(sum(1 for _, _, r in results if r != "ok") / max(1, len(results)), 4),
               "error_codes": sorted({r for _, _, r in results if r != "ok"}),
               "cpu_cores_used": round((process_cpu_seconds() - cpu0) / wall, 2),
               "rss_mb_end": round(process_rss_bytes() / 2**20, 1),
               "rss_mb_delta": round((process_rss_bytes() - rss0) / 2**20, 1),
               "active_runs_peak": active_peak[0],
               "latency_ms": {k: {"n": len(v), "p50": pct([s for s, _ in v], .5), "p95": pct([s for s, _ in v], .95),
                                  "p99": pct([s for s, _ in v], .99)} for k, v in by.items()},
               "all_ms": {"p50": pct([s for _, s, _ in results], .5), "p95": pct([s for _, s, _ in results], .95),
                          "p99": pct([s for _, s, _ in results], .99)}}
        print(name, json.dumps({k: res[k] for k in ("users", "actions", "actions_per_s", "error_rate", "all_ms",
                                                      "cpu_cores_used", "rss_mb_end", "active_runs_peak")}), flush=True)
        return res

    out = {"environment": {"cpus": os.cpu_count(), "quick": QUICK, "stage_seconds": DUR,
                           "model": "scripted, zero latency (engine and API overhead only)",
                           "dataset": "HarborLine demo general ledger (synthetic)"}, "scenarios": {}}
    S = out["scenarios"]
    S["A_normal_25"] = stage("A", 25, DUR)
    for n in (10, 25, 50, 100):
        S[f"B_users_{n}"] = stage(f"B{n}", n, DUR)
    S["C_peak_200"] = stage("C", 200, DUR, arrive_at_once=True)
    rss_before = process_rss_bytes()
    S["D_long_10x60"] = stage("D", 10, 0, turns_per_user=12 if QUICK else 60, same_thread=True, text="[q] And then?")
    req = [sum(len(m.get("content") or "") for m in c["messages"]) for c in h.llm.calls[-200:]]
    S["D_long_10x60"]["request_chars_max_last_200"] = max(req)
    S["D_long_10x60"]["rss_mb_growth"] = round((process_rss_bytes() - rss_before) / 2**20, 1)
    # E: failures, each followed by a recovery window
    for mode in ("503", "timeout", "network"):
        model.mode = mode
        S[f"E_model_{mode}"] = stage(f"E-{mode}", 10, DUR / 2, text="[q] status?")
        model.mode = "ok"
        S[f"E_model_{mode}_recovery"] = stage(f"E-{mode}-rec", 10, DUR / 2, text="[q] status?")
    from desk_engine.agent.tools import registry
    tool = registry.get_static("financial_kpis")
    real_handler, real_timeout = tool.handler, tool.timeout

    async def hang(ctx, a):
        time.sleep(3)
        return await real_handler(ctx, a)
    tool.handler, tool.timeout = hang, 1.0
    S["E_tool_timeouts"] = stage("E-tool-timeout", 10, DUR / 2, text="[k] KPIs?")
    tool.handler, tool.timeout = real_handler, real_timeout
    observer = db.observer

    def slow_db(kind, secs):
        observer(kind, secs)
    with mock.patch.object(type(db), "one", lambda self, sql, params=(): (time.sleep(0.002),
                                                                         _orig_one(self, sql, params))[1]):
        S["E_db_delay_2ms_per_read"] = stage("E-db-delay", 10, DUR / 2, text="[k] KPIs?")
    S["E_db_recovery"] = stage("E-db-rec", 10, DUR / 2, text="[k] KPIs?")
    out["metrics_after"] = {l.split(" ")[0]: l.split(" ")[1] for l in h.api("GET", "/api/admin/metrics").splitlines()
                            if l.startswith(("desk_llm_calls_total", "desk_agent_runs_total", "desk_llm_retries_total",
                                             "desk_tool_calls_total{status=\"error\"", "desk_db_connections",
                                             "desk_metrics_dropped"))}
    h.close()
    json.dump(out, open(out_path, "w"), indent=1)


from desk_engine.db import Database  # noqa: E402

_orig_one = Database.one

if __name__ == "__main__":
    main()
