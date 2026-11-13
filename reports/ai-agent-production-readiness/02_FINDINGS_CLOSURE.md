# 02 · Findings closure (AI-01 to AI-20)

Identifiers, severities and descriptions are the audit's own (`reports/ai-agent-audit/`, PR #68). "After PR #70" is the
remediation report's status (`reports/ai-agent-remediation/`). "Now" is this branch, re-verified on
`fix/ai-agent-production-readiness`. A finding is **Resolved** only where a test reproduces the defect on the older
code and passes here.

| ID | Sev. | Audit description (short) | After PR #70 | Now | Evidence |
|---|---|---|---|---|---|
| AI-01 | HIGH | Disabled or unapproved pinned skills reach the system role | Resolved | **Resolved, re-verified** | `test_skill_authorization` (10), E7 P2, P3 4/4 |
| AI-02 | HIGH | An editor's edit keeps an admin-approved skill enabled | Resolved | **Resolved, re-verified** | permission matrix, AI-02 test, E7 P1 |
| AI-03 | HIGH | Cut, empty or malformed model output stored as a complete answer | Resolved (agent loop) | **Resolved in every model path** (new: documents answer, dashboard insights, file reviewer) | `test_response_lifecycle` (18), `EveryModelReplyIsCheckedForCompleteness` (5, all fail on `main`), E3 |
| AI-04 | HIGH (process) | No live-model evaluation | Blocked | **Blocked** | 03_LIVE_MODEL_EVALUATION.md |
| AI-05 | MED | Figure check misses near misses, units, user figures | Resolved | Resolved (unchanged) | E10: precision 100%, recall 99.83% (re-run) |
| AI-06 | MED | No retries or error classes | Resolved | Resolved (unchanged) | E3 `model_500_then_ok`, `model_timeout` |
| AI-07 | MED | A tool's exception loses the turn | Resolved | Resolved (unchanged) | E3 |
| AI-08 | MED | Malformed stream parts dropped silently | Resolved | Resolved (unchanged) | E3 |
| AI-09 | MED | File and import names in the system role | Resolved | Resolved (unchanged) | E7 P4 |
| AI-10 | MED | Blocking work in tool handlers stalls every conversation and defeats timeouts | Partially resolved (calculators only) | **Resolved for tools** | `DatabaseToolsOffTheLoop` (3), `OffloadResources`; P1 `slow_db_read@50`: `/health` max 10,204 ms → 97 ms |
| AI-11 | MED | Fixed 60-message window, no token budget or summary | Partially resolved (user messages kept) | **Resolved** (budget + note of earlier answers; estimate by characters) | `HistoryBudget` (3); 60 maximum-size messages: 820,062 → 122,135 characters |
| AI-12 | MED | No latency, TTFT, finish, retry or error recording; no metrics | Partially resolved (timing in payloads) | **Resolved in code** (metrics endpoint, run trace); alerting is a deployment task, not done | `MetricsAndTrace` (5) |
| AI-13 | LOW | One IRR returned for flows with several | Resolved | Resolved (unchanged) | E1 1,385 / 1,385 (re-run) |
| AI-14 | LOW | IRR/XIRR above 1,000% not found | Resolved | Resolved (unchanged) | E1 |
| AI-15 | LOW | Growth from negative or zero bases | Resolved | Resolved (unchanged) | E1 |
| AI-16 | LOW | Rates given as percentages applied silently | Resolved | Resolved (unchanged) | E1 |
| AI-17 | LOW | Missing month silently substituted | Resolved | Resolved (unchanged) | E2 86 / 86 (re-run) |
| AI-18 | LOW | Identical read calls run again and again | Resolved | Resolved (now also counted in metrics) | E3; `test_cached_repeat_calls_are_counted` |
| AI-19 | LOW | Finished tasks retained | Resolved | Resolved (unchanged) | E9: 0 tasks kept after 1,500 conversations, RSS flat at 108 MB |
| AI-20 | LOW | Ratio calculator mutates its input | Resolved | Resolved (unchanged) | E1 |

AI-21 to AI-23 (INFO) are outside this report's range and unchanged. See 09_REMAINING_RISKS.md.

## AI-10, AI-11, AI-12 in detail

### AI-10: blocking work in tool handlers

| | |
|---|---|
| Original defect | Synchronous work inside `async` tool handlers runs on the event loop: other conversations wait, and `asyncio.wait_for` cannot interrupt it, so the tool timeout never fires (E3: another request waited 1.81 s). |
| Root cause | Handlers are coroutines but do SQLite reads (and some file work) synchronously; only the KPI and cost-centre services handed their heavy parts to threads. |
| Done in PR #70 | The ten pure `calc_*` tools run in a worker thread. |
| Remaining before this branch | Every database-reading tool still ran on the loop. Reproduced with a real tool (`list_datasets`) and a slowed read: the loop stalled for 0.80 s, and a 0.3 s timeout returned after 1.5 s with the full result. |
| Fix | `Tool.offload`: 18 read-only, local tools run in a worker thread on that thread's own event loop. Tools with network clients (MCP, Google, accounting, embedding search, file download) and every write stay on the loop; `registry.tool` refuses `offload` on a write tool. Each worker thread gets its own SQLite connection (the existing per-thread connection design); writes stay serialised by the existing lock. |
| Regression found and fixed | The first version ran `asyncio.run` per call, so every call opened a new thread and SQLite connection for nested thread work (60 calls → 60 connections; E6 RSS 481 → 640 MB). One loop per worker thread fixed it (E6 RSS 466 MB; `test_connections_stay_bounded`). |
| Proof | The stall is 0.80 s on `main` and under 0.3 s here; the timeout now returns within 1 s. P1, with 50 conversations each running a 200 ms query: `/health` p95 / max 10,204 / 10,204 ms (only 2 probes got through in the stage) → 6.7 / 97 ms (76 probes), throughput 4.8 → 27.1 turns/s. A red-team probe checks that tenants are never mixed under concurrency (40 parallel calls across two workspaces). |
| Not covered | The agent loop's own per-step reads (history, prompt context, memories) still run on the loop. They take milliseconds on a healthy database (E6 answer-only turn: 6.3 ms), but a locked or very slow database would stall them. CPU-heavy work in threads still contends for the GIL: at 50 concurrent KPI turns, `/health` p95 is 598 ms on this branch and 435 ms on `main`, from 4 and 3 probes. |

### AI-11: context budget

| | |
|---|---|
| Original defect | A fixed 60-message window, no token budget and no summary; a turn-1 fact was lost after about 15 turns. |
| Done in PR #70 | The user's own earlier messages are carried in a note when the window moves on. |
| Remaining before this branch | No size budget. Sixty maximum-size messages (20,000-character user messages, 8,000-character answers) produced an 820,062-character request (about 205,000 tokens), above the context window of many models. Nothing of the assistant's earlier answers survived. |
| Fix | `history_budget_chars()`: a budget of `DESK_LLM_HISTORY_TOKENS` (default 30,000) × 4 characters, newest first. The current turn is always kept whole, and tool results are counted at the size `compact()` actually sends. When anything is dropped, one note carries the user's first 5 and latest 15 messages (up to 3,000 characters) and the assistant's first 2 and latest 4 answers (240 characters each). Answers are marked "not re-checked against the books"; tool results are not carried. |
| Proof | The same thread now sends 122,135 characters (≈ 30,500 tokens), with the turn-1 fact and earlier answers in the note. A short thread is sent whole. E3 `long_conversation_80_turns` and `early_fact_retained` pass. |
| Limits | Tokens are estimated from characters (4 per token), not counted with each model's tokenizer, and there is no per-model context-window setting. The note truncates; it does not summarise meaning. |

### AI-12: observability

| | |
|---|---|
| Original defect | No recording of model or tool latency, TTFT, finish reason, retries or error category; no metrics endpoint. |
| Done in PR #70 | Per-answer `timing`, `attempts` and `finish`; per-run `outcome` and `error_code`. |
| Remaining before this branch | No metrics endpoint, no tool durations, no way to follow a run's steps. |
| Fix | `desk_engine/metrics.py`, one registry per engine. It records model calls by outcome, retries by error class, model call duration and TTFT histograms, tool calls by status (`ok`, `error`, `refused`, `reused`), tool duration, run outcomes, and event-loop lag (a 0.25 s timer). `GET /api/admin/metrics` serves them in Prometheus text, to the instance administrator only. Labels are closed vocabularies: every MCP tool is `mcp`, and no workspace, user or thread id appears. Tool messages now carry `ms`. `GET /api/w/{ws}/threads/{tid}/trace` lists a run's model and tool steps under the thread's own access rules. |
| Proof | `MetricsAndTrace` (5 tests) checks exact counter lines, histograms, the 403 for non-administrators, that no tenant id appears, and the trace order. Red-team probes check trace isolation across workspaces and bounded labels. |
| Not done | Alert rules and their delivery (needs the operator's Prometheus/Alertmanager), OpenTelemetry export, dashboards. Metrics are in-process and reset on restart. Proposed alert rules are in 07_PRODUCTION_GATES.md. |
