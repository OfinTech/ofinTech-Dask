# OfinTech Desk AI Agent: benchmarks

## Conditions

| | |
|---|---|
| Hardware | Cloud VM, 2 vCPU (Intel Xeon @ 2.10 GHz), 7 GB RAM, Linux 6.18 |
| Software | Python 3.13.18, engine at `main` `f8c58cd`, SQLite on local disk |
| Process | One engine process in-process with Starlette's TestClient: the same app and event loop as production, but without a network hop |
| Model | Scripted OpenAI-compatible endpoint on a mocked transport that **answers instantly**. Every figure below is the **engine's own overhead**: prompt building, tool selection, tool execution, storage, events and the figure check. |
| Commands | `python3 reports/ai-agent-audit/evals/e6_benchmarks.py` (300 turns per scenario) and `python3 reports/ai-agent-audit/evals/e9_memory_growth.py` |
| Raw results | `results/e6_benchmarks.json`, `results/e6_benchmarks.log`, `results/e9_memory_growth.json` |

**NOT MEASURED (no model provider available):** time to first token, model inference latency, tokens per second,
end-to-end latency with a real model, and GPU utilisation. With any hosted model, its own latency (typically
seconds) will dominate the engine's (milliseconds) shown here.

## 1. End-to-end turn, engine overhead (n = 300 per scenario)

Measured from the HTTP POST of the user's message to the run reaching `done`, with every tool result asserted to be
a success.

| Scenario | P50 | P95 | P99 | Max |
|---|---:|---:|---:|---:|
| Answer only (1 model call) | 6.6 ms | 8.8 ms | 15.1 ms | 66.8 ms |
| One calculator call (2 model calls) | 10.4 ms | 16.2 ms | 20.2 ms | 22.4 ms |
| KPI scorecard from a 2,000-line ledger | 16.4 ms | 24.9 ms | 31.5 ms | 158.7 ms |
| Document search over 20 documents | 12.4 ms | 20.7 ms | 24.9 ms | 188.4 ms |
| Three tool calls in one step | 19.9 ms | 30.2 ms | 36.3 ms | 339.6 ms |

## 2. Long conversation (120 turns in one thread, each with a tool call)

| | P50 | P95 |
|---|---:|---:|
| First 10 turns | 11.6 ms | 14.1 ms |
| Last 10 turns | 16.1 ms | 19.2 ms |

The cost per turn grows slowly, because history is capped at 60 messages and old tool results are cut to 600
characters. The request size stays bounded: in an 80-turn conversation with large inputs, the largest request was
47,704 characters (E3).

## 3. Concurrency (N conversations started at once, each with a KPI scorecard call)

| N | Wall time | Throughput |
|---:|---:|---:|
| 5 | 0.094 s | 53 turns/s |
| 20 | 0.328 s | 61 turns/s |

This is one process on 2 vCPU with an instant model. Real throughput is bounded by the model provider's rate
limits, not by this number.

## 4. Tool latency against data size

| Ledger lines | Ingest | `financial_kpis` cold | `financial_kpis` warm P50 / P95 | `query_dataset` (group by account and month) P50 / P95 |
|---:|---:|---:|---:|---:|
| 2,000 | 0.04 s | 37.4 ms | 6.4 / 9.8 ms | 2.9 / 3.9 ms |
| 20,000 | 0.68 s | 128.3 ms | 6.4 / 10.4 ms | 5.9 / 7.1 ms |
| 100,000 | 1.47 s | 91.6 ms | 6.3 / 8.3 ms | 4.8 / 5.3 ms |

| Documents indexed | `search_documents` P50 / P95 (n = 30) |
|---:|---:|
| 20 | 1.2 / 1.9 ms |
| 200 | 4.0 / 5.4 ms |

The scorecard is cached after the first call (cold to warm: 37–128 ms down to about 6 ms). Retrieval is
full-text, because no embeddings provider was configured.

## 5. Memory

| Conversations | Engine RSS (harness records cleared) | Finished agent tasks kept |
|---:|---:|---:|
| 250 | 106.5 MB | 250 |
| 750 | 108.4 MB | 750 |
| 1,500 | 109.1 MB | 1,500 |

The engine grows by **about 1.6 KB per conversation** after warm-up. That is consistent with `Agent.tasks`
keeping one finished `asyncio.Task` per thread forever (`agent/harness.py` lines 144 and 182–184; finding AI-19).
That is about 160 MB per 100,000 conversations in a long-running server process. It is a slow leak, not an
outage.

In E6 the RSS reached 478 MB after about 1,500 turns. That was the **test harness** (the scripted model and the
mocked transport keep every request) and not the engine; E9 separates the two.

## 6. Bottlenecks and inefficiencies found

| Observation | Evidence | Effect |
|---|---|---|
| Blocking work inside async tool handlers stalls the whole engine and defeats the tool timeout | E3 `tool_blocking_sync_work`: a tool doing 2 s of synchronous work held another request (`/health`) for 1.81 s, and the 0.5 s timeout did not fire. SQLite calls in handlers are synchronous. | Under real load, one heavy query (a large ledger, a large file parse) delays every conversation on the server. |
| No reuse of identical tool calls within a run | E3 `repeated_identical_tool_calls`: the same call was executed 6 times. | Wasted tool time and model tokens when a model loops (bounded by 16 steps). |
| Tool schemas are the largest part of a request | 25–26 tools at the median; `context.py` estimates about 8,000 tokens for all tools. | The existing tool-group selection already reduces this. Measuring its effect on token cost needs a live model. |
| The whole history is read from SQLite at every step | `history()` runs once per model call (`harness.py` line 307). | Measured cost is small (section 2). It is not a bottleneck at these sizes. |

## Proposed SLOs (to confirm after a live run)

- **Engine overhead per turn, excluding the model:** P95 < 100 ms. Measured sequentially, P95 is 8.8–30.2 ms, so this target is met. Under concurrency, only throughput was measured (20 conversations finished in 0.328 s in total), not P95, so the target is not yet shown to hold at 20 concurrent conversations.
- **End-to-end per turn, with the chosen model:** set after E8 is run live. Measure TTFT and total time per step, and record both (finding AI-12).
