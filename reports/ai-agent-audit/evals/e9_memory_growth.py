"""E9 — does memory grow with the number of conversations? (leak check)

Runs batches of one-turn conversations, each in a new thread, and records resident memory and the size of the
engine's per-thread structures after each batch (with a full garbage collection first).

    python3 reports/ai-agent-audit/evals/e9_memory_growth.py
"""
from __future__ import annotations

import gc
import time

from common import Harness, save
from e6_benchmarks import rss_mb


def main() -> None:
    h = Harness()
    h.setup_workspace()
    points = []
    for batch in range(6):
        for i in range(250):
            h.llm.script = [[("calc_growth", {"values": [1, 2]})], "ok"]
            t = h.api("POST", f"/api/w/{h.ws}/threads", json={})["id"]
            h.api("POST", f"/api/w/{h.ws}/threads/{t}/messages", json={"text": "q"})
            while h.engine.db.one("SELECT status FROM agent_runs WHERE thread_id = ?", (t,))["status"] not in ("done", "error"):
                time.sleep(0.002)
            h.llm.calls.clear()          # the test model's and mock transport's own records are not the engine's
            h.router.requests.clear()
        gc.collect()
        ev = h.engine.events
        chans = next((len(v) for k, v in vars(ev).items() if isinstance(v, dict)), None)
        points.append({"conversations": (batch + 1) * 250, "rss_mb": round(rss_mb(), 1), "agent_tasks_kept": len(h.engine.agent.tasks),
                       "event_channels": chans, "harness_records_kept": len(h.llm.calls) + len(h.router.requests)})
        print(points[-1])
    h.close()
    growth = (points[-1]["rss_mb"] - points[1]["rss_mb"]) / (points[-1]["conversations"] - points[1]["conversations"]) * 1000
    save("e9_memory_growth", {"points": points, "kb_per_conversation_after_warmup": round(growth, 1),
                              "note": "the test model's and the mock transport's request records are cleared after every conversation"})
    print(f"growth after warm-up: {growth:.1f} KB per conversation")


if __name__ == "__main__":
    main()
