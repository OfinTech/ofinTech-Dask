"""E6 — benchmarks of the agent pipeline with the model's own time taken out.

The model is a scripted endpoint on an in-process mocked transport that answers instantly, so every millisecond
measured here is the engine's: building the prompt, choosing tools, running them, storing messages and events,
and the figure check. Live-model latency (time to first token, tokens per second) needs a real provider and is
NOT measured here.

    python3 reports/ai-agent-audit/evals/e6_benchmarks.py            # full run (about 10-15 minutes)
    python3 reports/ai-agent-audit/evals/e6_benchmarks.py --quick    # smaller samples
"""
from __future__ import annotations

import json
import random
import resource
import sys
import time

from common import Harness, pct, run_tool, save, tool_ctx

QUICK = "--quick" in sys.argv
N_TURNS = 60 if QUICK else 300
rng = random.Random(5)


def rss_mb() -> float:
    for line in open("/proc/self/status"):
        if line.startswith("VmRSS:"):
            return int(line.split()[1]) / 1024
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024


def stats(xs: list[float]) -> dict:
    return {"n": len(xs), "p50_ms": round(pct(xs, .5) * 1000, 1), "p95_ms": round(pct(xs, .95) * 1000, 1),
            "p99_ms": round(pct(xs, .99) * 1000, 1), "max_ms": round(max(xs) * 1000, 1), "mean_ms": round(sum(xs) / len(xs) * 1000, 1)}


def ledger(n_lines: int) -> list[dict]:
    accts = [("1010", "Bank", "Bank"), ("1100", "Trade receivables", "Receivable"), ("1300", "Inventory", "Asset"),
             ("2100", "Trade payables", "Payable"), ("3000", "Share capital", "Equity"), ("4000", "Sales", "Income"),
             ("5000", "Cost of goods sold", "Cost of sales"), ("6100", "Salaries", "Expense"), ("6200", "Rent", "Expense")]
    out = []
    for i in range(n_lines // 2):
        m = 1 + i % 24
        day = f"{2025 + (m - 1) // 12}-{(m - 1) % 12 + 1:02d}-{1 + i % 28:02d}"
        a, b = rng.sample(accts, 2)
        amt = round(rng.uniform(10, 50_000), 2)
        for (code, name, cls), dr, cr in ((a, amt, 0), (b, 0, amt)):
            out.append({"date": day, "entry": f"E{i}", "type": "Journal", "account_code": code, "account": name,
                        "account_class": cls, "debit": dr, "credit": cr, "description": f"line {i}", "party": f"P{i % 300}", "tags": []})
    return out


def push(h: Harness, ws: str, lines: list[dict]) -> float:
    conn = h.api("POST", f"/api/w/{ws}/connections/custom", json={"label": f"ERP{len(lines)}", "mode": "push"})
    t0 = time.perf_counter()
    for i in range(0, len(lines), 5000):
        r = h.client.post("/api/ingest/v1/accounting/journal-lines", json={"lines": lines[i:i + 5000]},
                          headers={"authorization": f"Bearer {conn['ingest_key']}"})
        assert r.status_code == 200, r.text[:300]
    return time.perf_counter() - t0


def run_turn(h: Harness, thread: str, text: str) -> float:
    t0 = time.perf_counter()
    h.api("POST", f"/api/w/{h.ws}/threads/{thread}/messages", json={"text": text})
    while True:
        run = h.engine.db.one("SELECT status FROM agent_runs WHERE thread_id = ? ORDER BY created_at DESC LIMIT 1", (thread,))
        if run and run["status"] in ("done", "error", "waiting_approval"):
            assert run["status"] == "done", run
            bad = h.engine.db.scalar("SELECT COUNT(*) FROM messages WHERE thread_id = ? AND role = 'tool' AND "
                                     "(content LIKE 'Error%' OR content LIKE 'Refused%')", (thread,))
            assert not bad, "a timed tool call failed"
            return time.perf_counter() - t0
        time.sleep(0.002)


def main() -> None:
    out: dict = {"model": "scripted, zero latency (engine overhead only)", "turns_per_scenario": N_TURNS}
    rss0 = rss_mb()

    # 1. end-to-end turns
    h = Harness()
    h.setup_workspace()
    push(h, h.ws, ledger(2000))
    for i in range(20):
        h.api("POST", f"/api/w/{h.ws}/documents", files=[("files", (f"policy{i}.txt",
              (f"Policy {i}. Hotels up to {100 + i} per night. Approvals above {1000 * i} need the finance manager. " * 30).encode()))])
    h.settle()
    scenarios = {
        "answer_only": ["The answer."],
        "one_calculator_call": [[("calc_npv_irr", {"cashflows": [-1000, 300, 400, 500], "discount_rate": 0.08})], "NPV is 19.98."],
        "kpi_scorecard_call": [[("financial_kpis", {})], "Here is the scorecard."],
        "document_search_call": [[("search_documents", {"query": "hotel limit per night"})], "Hotels up to 100 per night [1]."],
        "three_tool_calls": [[("calc_growth", {"values": [1, 2, 3]}), ("financial_kpis", {"category": "liquidity"}),
                              ("search_documents", {"query": "approvals"})], "Done."],
    }
    e2e = {}
    for name, script in scenarios.items():
        times = []
        for i in range(N_TURNS):
            h.llm.script = list(script)
            thread = h.api("POST", f"/api/w/{h.ws}/threads", json={})["id"]
            times.append(run_turn(h, thread, f"question {i} about revenue"))
        e2e[name] = stats(times)
        print(f"  turn {name:22s} {e2e[name]}")
    out["end_to_end_turn"] = e2e
    out["model_calls"] = len(h.llm.calls)
    sizes = [len(json.dumps(b["messages"])) + len(json.dumps(b.get("tools", []))) for b in h.llm.calls[-50:]]
    tools_sent = [len(b.get("tools", [])) for b in h.llm.calls[-50:]]
    out["request_size_chars"] = {"p50": pct(sizes, .5), "max": max(sizes), "tools_sent_p50": pct(tools_sent, .5),
                                 "approx_tokens_p50": round(pct(sizes, .5) / 4)}
    out["rss_mb_after_turns"] = round(rss_mb(), 1)

    # 2. a long thread: does a turn get slower as the conversation grows?
    thread = h.api("POST", f"/api/w/{h.ws}/threads", json={})["id"]
    growth = []
    for i in range(120):
        h.llm.script = [[("calc_growth", {"values": [1, 2]})], "ok " * 50]
        growth.append(run_turn(h, thread, f"q{i} " + "detail " * 100))
    out["long_thread"] = {"first_10": stats(growth[:10]), "last_10": stats(growth[-10:])}
    print("  long thread", out["long_thread"])

    # 3. concurrency: N conversations started at once
    conc = {}
    for n in (5, 20):
        threads = [h.api("POST", f"/api/w/{h.ws}/threads", json={})["id"] for _ in range(n)]
        h.llm.script = [[("financial_kpis", {})], "ok"] * n * 2
        t0 = time.perf_counter()
        for t in threads:
            h.api("POST", f"/api/w/{h.ws}/threads/{t}/messages", json={"text": "kpis"})
        while True:
            done = h.engine.db.scalar(f"SELECT COUNT(*) FROM agent_runs WHERE thread_id IN ({','.join('?' * n)}) AND "
                                      "status IN ('done','error')", tuple(threads))
            if done == n:
                break
            time.sleep(0.005)
        wall = time.perf_counter() - t0
        conc[n] = {"wall_s": round(wall, 3), "turns_per_s": round(n / wall, 2)}
        print(f"  concurrency {n}: {conc[n]}")
    out["concurrency"] = conc
    h.close()

    # 4. tool latency against ledger size
    tool_lat = {}
    for n_lines in ((2_000, 20_000) if QUICK else (2_000, 20_000, 100_000)):
        h2 = Harness()
        ws = h2.setup_workspace(with_model=False)
        ingest = push(h2, ws, ledger(n_lines))
        ctx = tool_ctx(h2, ws)
        cold, warm = [], []
        t0 = time.perf_counter(); o = run_tool(h2, ctx, "financial_kpis", {}); cold.append(time.perf_counter() - t0)
        assert "KPI" in o.text and not o.text.startswith("Error"), o.text[:200]
        for _ in range(10 if QUICK else 30):
            t0 = time.perf_counter(); run_tool(h2, ctx, "financial_kpis", {}); warm.append(time.perf_counter() - t0)
        ds = h2.api("GET", f"/api/w/{ws}/datasets")
        ds = ds["datasets"] if isinstance(ds, dict) else ds
        q = []
        for _ in range(10 if QUICK else 30):
            t0 = time.perf_counter()
            o = run_tool(h2, ctx, "query_dataset", {"dataset": ds[0]["name"],
                                                    "group_by": [{"column": "account"}, {"column": "date", "grain": "month"}],
                                                    "measures": [{"column": "debit", "agg": "sum"}]})
            assert not o.text.startswith("Error"), o.text[:200]
            q.append(time.perf_counter() - t0)
        tool_lat[n_lines] = {"ingest_s": round(ingest, 2), "financial_kpis_cold_ms": round(cold[0] * 1000, 1),
                             "financial_kpis_warm": stats(warm), "query_dataset_group_by": stats(q)}
        print(f"  ledger {n_lines}: {tool_lat[n_lines]}")
        h2.close()
    out["tool_latency_by_ledger_lines"] = tool_lat

    # 5. retrieval latency against corpus size
    ret = {}
    for n_docs in ((20, 100) if QUICK else (20, 200)):
        h3 = Harness()
        ws = h3.setup_workspace(with_model=False)
        for i in range(n_docs):
            h3.api("POST", f"/api/w/{ws}/documents", files=[("files", (f"doc{i}.txt",
                   (f"Contract {i} with supplier {i}. Payment terms {30 + i % 60} days. Penalty {i}% per month. " * 80).encode()))])
        h3.settle(300)
        ctx = tool_ctx(h3, ws)
        times = []
        for i in range(30):
            t0 = time.perf_counter()
            o = run_tool(h3, ctx, "search_documents", {"query": f"payment terms supplier {i}"})
            times.append(time.perf_counter() - t0)
            assert "[1]" in o.text, o.text[:200]
        ret[n_docs] = stats(times)
        print(f"  retrieval {n_docs} docs: {ret[n_docs]}")
        h3.close()
    out["retrieval_latency_by_documents"] = ret
    out["rss_mb"] = {"start": round(rss0, 1), "end": round(rss_mb(), 1)}
    out["not_measured"] = ["time to first token", "model inference latency", "tokens per second", "GPU utilisation",
                           "multi-process / multi-host throughput"]
    save("e6_benchmarks", out)
    print("E6 saved")


if __name__ == "__main__":
    main()
