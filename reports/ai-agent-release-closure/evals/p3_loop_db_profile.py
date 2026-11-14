"""P3 — which database calls run on the event-loop thread during agent turns, and how long they block it.

Every Database call is attributed to the thread it ran on and to its calling function. Run from engine/:

    python3 ../reports/ai-agent-release-closure/evals/p3_loop_db_profile.py [out.json]
"""
from __future__ import annotations

import collections
import json
import sys
import threading
import time
import traceback

sys.path.insert(0, ".")
from desk_engine.db import Database  # noqa: E402
from tests.helpers import Harness  # noqa: E402
from tests.test_overview import GL  # noqa: E402

calls: list[tuple[str, str, float]] = []
loop_thread: list[int] = []


def wrap(name):
    orig = getattr(Database, name)

    def timed(self, *a, **k):
        t0 = time.perf_counter()
        try:
            return orig(self, *a, **k)
        finally:
            if loop_thread and threading.get_ident() == loop_thread[0]:
                st = traceback.extract_stack(limit=6)
                site = next((f"{f.filename.split('desk_engine/')[-1]}:{f.name}" for f in reversed(st[:-1])
                             if "desk_engine/db.py" not in f.filename), "?")
                calls.append((name, site, time.perf_counter() - t0))
    setattr(Database, name, timed)


def main() -> None:
    for n in ("one", "all", "scalar", "execute", "insert", "update"):
        wrap(n)
    h = Harness()
    ws = h.setup_workspace()
    h.api("POST", f"/api/w/{ws}/cost-centres/ledger/upload", files={"file": ("GL.xlsx", GL.read_bytes())})
    import asyncio
    loop_thread.append(h.client.portal.call(lambda: asyncio.sleep(0, threading.get_ident())))
    turns = 20
    calls.clear()
    for _ in range(turns):
        t = h.api("POST", f"/api/w/{ws}/threads", json={})["id"]
        h.llm.script = [[("financial_kpis", {"month": "2026-10"})], "Gross margin was 56.6% in October 2026."]
        h.api("POST", f"/api/w/{ws}/threads/{t}/messages", json={"text": "How did October go?"})
        h.wait_run(t, 20)
    # attribute to the agent run (exclude the HTTP polling of wait_run and the API calls of this script)
    agent = [c for c in calls if c[1].startswith(("agent/", "memory", "skills/", "data/", "app.py:record", "audit"))
             or "harness" in c[1]]
    by = collections.Counter(c[1] for c in agent)
    secs = collections.defaultdict(float)
    for _, site, s in agent:
        secs[site] += s
    out = {"turns": turns, "loop_db_calls_total": len(calls), "agent_loop_db_calls_per_turn": round(len(agent) / turns, 1),
           "agent_loop_db_ms_per_turn": round(sum(s for *_, s in agent) / turns * 1000, 2),
           "top_sites": [{"site": k, "calls_per_turn": round(v / turns, 1), "ms_per_turn": round(secs[k] / turns * 1000, 3)}
                         for k, v in by.most_common(25)]}
    h.close()
    print(json.dumps(out, indent=1))
    if len(sys.argv) > 1:
        json.dump(out, open(sys.argv[1], "w"), indent=1)


if __name__ == "__main__":
    main()
