# 04 · SLO approval and validation

## SLO APPROVAL: PENDING

No owner approval is documented. The targets below are the exact proposals from
`reports/ai-agent-final-release/04_PERFORMANCE_SLOS.md`, unchanged, laid out for a decision. Writing them into
`deploy/monitoring/prometheus/slo.yml` does not approve them.

## Approval-ready table

**Window:** 30 days rolling.
**Burn-rate alerts:** 14.4× over 1 h and 5 m (page); 6× over 6 h and 30 m.
**Data source:** the engine's `/api/admin/metrics`, scraped by Prometheus.

| # | Layer | SLO | Target | Eligible workload | SLI (Prometheus) | Error budget (30 d) | Alert | Measured now | Approval |
|---|---|---|---|---|---|---|---|---|---|
| 1 | API availability | requests without a server error | ≥ 99.9% | router requests excluding `/health`, `/ready`, static UI; 4xx excluded | `desk_slo:api_errors:ratio_rate*` | 0.1% of requests (~43 min at constant load) | `DeskApiAvailabilityBudgetBurn`, `DeskApi5xxHigh` | 0% 5xx in every load stage (minutes, not a month) | PENDING |
| 2 | Agent orchestration latency | engine time per turn, model excluded | P95 ≤ 1 s up to 25 concurrent users per process | completed runs | `desk_agent_run_seconds` − `desk_llm_call_seconds` (scripted model ≈ 0) | 5% of turns | `DeskEventLoopLag` | L1 A (25 users): P95 395 ms ✓; B 100 users: 1.6 s ✗ | PENDING |
| 3 | Full agent response, simple question | message to stored answer | P95 ≤ 10 s | completed runs without tools | share ≤ 10 s of `desk_agent_run_seconds{outcome="completed"}` ≥ 95% | 5% | `DeskAgentLatencyHigh` (30 s) | **not measurable**: needs the live model | PENDING |
| 3b | Full agent response, multi-step analysis | as above | P95 ≤ 30 s | completed runs with ≥ 2 tool calls | needs a steps label (not yet) | 5% | — | not measurable | PENDING |
| 4 | Financial tool execution | built-in tool duration | P95 ≤ 1 s | built-in tools (not MCP or accounting sync) | `desk_slo:tool_fast:ratio_rate30m` ≥ 95% | 5% | `DeskToolFailures` (errors) | E6: KPI tool call ≈ 17 ms; L1 KPI turn P95 ≤ 0.4 s at 25 users | PENDING |
| 5 | Live-model inference | model call and TTFT | to be set from E8 | all model calls | `desk_llm_call_seconds`, `desk_llm_ttfb_seconds` | — | `DeskModelTimeouts`, `DeskModelUnavailable` | **not measurable** (E8 blocked) | PENDING (no target yet) |
| 6 | Financial correctness | critical deterministic tests | 100% | E1, E2, R1, F1, golden tests | test results per release | 0 | CI | 100% (07) | PENDING |
| 7a | Reliability: agent success | completed / (all − cancelled) | ≥ 99% | runs started | `desk_slo:agent_failures:ratio_rate*` | 1% of runs | `DeskAgentSuccessBudgetBurn`, `DeskAgentErrorRate` | 100% outside injected failures | PENDING |
| 7b | Reliability: incomplete answers marked complete | count | 0 | all runs | lifecycle suites; `desk_agent_run_errors_total{code="answer_incomplete"}` counts them as failures | 0 | — | 0 | PENDING |
| 7c | Tenant exposure | count | 0 | every release | E5, tenant suites | 0 | `DeskCrossWorkspaceAttempts` (attempts) | 0 / 657 | PENDING |
| 8 | API error rate (including 429) | — | < 1% | all requests | as #1 | — | as #1 | 0% | PENDING |

## Reasons for the targets

- **API 99.9%.** One instance on SQLite with restarts of about 1 s (D2). It is achievable on a stable host, but monthly evidence does not exist yet.
- **Orchestration 1 s P95 at 25 users per process.** Measured capacity is about 65–90 actions/s per process on 2 vCPU. Beyond about 25 concurrent busy users, latency grows linearly (CPU queueing). The SLO states the tested envelope rather than promising more.
- **Full response 10 s / 30 s.** Users accept seconds for a financial answer. Most of this budget is the model's, so it must be confirmed against E8.
- **Correctness and integrity at 100% / 0.** These are release blockers, not budgets.

## Measurement environment

2 vCPU Intel Xeon @ 2.10 GHz, 7 GB RAM, Python 3.13.18, SQLite WAL, one engine process, a scripted zero-latency
model, synthetic HarborLine ledger. L1 and P4 run in process; R2 runs the engine as a separate process driven over
HTTP. This is the measurement environment only, not production capacity.

## Known capacity limits

- One process is about one CPU core: roughly 65–90 actions/s in process, or about 21–26 turns/s over HTTP with polling clients.
- A single instance: multi-instance recovery is not supported (AI-21).
- Offloaded reads cost CPU when the database is fast (05). `DESK_AGENT_OFFLOAD_READS=0` trades that back.

## Rollback triggers (proposed)

Roll back a release if any of these holds within 24 hours of deploying:
- `DeskApiAvailabilityBudgetBurn` or `DeskServiceDown` fires;
- the agent success rate falls below 99% for 1 hour;
- orchestration P95 is more than 2× the previous release's at the same load;
- any correctness or isolation regression appears in CI.

## Release gate: BLOCKED

- The targets are not approved.
- Targets 3, 3b and 5 cannot be measured without the live model.
- The measured envelope meets the proposed targets 2, 4, 6, 7 and 8 at up to 25 concurrent users per process, in the short tests.
