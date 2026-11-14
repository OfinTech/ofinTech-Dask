# 05 · Event-loop optimisation

## Root cause (profiled, not assumed)

`evals/p3_loop_db_profile.py` attributes every database call to its thread and caller over 20 tool turns. On PR
#72's head, **60 reads per turn ran on the event-loop thread**:

| Source | Reads per turn |
|---|---:|
| Tool availability checks (`connector_tools.check` 12, `list_datasets` 6, `list_skills` 4, `_has*` 8) | ~30 |
| Prompt context in `_run` (skills, pins, connections, datasets, collections, memories, language) | 11 |
| History (once per model call) | 2 |
| Event payloads (`_message_out`, `run_out`) | 7 |
| Writes (record_usage, tool message, audit) | — |

On a healthy database these 60 reads take about 4 ms per turn. Every millisecond they wait on a slow database,
though, is a millisecond every other conversation and request waits. L1 (PR #72) measured P95 1.9 s with 2 ms per read.

## Change (smallest that moves the reads)

| File | Change |
|---|---|
| `agent/tools.py` | `for_context` runs the built-in availability checks (`_static_for`) in a worker thread. MCP providers stay on the loop: they use its network clients. |
| `agent/harness.py` | The prompt context moves into `_prompt_context`, which runs in a worker thread. `history()` runs in a worker thread at each step. Writes, events, model calls and approvals stay on the loop. |
| `app.py` | `Engine.read_pool`: a dedicated 8-thread pool for these reads, separate from the default pool that offloaded tools use. `Engine.read()` runs a read there. `DESK_AGENT_OFFLOAD_READS=0` keeps the reads on the loop. |
| `app.py` | Shutdown drains the read pool and the default pool, within 10 s, before closing the database. If they do not finish, it leaves connections to the process exit. |

SQLite safety is unchanged:
- Each thread uses its own connection, which `Database` already provides; no connection object crosses threads.
- Writes still take the existing process-wide write lock.
- Connections of ended threads are closed (RT-05, PR #72).

## Two defects found and fixed in this branch before release

| # | Found by | Defect | Fix | Regression test |
|---|---|---|---|---|
| 1 | full suite (segfault) | Shutdown closed a SQLite connection while a worker thread was executing on it, which crashes the process. The more work in threads, the likelier this is. | Drain the pools before `db.close()`; skip the close on timeout. | `test_shutdown_race`: 25 shutdowns at different points of a run, in a subprocess, with real SQL in flight. Fails with signal 11 before the fix. |
| 2 | L1 load test (tool-timeout stage 9.6 → 2.5 actions/s) | The agent's reads shared the default pool with hung tools and starved behind them. | Dedicated read pool | `HungToolsDoNotStarveOthers`: a plain question waited 2.71 s behind 12 hung tool calls before the fix; now under 1 s |

On PR #72's head the shutdown race test passes 3 of 3. Both defects were introduced by this branch's first version
and fixed before review.

## Regression tests

| Test | Covers |
|---|---|
| `test_loop_reads.LoopReads` (2) | ≤ 26 reads per turn on the loop (was 57); a slow history, connections or datasets read no longer stalls `/health` (was 1.36 s) |
| `HungToolsDoNotStarveOthers` | thread-pool saturation |
| `OffloadSwitch` | the off switch |
| `test_shutdown_race` | process shutdown with reads in flight |
| Existing suites | concurrency, cancellation (lifecycle), tool failures, cross-workspace isolation (E5 0 / 657), connection closure (`ConnectionsOfEndedThreads`), offload connections bounded |

Full suite: **574 / 574**, twice with faulthandler and no crash.

## Before / after

**P3 profile** (20 tool turns):

| | Before | After |
|---|---:|---:|
| Reads per turn on the event loop | 60 | 22 |
| Their time (healthy database) | 3.9 ms | 2.4 ms |

**P4** (20 concurrent tool turns for 15 s; `/health` probed every 20 ms; 2 vCPU; scripted model; same session).
"Off" means after the change, with `DESK_AGENT_OFFLOAD_READS=0`.

| Condition | Measure | Before (PR #72) | After | Off |
|---|---|---:|---:|---:|
| **2 ms per DB read** | turns/s | 7.0 | **15.8** | 6.9 |
| | turn P50 / P95 | 2,651 / 2,764 ms | **1,045 / 1,153 ms** | 2,708 / 2,811 ms |
| | `/health` P95 / max | 2,315 / 2,363 ms | **363 / 428 ms** | 2,336 / 2,378 ms |
| **Healthy database** | turns/s | **110.3** | 82.4 | 101.4 |
| | turn P50 / P95 | **141 / 172 ms** | 197 / 245 ms | 152 / 197 ms |
| | `/health` P95 / max | 115 / 153 ms | **70 / 132 ms** | 133 / 210 ms |
| | CPU cores | 0.97 | 1.07 | 0.96 |
| | DB connections at the end | 2 | 10 | 2 |

**L1** (full release load test, same script and machine; PR #72 → this branch):

| Stage | Actions/s | Errors | P95 |
|---|---|---|---|
| A, normal mix at 25 users | 88.5 → 73.1 | 0 → 0 | 280 → 395 ms |
| B, 100 users | 74.6 → 64.1 | 0 → 0 | 1.12 → 1.61 s |
| C, 200 users | 75.1 → 62.4 | 0 → 0 | 2.26 → 3.24 s |
| E, tool timeouts | 9.6 → 9.2 | 0 → 0 | 1.06 → 1.13 s |
| E, 2 ms per DB read | **8.3 → 13.5** | 0 → 0 | **1.90 → 1.43 s** |

Model-failure stages: handled the same (honest failures, immediate recovery). Memory: unchanged.

## Verdict

- **The fix does what it is for.** A slow database no longer stalls every conversation: about 2.3× the throughput, `/health` P95 6× better, turn P95 2.4× better in P4.
- **It costs CPU on a healthy database with an instant model.** Throughput is 12–25% lower and P50 about 40% higher. That is GIL contention from handing work to threads, measured with the load generator in the same process.
- **With a real model this trade favours the change.** A real model's latency is seconds per call, so about 3 ms more CPU per turn is negligible, while a stalled loop delays every user. Operators can turn it off (`DESK_AGENT_OFFLOAD_READS=0`), which restores PR #72's behaviour (P4 "off" column).
- **Remaining on the loop:** 22 reads per turn (run state, event payloads) and all writes. A slow database still costs those milliseconds on the loop. Moving the writes would need write-path changes, which are out of scope for "smallest safe change".
