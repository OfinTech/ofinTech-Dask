# 06 · Performance benchmarks

## Environment and method

| | |
|---|---|
| Machine | Linux VM, 2 vCPU Intel Xeon @ 2.10 GHz, 7 GB RAM; no GPU |
| Runtime | Python 3.13.18, SQLite 3.53.1 (WAL), single process |
| Model | **Scripted, zero latency.** Every figure is the engine's own overhead. LLM inference time and real TTFT are **not measured** (E8 blocked). |
| Before | `main` at `b9d0161` (a separate worktree, same machine, same session) |
| After | this branch |
| Reproduce | `cd engine && python3 ../reports/ai-agent-production-readiness/evals/p1_staged_load.py out.json`; E6 and E9 in `reports/ai-agent-audit/evals/` |

## P1: staged concurrency (new)

Each stage runs n conversations at once, at 1, 5, 20 and 50 (justified by 2 vCPU). Turn time is from the run's
creation to its end. `/health` is probed every 20 ms during the stage; its latency is what any other user waits.
The slow-database workload delays the dataset tool's own query by 200 ms.

| Workload @ n | Turn P50 / P95 / P99 ms, `main` | Same, branch | Turns/s `main` → branch | `/health` P95 / max ms, `main` → branch | Errors |
|---|---|---|---|---|---|
| Answer @ 50 | 46 / 110 / 116 | 25 / 54 / 58 | 210 → 244 | 116 / 116 → 81 / 81 | 0 |
| Calculator @ 50 | 219 / 261 / 266 | 202 / 234 / 237 | 113 → 112 | 290 / 290 → 228 / 228 | 0 |
| KPI scorecard @ 20 | 165 / 178 / 182 | 183 / 242 / 246 | 71 → 64 | 120 / 120 → 189 / 189 | 0 |
| KPI scorecard @ 50 | 497 / 563 / 579 | 490 / 544 / 553 | 59 → 59 | 435 / 435 → 598 / 598 | 0 |
| **Slow DB read @ 5** | 211 / 416 / 416 | 209 / 219 / 219 | **4.8 → 21.0** | **611 / 611 → 1.3 / 4.7** | 0 |
| **Slow DB read @ 20** | 1,874 / 3,522 / 3,728 | 412 / 747 / 763 | **4.8 → 23.7** | **3,931 / 3,931 → 5.6 / 55** | 0 |
| **Slow DB read @ 50** | 2,514 / 7,272 / 7,683 | 917 / 1,486 / 1,666 | **4.8 → 27.1** | **10,204 / 10,204 → 6.7 / 97** | 0 |

All stages, before and after, ended without errors. The full table is in `results/{before,after}/p1_staged_load.json`.

How to read it:
- On `main`, a slow query in one conversation froze the server: during the 50-conversation stage, only 2 health probes got through, and one waited 10.2 s. With the query in a worker thread, the server stays responsive (76 probes, P95 6.7 ms), and throughput is bounded by the thread pool rather than serialised.
- In the fast stages, few probes complete (n = 1–4), so their `/health` columns are indicative only. The KPI scorecard's work is CPU-bound Python in threads; it contends for the GIL whether it runs in a thread on `main` (scorecard computation) or here. Its `/health` latency is similar at 50 and higher at 20 on this branch. That is within this benchmark's noise, but not shown to be equal.

## E6: engine overhead per turn (n = 300 per scenario)

The comparison is with `main` (`reports/ai-agent-remediation/results/after/e6_benchmarks.json`, same code as `main`).

| Scenario | P50 ms `main` → branch | P95 ms `main` → branch |
|---|---|---|
| Answer only | 6.4 → 6.5 | 9.0 → 10.1 |
| One calculator call | 10.1 → 9.8 | 14.7 → 15.0 |
| KPI scorecard call | 17.1 → 16.8 | 25.2 → 26.1 |
| Document search call | 14.6 → 14.5 | 23.8 → 23.0 |
| Three tool calls | 22.9 → 21.5 | 34.8 → 30.3 |
| 20 concurrent (turns/s) | 66.3 → 62.8 | — |
| Request size P50 (characters) | 29,697 → 29,697 | — |
| RSS after the benchmark | 481.3 MB → **465.7 MB** | — |

With healthy queries, offloading adds no measurable overhead. An intermediate version of this branch reached
640 MB RSS: it leaked one SQLite connection per offloaded call. That was found here, fixed, and guarded by a test
(05, RT-02).

## E9: memory growth

| Conversations | `main` RSS / tasks kept | Branch RSS / tasks kept |
|---:|---|---|
| 250 | 107.5 MB / 0 | 106.8 MB / 0 |
| 1,500 | 108.8 MB / 0 | 108.1 MB / 0 |

## Bottlenecks reviewed

| Item | Finding |
|---|---|
| Blocking operations | Tool DB reads: fixed (AI-10). The agent loop's own per-step reads (history, context): still on the loop, a few ms each on a healthy DB (RT-03). |
| Memory growth | None (E9); the connection leak introduced mid-review was fixed. |
| Unbounded task retention | None (E9: 0 tasks kept). |
| Inefficient context | Bounded by the new history budget; the request is unchanged for normal threads (E6 request size identical). |
| Redundant tool calls | Repeated identical reads reused after the second (AI-18); now visible as `status="reused"` in metrics. |
| Unnecessary model invocations | Retries only for transient errors, before any text, at most 2 (AI-06); counted in `desk_llm_retries_total`. |
| Cleanup failures | Connections per worker thread bounded (test); tasks released (E9). |

## Proposed SLOs (NOT approved; engine overhead with an instant model, 2 vCPU)

| SLO | Proposed | Measured (branch) | Meets? |
|---|---|---|---|
| Engine overhead per turn, P95, 20 concurrent, any workload | ≤ 250 ms | 34 / 82 / 242 / 747 ms (answer / calculator / KPI / slow DB) | Yes, except the 200 ms-per-query slow-DB stress case |
| `/health` P95 while 20 conversations run | ≤ 100 ms | 38 / 98 / 189 / 5.6 ms | No for KPI scorecard (CPU-bound) |
| Errors under load | 0% | 0% | Yes |
| Memory growth per conversation | flat | flat | Yes |

These are proposals for the owner; the production gate (07) treats performance as **not tested against an agreed SLO**.
