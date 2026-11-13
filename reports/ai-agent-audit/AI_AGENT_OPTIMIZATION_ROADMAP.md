# OfinTech Desk AI Agent: optimisation roadmap

Each item names its finding (see AI_AGENT_SECURITY_AUDIT.md and AI_AGENT_EVALUATION.md), the evidence, the
change, and the test that proves it. Effort is engineering time for one person who knows the code. No change here
has been made. This audit modifies no application code.

## P0: must fix before production

### P0-1 Pinned skills must be enabled skills (AI-01, HIGH)
- **Current issue:** `Agent._run` pins any named skill (`agent/harness.py` 282–286), and `get_skill` ignores `enabled` (`skills/loader.py` 152–157). A viewer can place a disabled or unapproved skill's text in the system prompt.
- **Evidence:** E7 P2 and P3.
- **Proposed solution:** pin only names in `enabled_index`. Drop and audit the others. Reject them in `POST/PATCH /threads`.
- **Expected impact:** closes a path for unreviewed text into the system role.
- **Effort:** 0.5 day.
- **Risk of change:** low. A thread pinned to a since-disabled skill silently loses it, which is what disabling means.
- **Verification:** E7 P2 and P3 pass; a unit test asserts that `system_parts` never receives a disabled skill.

### P0-2 An editor's edit returns a skill to admin review (AI-02, HIGH)
- **Current issue:** `update_skill` keeps `enabled` as it was (`routes/skills.py` 62–75; `loader.py` 179–182).
- **Evidence:** E7 P1.
- **Proposed solution:** when the editor is below admin, set `enabled = 0` on update and notify admins. Alternatively, keep the approved body live and store the edit as a pending revision. Audit both.
- **Expected impact:** the approval rule in `_may_enable` holds for edits too.
- **Effort:** 0.5–1.5 days (the second option needs a revision table).
- **Risk of change:** low to medium. The UI must show "awaiting approval".
- **Verification:** E7 P1 passes; an RBAC test asserts that an editor's PUT leaves the skill disabled.

### P0-3 Never store an incomplete model output as a complete answer (AI-03, HIGH)
- **Current issue:**
  - `OpenAICompatible.stream` returns a result even when the stream ended without `[DONE]` or a `finish_reason` (`llm/openai_compat.py` 136–183).
  - `Agent._run` ignores `result.finish` (`harness.py` 317–345).
  - A truncated reply (`length`) or an empty reply becomes the final answer with status `done`.
- **Evidence:** E3 `stream_cut_before_done`, `finish_reason_length` and `empty_model_reply`. In the first, a half-sentence revenue answer was stored as complete.
- **Proposed solution:**
  - Track whether the stream ended properly, and raise a categorised `UpstreamError("incomplete_stream")` if not.
  - On `finish == "length"`, either continue once ("continue from where you stopped") or store the text marked `incomplete: true` with a visible notice.
  - Treat an empty reply as an error. Apply the same logic in `llm/anthropic.py` (`stop_reason == "max_tokens"`).
- **Expected impact:** no cut-off financial answer is presented as finished.
- **Effort:** 1 day.
- **Risk of change:** low.
- **Verification:** the three E3 scenarios pass, and the same cases pass for the Anthropic adapter.

### P0-4 Measure the live model before claiming accuracy (AI-04, HIGH, process)
- **Current issue:** no evaluation of a real model's answers exists in this repository. Every accuracy result so far is about the engine.
- **Evidence:** AI_AGENT_EVALUATION.md section 7: 5 of 10 Phase 9 metrics are NOT TESTED.
- **Proposed solution:** run `evals/e8_live_model.py --live --repeat 3` for each model offered to customers. Record the results in `results/`, and set the acceptance thresholds (tool selection ≥ 98%, figure accuracy 100% on these scenarios, no unverified figures). Re-run on every model or prompt change, nightly or before release (not on every push, because of cost).
- **Expected impact:** the first evidence of answer quality.
- **Effort:** 1 day plus provider cost (18 scenarios × 3 repeats is a few dollars per model).
- **Risk of change:** none to the product.
- **Verification:** the results file exists and the gate is enforced in the release checklist.

## P1: high-impact improvements

### P1-1 Retries, backoff and error categories for model calls (AI-06, MEDIUM)
- **Current issue:** there is no retry on 429, 5xx or timeouts (`llm/openai_compat.py` 136–138). A timeout surfaces as "Unexpected error: ReadTimeout" (`harness.py` 230–232).
- **Evidence:** E3 `model_500_then_ok` and `model_timeout`.
- **Proposed solution:** up to 2 retries with jittered backoff, honouring `Retry-After`, only *before the first byte is streamed*, so a partial answer is never replayed. Map `httpx` errors to categories (`timeout`, `network`, `rate_limited`, `upstream`) with readable messages. Add a total per-step deadline as well as the per-read timeout.
- **Expected impact:** transient provider errors stop costing users a turn.
- **Effort:** 1 day.
- **Risk of change:** low. Retry only idempotent model calls, never tools.
- **Verification:** E3 `model_500_then_ok` and `model_timeout` pass; a test proves no retry happens after streaming has started.

### P1-2 A tool's unexpected exception becomes a tool error, not a lost turn (AI-07, MEDIUM)
- **Current issue:** `Tool.run` catches only some exception types (`agent/tools.py` 220–223).
- **Evidence:** E3 `tool_raises_unexpected_exception` (database locked lost the turn).
- **Proposed solution:** add a final `except Exception`. Log it with `run_id`, return "Error: the tool failed (internal error, logged as …)", and audit `result=error`.
- **Expected impact:** one failing tool no longer ends the conversation turn.
- **Effort:** 2 hours.
- **Risk of change:** low.
- **Verification:** the E3 scenario passes.

### P1-3 Make the figure check unit-aware and stop trusting the user's numbers (AI-05, MEDIUM)
- **Current issue:** `unverified_figures` (`harness.py` 60–83, 329) misses mis-copies within 0.5%, integers below 100, user-asserted numbers, and cross-unit coincidences.
- **Evidence:** E4: 0% detection for near misses, small counts and user echo; 86.6% for fabricated percentages against a real scorecard.
- **Proposed solution:**
  - Match like with like: a % against percentages and ratio-type values, money against money.
  - Compare money to the cent after the answer's own rounding.
  - Check integers when followed by a unit word (days, months, invoices).
  - Keep user-message numbers in a separate "user-provided" class, shown as "from your message, not verified".
  - Run the check on tool-produced text only.
  - Optionally re-prompt once when an answer has unverified figures.
- **Expected impact:** catches a model that mis-copies a balance or repeats a wrong figure the user gave it.
- **Effort:** 2–3 days.
- **Risk of change:** medium; false positives annoy users. Gate it on E4's false-positive classes (must stay ≤ 0.5%).
- **Verification:** E4 detection ≥ 99% for near misses, small counts and user echo, with false positives ≤ 0.5%.

### P1-4 Keep data names out of the system role (AI-09, MEDIUM)
- **Current issue:** dataset, connection and collection names and the workspace name are written into the system prompt (`agent/prompts.py` 75–94).
- **Evidence:** E7 P4.
- **Proposed solution:** move those lists into a delimited, clearly labelled data message (for example JSON in a user-role context message), or quote and escape them. Keep only the rules in the system role.
- **Expected impact:** an outside file name can no longer pose as an instruction.
- **Effort:** 1 day, plus a prompt-cache check.
- **Risk of change:** medium; it changes what the model sees, so run E8 before and after.
- **Verification:** E7 P4 passes; E8 scores do not drop.

### P1-5 Report malformed stream chunks (AI-08, MEDIUM)
- **Current issue:** a chunk that is not valid JSON is skipped silently (`llm/openai_compat.py` 147–149).
- **Evidence:** E3 `malformed_sse_chunk`.
- **Proposed solution:** count malformed chunks. If any arrive, mark the answer incomplete (as in P0-3) and log the chunk size, not its content.
- **Effort:** 2 hours.
- **Risk of change:** low.
- **Verification:** the E3 scenario passes.

## P2: performance and quality

| ID | Current issue | Evidence | Proposed solution | Impact | Effort | Risk | Verification |
|---|---|---|---|---|---|---|---|
| P2-1 (AI-10) | Synchronous work in async tool handlers blocks the event loop and defeats the timeout | E3: `/health` waited 1.81 s; the timeout did not fire | Run heavy handlers (SQLite aggregation, file parsing) with `asyncio.to_thread`, or mark tools `blocking=True` and run them in a pool | Other conversations unaffected | 2–3 days | Medium (SQLite connection per thread) | E3 `tool_blocking_sync_work` passes; E6 concurrency P95 recorded |
| P2-2 (AI-11) | Fixed 60-message window, no token budget, early facts lost after about 15 turns | E3 `early_fact_retained` | Budget the request in tokens per model. Summarise turns that fall out of the window into a short "conversation so far" note. Prompt the model to propose memory for lasting facts. | Long threads keep their context | 2–4 days | Medium (needs E8 multi-turn) | E3 scenario passes; E8 `multi_turn` holds |
| P2-3 (AI-12) | No latency, TTFT, finish reason or step trace is recorded | `app.py` `record_usage` stores tokens only | Record per model call: TTFT, total time, finish reason, retries. Per tool: duration and status. Expose a `/metrics` endpoint (Prometheus text) behind admin auth. OpenTelemetry only if a customer needs it. | Production visibility | 2 days | Low | Benchmark rows appear in the DB; the metrics endpoint is covered by a test |
| P2-4 (AI-13–16, AI-20) | Calculator edge cases | E1 failure classes | Warn when there is more than one sign change (multiple IRRs). Widen the XIRR bracket (or use Newton from several starts). Use `(cur − prev) / abs(prev)` for growth from a negative base. Refuse salvage above cost. Ask when a rate is above 1 ("did you mean 15%?"). Stop `a.pop` in ratios. | Fewer silently wrong figures | 1 day | Low | E1 at 1,385/1,385 |
| P2-5 (AI-17) | A month outside the ledger is silently replaced | E2 `future_month_not_invented` | Return "No ledger data for 2027-06; the latest month is 2026-10" before any figure | The model cannot present October as June | 2 hours | Low | E2 at 86/86 |
| P2-6 (AI-18) | Identical tool calls re-executed within a run | E3: 6 executions | Cache read-tool results per run by (name, canonical args); return "same as call n" | Fewer tokens and less tool time in loops | 0.5 day | Low (read tools only) | E3 scenario passes |
| P2-7 (AI-19) | Finished tasks kept forever | E9: 1.6 KB per conversation | `task.add_done_callback` to drop the entry from `Agent.tasks` | No slow growth | 1 hour | Low | E9 growth ≈ 0 |

## P3: long-term architecture

| ID | Current state | Proposal | Effort |
|---|---|---|---|
| P3-1 (AI-21) | One process on SQLite. Start-up marks every running run as interrupted (`app.py` 134–136), which assumes a single instance. | For larger Team Server customers: Postgres and per-instance run ownership (a lease with heartbeat) before allowing more than one replica. Keep SQLite for desktop. | weeks |
| P3-2 (AI-22) | Tool output is plain text. Untrusted-content handling relies on rule 13. | Structured envelopes per tool result (`source`, `trust`, `content`), and per-source quoting, so models can be told exactly which text is data. Measure with E8's injection scenario on several models. | 1–2 weeks |
| P3-3 | Accuracy is measured only when someone runs E8 | A nightly live evaluation on the default model, with trend tracking and an alert when a metric drops | 2–3 days |
| P3-4 | No temperature or max-tokens control in the loop | Per-workspace sampling settings (temperature 0 for figures). Set `max_tokens` with the P0-3 continuation path. | 1 day |
