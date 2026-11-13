# OfinTech Desk AI Agent: executive report

**Audit date:** 13 November 2026. **Code audited:** `OfinTech/ofintech-dask` `main` at `f8c58cd` (version 1.3.0).

**Method:** source review of the agent, plus nine reproducible evaluation suites (`evals/`) run in an isolated
environment on synthetic data, plus a re-run of the 100 existing agent-security tests. No application code was
changed, nothing was merged, and no production system or real data was touched.

## Summary

**Is the OfinTech Desk AI Agent operating at its best practical performance and quality level? No.** The
deterministic core is strong and safe. However, the loop mishandles model failures in ways that can show a user an
unfinished financial answer as finished. An approval control on the assistant's instructions can be bypassed. And
the accuracy of a real model's answers has never been measured.

| | |
|---|---|
| **Overall score** | **62 / 100, provisional.** This is the weighted mean of 9 of 10 categories. The accuracy of the agent's answers with a live model is **not scored**, because no evidence exists. See AI_AGENT_SCORECARD.md. |
| **Production readiness** | **Not ready for enterprise financial environments** until the 4 P0 items are done. It is suitable for supervised pilot use today, because no write happens without a person's approval and no payment can be made. |
| **Confidence** | High in what was executed: deterministic, seeded, with reproducible commands. None in model-level behaviour: not tested. |

## What works well (measured)

- **Calculators are exact.** 1,331 of 1,331 standard cases match an independent 50-digit implementation to the cent, across NPV/IRR, XNPV/XIRR, loans, depreciation, ratios, variance, VAT, break-even, growth and FX (E1).
- **Figures read from the ledger are exact.**
  - 60 of 60 KPI values across 4 months match ground truth summed independently from 867 synthetic journal lines, with month-boundary postings and credit notes (E2).
  - A 19-month DSO series matches too.
  - An unbalanced ledger is reported as unbalanced.
- **Tenant isolation held.** 657 tool calls from workspace B with every workspace-A identifier led to 0 exposures (E5). The 100 existing security tests pass.
- **Writes are controlled.** Every write waits for a person to approve the exact arguments, and approvals cannot be reused or altered. Payment tools cannot exist. Unknown and unoffered tools are refused and audited.
- **Engine overhead is low and stable.**
  - P95 is 8.8–30.2 ms per turn without the model's own time.
  - Tools stay under 11 ms warm on a 100,000-line ledger.
  - Requests stay bounded in long conversations (E3, E6).
  - The tool-group selection keeps the median request near 7,300 tokens.

## Critical findings

| ID | Severity | Finding | Evidence |
|---|---|---|---|
| AI-01 | HIGH | A skill pinned to a conversation reaches the system prompt even when an admin has disabled it, or has not yet approved it. A viewer can pin it. | E7 P2, P3; `agent/harness.py` 282–286, `skills/loader.py` 152–157 |
| AI-02 | HIGH | An editor can rewrite an admin-enabled skill and it stays enabled, so unreviewed text reaches the system prompt. | E7 P1; `routes/skills.py` 62–75 |
| AI-03 | HIGH | An incomplete model output is stored and shown as a complete answer. This covers three cases: a stream that was cut off, a reply truncated by the output limit, and an empty reply. | E3 (3 scenarios); `llm/openai_compat.py` 136–183, `agent/harness.py` 317–345 |
| AI-04 | HIGH (process) | The accuracy of a real model's answers has never been measured. Tool selection, hallucination, relevance and task completion are all NOT TESTED. | AI_AGENT_EVALUATION.md section 7 |

## All findings

| ID | Severity | Finding | Location |
|---|---|---|---|
| AI-01 | HIGH | Pinned skills bypass enable/approval | `agent/harness.py` 282–286; `skills/loader.py` 152–157 |
| AI-02 | HIGH | An editor's edit keeps a skill enabled | `routes/skills.py` 62–75; `skills/loader.py` 179–182 |
| AI-03 | HIGH | Cut, truncated or empty model output stored as a complete answer | `llm/openai_compat.py` 136–183; `agent/harness.py` 317–345 |
| AI-04 | HIGH | No live-model evaluation exists | none (process gap); `evals/e8_live_model.py` provided |
| AI-05 | MEDIUM | Figure check: 0% detection of mis-copies within 0.5%, numbers below 100, and user-asserted figures; 13.4% of fabricated percentages pass against a real scorecard; advisory only | `agent/harness.py` 60–83, 329 |
| AI-06 | MEDIUM | No retry or backoff on 429, 5xx or timeouts; timeouts surface as "Unexpected error: ReadTimeout" | `llm/openai_compat.py` 136–138; `agent/harness.py` 228–232 |
| AI-07 | MEDIUM | An unexpected tool exception (e.g. database locked) loses the whole turn | `agent/tools.py` 216–223 |
| AI-08 | MEDIUM | Malformed stream chunks are silently dropped | `llm/openai_compat.py` 147–149 |
| AI-09 | MEDIUM | Dataset, connection and collection names and the workspace name enter the system role verbatim | `agent/prompts.py` 75, 85, 92, 94 |
| AI-10 | MEDIUM | Blocking work in async tool handlers stalls every conversation and defeats tool timeouts (another request waited 1.81 s) | `agent/tools.py` 217 |
| AI-11 | MEDIUM | Fixed 60-message window with no token budget or summary; a fact from turn 1 is lost after about 15 turns | `agent/harness.py` 33, 89–92 |
| AI-12 | MEDIUM | No recording of model or tool latency, TTFT, finish reason, retries or error category | `app.py` 209–213 |
| AI-13 | LOW | Several IRRs exist but one is reported without a warning (22 of 30 non-conventional cases) | `agent/finance_tools.py` 33–66, 237 |
| AI-14 | LOW | XIRR above 1,000% is reported as "n/a" | `agent/finance_tools.py` 77 |
| AI-15 | LOW | Growth from a negative base has the wrong sign (−100 → −50 reported as −50%) | `agent/finance_tools.py` 395 |
| AI-16 | LOW | No sanity checks: a VAT rate of 15 is taken as 1,500%; salvage above cost gives a schedule of zeros | `agent/finance_tools.py` 136, 215–221 |
| AI-17 | LOW | A month outside the ledger is silently replaced by the latest month | `agent/analysis_tools.py` 71 |
| AI-18 | LOW | Identical tool calls are re-executed within a run (6 times in E3) | `agent/harness.py` 348–360 |
| AI-19 | LOW | Finished agent tasks are kept forever (1.6 KB per conversation) | `agent/harness.py` 144, 182–184 |
| AI-20 | LOW | `calc_financial_ratios` mutates its call arguments, so the stored call loses `days_in_period` | `agent/finance_tools.py` 321 |
| AI-21 | INFORMATIONAL | Single process on SQLite; restart recovery assumes one instance | `db.py` 886–888; `app.py` 134–136 |
| AI-22 | INFORMATIONAL | Tool results have no structural "untrusted" envelope; the defence relies on the prompt rule plus approvals | `agent/prompts.py` 54–57 |
| AI-23 | INFORMATIONAL | Audit rows of failed tools keep 200 characters of the error text | `agent/harness.py` 438–442 |

No CRITICAL issue was found. Nothing tested allowed money to move, data to leave without approval, or one
workspace to read another.

## Highest-priority improvements

1. **P0-1 and P0-2:** pin only enabled skills, and return an editor's edit to admin review. About 1–2 days; verified by E7.
2. **P0-3:** detect incomplete streams and truncation, and never store them as a final answer. About 1 day; verified by E3.
3. **P0-4:** run the live evaluation (`e8_live_model.py --live --repeat 3`) on every model offered, and make it a release gate. About 1 day plus a few dollars per model.
4. **P1-1, P1-2 and P1-5:** retries with backoff before streaming, a catch-all for tool exceptions, and reporting of malformed chunks. About 1.5 days; this would take error recovery from 4/8 to 8/8 on E3's recoverable set.
5. **P1-3:** a unit-aware figure check that does not trust the user's figures. About 2–3 days; verified by E4.

The full plan, with effort, risk and verification tests, is in AI_AGENT_OPTIMIZATION_ROADMAP.md.

## The questions asked

- **What works well?** The financial engine (exact against independent ground truth), tenant isolation, approval binding, the payment and sealed-workspace guards, tool validation, loop limits, and low engine overhead.
- **What is underperforming?** Handling of model and tool failures (7 of 17 scenarios pass), context retention in long conversations, the figure check's blind spots, observability, and calculator edge cases.
- **What is unsafe?**
  - Unreviewed text can reach the assistant's system instructions (AI-01, AI-02, and AI-09 in a narrower form).
  - A cut-off answer can be presented as complete (AI-03).
  - Neither can move money or send data without a person's approval. Both can mislead a reader.
- **What has not been verified?** Everything that depends on a real model: intent, tool choice, hallucination, use of tool output, ambiguity handling, multi-turn quality, consistency, injection resistance at the model level, time to first token, and cost. Also not verified: multi-instance deployment, and logs under real failure.
- **Which improvements give the most benefit?** The four P0 items (about 4 days in total), then P1-1/P1-2/P1-5 and P1-3.
- **Is the agent production-ready?** Not for enterprise financial environments. It is ready for a supervised pilot.
- **What is required for an enterprise grade?** All four P0 items, the P1 items, and a recorded live evaluation meeting the targets:
  - tool selection ≥ 98%;
  - figure accuracy 100% on the defined scenarios, with no unverified figures;
  - error recovery ≥ 99% on the E3 recoverable set;
  - metrics for latency and failures (P2-3).

## Audit limitations

- **No model provider was reachable.** Every behavioural test used a scripted model, which proves what the engine does, not what a model would do.
- **The hardware was small.** Benchmarks ran in-process on a 2-vCPU VM. Production hardware and network will differ, and model latency will dominate.
- **Rates hold only for what was generated.** E1, E2, E4 and E5 are exact for their seeded distributions. E3 and E7 are single scenarios that show a behaviour exists, not a rate.
- **Hidden prompts are not reproduced.** The prompt review describes the system prompt's structure and issues without quoting it.

## Reproducing everything

From the repository root, with the engine's requirements installed:

```bash
for e in e1_calculators e2_ledger_grounding e3_reliability e4_figure_check e5_tenant_isolation \
         e7_instruction_surfaces e9_memory_growth; do
  python3 reports/ai-agent-audit/evals/$e.py
done
python3 reports/ai-agent-audit/evals/e6_benchmarks.py           # about 10 minutes
python3 reports/ai-agent-audit/evals/e8_live_model.py --dry-run  # scorer validation
# live model (costs tokens; synthetic data only):
DESK_EVAL_BASE_URL=... DESK_EVAL_API_KEY=... DESK_EVAL_MODEL=... \
  python3 reports/ai-agent-audit/evals/e8_live_model.py --live --repeat 3
```
