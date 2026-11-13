# 04 · Performance SLOs

## Approval status

**PROPOSED, NOT APPROVED.** No business owner has approved these targets. The technical results below come from
short benchmarks (minutes, not months). They show whether the engine *can* meet a target under the tested load.
They do not show monthly compliance, and no SLA is claimed.

## Four latency layers

| Layer | What it covers | How it is measured | Measurable now? |
|---|---|---|---|
| Backend / API | an HTTP request to the engine, excluding model time | `desk_http_request_seconds` | yes |
| Agent orchestration | the agent loop's own work: prompt building, tools, figure check, storage | run time with a zero-latency model (L1, E6) | yes |
| Model inference | the provider's time per call, including TTFT | `desk_llm_call_seconds`, `desk_llm_ttfb_seconds` | only with a live model (E8 blocked) |
| End to end | message sent to answer stored | `desk_agent_run_seconds` | orchestration part only |

## Proposed SLOs, SLIs and error budgets

**Window:** 30 days, rolling. **Error budget:** 1 − target over the window.
**Burn-rate alerts** (`deploy/monitoring/prometheus/slo.yml`) page when the budget burns 14.4× too fast over 1 h and 5 m, or 6× over 6 h and 30 m.

| SLO | Target | SLI formula (Prometheus) | Eligible population | Exclusions |
|---|---|---|---|---|
| API availability | ≥ 99.9% | 1 − `sum(rate(desk_http_requests_total{code="5xx"}))` / `sum(rate(desk_http_requests_total))` | all API requests through the router | `/health`, `/ready`, static UI; 4xx (client errors); planned maintenance windows |
| Agent successful execution | ≥ 99% | `completed` / (all − `cancelled`) from `desk_agent_runs_total` | every run started | user cancellations; runs failed by `llm_auth` / `llm_not_found` (customer configuration), tracked separately |
| Agent response, simple question (no tool) | P95 ≤ 10 s end to end | share of `desk_agent_run_seconds{outcome="completed"}` ≤ 10 s (`desk_slo:agent_fast:ratio_rate30m`) ≥ 95% | completed runs | — |
| Agent response, multi-step analysis (≥ 2 tool calls) | P95 ≤ 30 s end to end | needs a step-count label or the run trace | completed runs | not yet separable in metrics; see limitations |
| Reconciliation (prepare close with a statement) | P95 ≤ 5 s | `desk_http_request_seconds{route="/api/w/{ws}/close/{rid}/statement"}` | statement uploads | file size above 5,000 lines |
| Standard financial tool | P95 ≤ 1 s | share of `desk_tool_seconds{tool!="mcp"}` ≤ 1 s (`desk_slo:tool_fast:ratio_rate30m`) ≥ 95% | built-in tools | MCP and accounting-sync tools (third-party latency) |
| API latency | P95 ≤ 1 s | share of `desk_http_request_seconds` ≤ 1 s (`desk_slo:api_fast:ratio_rate30m`) ≥ 95% | API requests | uploads, exports, SSE streams |
| API error rate | < 1% | same as availability, including 429 | all | — |
| Unauthorised tenant exposure | 0 | E5, the tenant suites, `desk_workspace_rejections_total` (rejections are expected; exposures must be 0) | every release | — |
| Incomplete answers marked complete | 0 | the lifecycle and completeness suites; `desk_agent_run_errors_total{code="answer_incomplete"}` counts them as failures, not answers | every release | — |
| Critical financial correctness | 100% of defined tests | E1, E2, R1, F1, golden tests | every release | — |

### Error budget example

At 99.9% availability and 1,000,000 API requests a month, 1,000 server errors are allowed. A burn rate of 14.4
spends 2% of that budget in one hour.

## Technical results against the proposed targets (L1, scripted model, 2 vCPU)

See 05_LOAD_TEST_RESULTS.md for the full tables. The results are summarised there after the run.

| SLO (orchestration and API part only) | Result | Status |
|---|---|---|
| Standard financial tool P95 ≤ 1 s | met in every normal stage | technically met (short test) |
| API error rate < 1% | 0% in every normal and peak stage | technically met (short test) |
| Agent success ≥ 99% outside injected failures | 100% | technically met (short test) |
| Agent response P95 ≤ 10 s | orchestration P95 is far below 10 s up to 100 users; the model's share is unknown | **cannot be judged** without the live model |
| API availability 99.9% monthly | not measurable in a short test | **not tested** |
| Tenant exposure 0, incomplete-as-complete 0, financial correctness 100% | 0 / 0 / 100% (07) | met in the defined tests |

## Limitations

- One process on 2 vCPUs, and the load generator shares the process (GIL). Production hardware and an out-of-process generator (k6 against a staging server) will give different absolute numbers.
- Multi-step agent latency is not separable by a metric label yet. Adding a `steps` bucket label to `desk_agent_run_seconds` is a small follow-up.
- Monthly availability needs production traffic and a 30-day window.
