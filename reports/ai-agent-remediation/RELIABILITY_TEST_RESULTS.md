# Reliability test results

Covers AI-03, AI-06, AI-07, AI-08, AI-10, AI-11, AI-18, AI-19. Source: E3 (`results/{before,after}/e3_reliability.json`),
E9, and the new unit tests.

## E3: 17 failure scenarios

| Scenario | Expected | `main` | This branch |
|---|---|---|---|
| model_429_rate_limited | retried with backoff, or a clear failure | pass (1 call, generic error) | pass (3 calls, then `llm_rate_limited`) |
| model_500_then_ok | one transient 500 retried, turn completes | **fail** | pass (2 calls, done) |
| model_timeout | clear message, no hang, no partial answer | **fail** ("Unexpected error: Rea…") | pass ("Fake did not answer in time", `llm_timeout`) |
| stream_cut_before_done | not stored as a complete answer | **fail** (stored as done) | pass (`llm_incomplete`, nothing stored as final) |
| finish_reason_length | marked incomplete | **fail** (stored as done) | pass (kept, `incomplete: true`, `answer_incomplete`) |
| malformed_sse_chunk | reported, not silently dropped | **fail** (stored as done) | pass (`llm_malformed`) |
| empty_model_reply | not stored as the answer | **fail** | pass (`answer_empty`) |
| invalid_json_arguments | error to model, run recovers | pass | pass |
| unknown_tool | refused, audited, run continues | pass | pass |
| schema_violation | refused before the tool runs | pass | pass |
| tool_raises_unexpected_exception | error to the model, turn not lost | **fail** (run lost) | pass |
| tool_timeout_async | stopped at its timeout | pass | pass |
| tool_blocking_sync_work | cannot stall other conversations | **fail** (other request waited 1.81 s) | pass (0.0 s) |
| runaway_loop | stopped after MAX_STEPS | pass | pass |
| repeated_identical_tool_calls | not executed again and again | **fail** (6 executions) | pass (2 executions) |
| long_conversation_80_turns | bounded size, valid call/result pairing | pass | pass |
| early_fact_retained | early fact still reaches the model | **fail** | pass |
| **Total** | | **7 / 17** | **17 / 17** |

## What changed

| Area | Behaviour now |
|---|---|
| Completion evidence (AI-03) | OpenAI-compatible streams must end with `[DONE]` or a `finish_reason`; Anthropic streams with `message_stop`. Without it: `llm_incomplete`. `length` and `content_filter` finishes keep the text, mark it `incomplete`, and end the run as `answer_incomplete`. The app shows "This answer is incomplete and must not be relied on." |
| Malformed stream parts (AI-08) | counted; any → `llm_malformed` |
| Error classes (AI-06) | `llm_auth` (401/403), `llm_rate_limited` (429), `llm_not_found` (404), `llm_timeout` (408 / transport timeout), `llm_unavailable` (5xx), `llm_network`, `llm_error` |
| Retries (AI-06) | only `llm_rate_limited`, `llm_timeout`, `llm_unavailable`, `llm_network`, `llm_incomplete`, `llm_malformed`; at most `DESK_LLM_RETRIES` (default 2, max 5); exponential backoff from `DESK_LLM_RETRY_BASE_S` (1 s) with jitter, capped by `DESK_LLM_RETRY_MAX_WAIT_S` (20 s); `Retry-After` honoured. **Never** after text was streamed to the user, never for tools, never for permanent errors. |
| Run outcome (AI-12) | each run stores `outcome` and `error_code`; cancellation sets `cancelled`. Each answer stores `timing` (`ttfb_ms`, `total_ms`, `attempts`) and `finish`. |
| Tool exceptions (AI-07) | returned to the model as "Error: {tool} failed unexpectedly ({type}). The details are in the server log"; the exception is logged, not sent |
| Blocking calculators (AI-10, partial) | the ten pure `calc_*` tools run in a worker thread, so their timeout holds and the event loop stays free |
| Repeated identical reads (AI-18) | beyond the second identical read call in a run, the run's last result is reused; writes are never reused |
| Long conversations (AI-11, partial) | at the 60-message window, the user's own earlier messages (≤ 20, ≤ 300 chars each, ≤ 3,000 total) are carried in a user-role note |
| Task release (AI-19) | finished agent tasks are released by a done-callback |

## Memory growth (E9)

| Conversations | `main` tasks kept / RSS | This branch tasks kept / RSS |
|---:|---|---|
| 250 | 250 / 107.5 MB | 0 / 107.5 MB |
| 750 | 750 / 109.4 MB | 0 / 108.8 MB |
| 1,500 | 1,500 / 110.1 MB | 0 / 108.8 MB (flat from 750) |

## Unit tests added

| File | Tests | Covers |
|---|---:|---|
| `test_response_lifecycle.py` | 18 | both adapters: completion evidence, malformed parts, finish mapping, error classes, retry policy, no retry after text, cancellation, tool exceptions, outcomes |
| `test_agent_resources.py` | 5 | task release, repeated reads, writes never reused, early messages kept, calculators off the loop |

## Still open

- Tools that touch the database still run on the event loop (AI-10, partial).
- No token budget or summary of earlier answers and tool results (AI-11, partial).
- Restart recovery assumes one instance (AI-21).
- All scenarios use a scripted model; real provider behaviour (e.g. how often streams are cut) is not measured.
