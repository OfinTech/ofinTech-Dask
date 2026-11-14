# 05 · Load test results

## Environment and method

| | |
|---|---|
| Hardware | Linux VM, 2 vCPU Intel Xeon @ 2.10 GHz, 7 GB RAM, no GPU |
| Engine | this branch; one process; SQLite (WAL); Python 3.13.18 |
| Model | **scripted, zero latency.** These are engine and API figures only, **not actual inference** (E8 blocked) |
| Dataset | HarborLine demo general ledger (synthetic), one workspace |
| Tool | `reports/ai-agent-final-release/evals/l1_release_load.py` (threads through the engine's ASGI client: real routing, auth, agent loop, SQLite). k6 and Locust are not installable here (no network); the scenarios port to k6 against a staging server. |
| Limitation | Load generator and engine share one process and the GIL, so absolute throughput is a lower bound for the engine. Clients wait on run completion events, not by polling. |
| Raw results | `results/l1_release_load.json` |

## Scenario A — normal operation (25 users, 20 s)

Mix per action: 30% question, 30% KPI tool turn, 20% calculator turn, 15% dashboard KPIs, 5% dashboard insights.

| | |
|---|---|
| Actions | 1,788 (88.5 / s) |
| Errors | 0 |
| Latency all P50 / P95 / P99 | 178 / 280 / 330 ms |
| P95 by type | question 229, dashboard 231, insights 224, calculator 291, KPI turn 318 ms |
| CPU | 1.0 core (the single-process ceiling) |
| RSS | 185 MB |

## Scenario B — concurrency stages (same mix, 20 s each)

| Users | Actions | Actions/s | Errors | P50 / P95 / P99 ms | Peak active runs | RSS MB |
|---:|---:|---:|---:|---|---:|---:|
| 10 | 1,740 | 86.6 | 0 | 71 / 137 / 183 | 5 | 188 |
| 25 | 1,640 | 81.2 | 0 | 196 / 295 / 327 | 13 | 212 |
| 50 | 1,640 | 79.5 | 0 | 401 / 537 / 565 | 22 | 277 |
| 100 | 1,575 | 74.6 | 0 | 856 / 1,121 / 1,354 | 53 | 391 |

Throughput stays at about 75–87 actions/s, and latency grows linearly with users: the requests queue for one CPU
core. That is the capacity of one engine process on this hardware with an instant model. With a real model, run
time is dominated by the provider. The engine's share stays as above, and concurrency is then bounded by
provider rate limits, not CPU.

## Scenario C — peak (200 users arriving at once, 22 s)

| | |
|---|---|
| Actions | 1,646 (75.1 / s) |
| Errors and timeouts | 0 |
| Latency P50 / P95 / P99 | 1,712 / 2,264 / 2,410 ms |
| Peak concurrent runs (queue) | 95 |
| RSS | 617 MB (client threads' stacks included; flat afterwards) |

## Scenario D — long conversations (10 users × 60 turns in one conversation each)

| | |
|---|---|
| Turns | 600 |
| Errors | 0 |
| Latency P50 / P95 / P99 | 80 / 105 / 162 ms (no drift with conversation length) |
| Largest request in the last 200 calls | 10,753 characters (bounded by the history budget) |
| RSS growth during the scenario | 0.0 MB |

The budget's own enforcement on oversized histories is tested separately (`HistoryBudget`: 820,062 → 122,135
characters). Response quality over long conversations needs a live model (E8).

## Scenario E — failure and recovery (10 users, 10 s per phase)

| Failure injected | Actions | Turn failures | Error code (all) | P95 ms | After recovery: errors / P95 ms |
|---|---:|---|---|---:|---|
| Model returns 503 | 817 | 100% (expected) | `llm_unavailable` after 2 retries | 169 | 0% / 68 |
| Model times out | 774 | 100% (expected) | `llm_timeout` (outcome `timed_out`) | 178 | 0% / 75 |
| Network failure | 766 | 100% (expected) | `llm_network` | 179 | 0% / 73 |
| KPI tool hangs 3 s, timeout 1 s | 100 | 0% (the turn continues; the tool error goes to the model) | — | 1,057 | — |
| Every DB read delayed by 2 ms | 90 | 0% | — | 1,895 | 0% / 217 (recovery phase) |

- During outages runs fail fast, with honest error codes, and nothing is stored as an answer. Recovery is immediate after the fault clears: no stuck runs or leaked tasks, and connections are back to 20.
- The metrics recorded every failure (`results/l1_release_load.json` → `metrics_after`):

  | Counter | Value |
  |---|---:|
  | `llm_unavailable` / `llm_timeout` / `llm_network` | 817 / 774 / 766 |
  | Retries | 1,634 / 1,548 / 1,532 |
  | Tool errors (`financial_kpis`) | 100 |

- **Bottleneck confirmed:** delaying each database read by 2 ms raised turn P95 from about 0.2 s to 1.9 s. The agent loop's own per-step reads run on the event loop and serialise across conversations (RT-03). This is the next optimisation candidate. It was not changed here: a healthy SQLite read takes microseconds, and the change touches the core loop.

## Bottlenecks and resource findings

| Finding | Evidence | Action |
|---|---|---|
| One process is CPU-bound at about 1 core | B / C: flat throughput, linear latency | run more instances only after AI-21 (single-instance recovery) is addressed; or a process pool for analysis |
| Agent loop's DB reads on the event loop | E: 2 ms per read → P95 1.9 s | follow-up (09) |
| **Connections of ended threads were never closed** (fixed) | first run: 497 open connections, RSS rising | fixed in `db.py` (`b69c3da`), test added; full run: 20 connections |
| Harness artefacts found and removed | fake-model request logs; DB polling by clients | documented in the script |

## Monitoring overhead

| | PR #71 | This branch |
|---|---|---|
| E6 answer-only P50 / P95 | 6.5 / 10.1 ms | 6.9 / 9.5 ms |
| E6 KPI scorecard call P50 | 16.8 ms | 17.9 ms |
| E6 RSS after the benchmark | 466 MB | 483 MB |

Instrumentation (two timers per DB call and per request) costs under about 1 ms per turn, within run-to-run noise.
It adds no queries.
