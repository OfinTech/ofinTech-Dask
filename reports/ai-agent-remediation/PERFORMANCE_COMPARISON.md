# Performance comparison

E6 (`results/{before,after}/e6_benchmarks.json`) on the same machine, back to back: Linux VM, 2 vCPU Intel Xeon
@ 2.10 GHz, 7 GB RAM, Python 3.13.18. The model is scripted with zero latency, so these figures are **engine overhead
only**. Real end-to-end latency and time to first token depend on the provider and were not measured
(see LIVE_MODEL_EVALUATION.md). n = 300 turns per scenario.

## End-to-end turn (engine overhead)

| Scenario | P50 before → after (ms) | P95 before → after (ms) | P99 before → after (ms) |
|---|---|---|---|
| Answer only | 6.4 → 6.4 | 8.4 → 9.0 | 14.8 → 14.5 |
| One calculator call | 9.1 → 10.1 | 15.3 → 14.7 | 19.0 → 20.4 |
| KPI scorecard call | 16.0 → 17.1 | 25.1 → 25.2 | 30.9 → 30.6 |
| Document search call | 12.2 → 14.6 | 18.0 → 23.8 | 23.7 → 30.6 |
| Three tool calls | 19.3 → 22.9 | 29.0 → 34.8 | 33.9 → 52.4 |

## Throughput, size, memory

| Measure | Before | After |
|---|---:|---:|
| 5 concurrent conversations | 67.7 turns/s | 54.5 turns/s |
| 20 concurrent conversations | 60.5 turns/s | 66.3 turns/s |
| Request size p50 (characters) | 29,341 | 29,697 (+1.2%) |
| Approx. tokens p50 | 7,335 | 7,424 |
| Long thread, last 10 turns P50 | 14.7 ms | 17.4 ms |
| RSS after the benchmark | 473.3 MB | 481.3 MB |
| Agent tasks kept after 1,500 conversations (E9) | 1,500 | **0** |
| Memory growth per conversation (E9) | 1.7 KB | 0.6 KB, flat after 750 |
| Other request blocked by a slow calculator (E3) | 1.81 s | **0.0 s** |

## Reading

- The added work per turn (figure provenance, completion checks, timing, the workspace-context message) costs
  roughly 0–4 ms at P50 on this 2-vCPU machine. Against model latency (typically hundreds of ms to seconds) this is small.
- Single-run benchmarks on a shared VM vary by several ms; the 5-conversation drop and the 20-conversation rise
  move in opposite directions and are within that noise. They should not be read as a trend.
- The worst-case gains matter more than the medians: a blocking calculator no longer stalls other users, and
  finished tasks no longer accumulate.
- The workspace-context message adds about 350 characters per request.

Retries add latency only when the provider fails; at most `DESK_LLM_RETRIES` (2) extra calls, each waiting at most
`DESK_LLM_RETRY_MAX_WAIT_S` (20 s).
