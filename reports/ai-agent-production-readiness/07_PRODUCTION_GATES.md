# 07 · Production gates

Each gate is judged on its own. BLOCKED is never counted as PASS. Where no agreed threshold exists, a threshold is
proposed and marked **not approved**.

| Gate | Acceptance requirement | Status | Evidence / reason |
|---|---|---|---|
| Security | No unresolved critical / high findings | **PASS** (engine layer) | AI-01, AI-02 re-verified; 154 security tests; red-team matrix 14 categories; no open CRITICAL / HIGH. Model-level injection resistance is part of E8 (blocked). |
| Financial correctness | Defined critical deterministic tests pass | **PASS** | E1 1,385 / 1,385; E2 86 / 86; R1 211 / 211; F1 168 / 168; golden financial tests |
| Tenant isolation | No confirmed unauthorised access | **PASS** | E5 0 / 657; route-level isolation test; embedded tenants; offloaded tools under concurrency; trace and metrics probes |
| Agent reliability | All required failure-recovery tests pass | **PASS** | E3 17 / 17; 18 lifecycle tests; P1 0 errors in 16 stages; E9 no growth |
| Response integrity | No failed output marked complete | **PASS** | Agent loop (PR #70) and, now, documents answer, dashboard insights, file reviewer (5 tests failing on `main`) |
| Live model accuracy | E8 executed and accepted | **BLOCKED** | No authorised endpoint (03). Thresholds proposed, not approved. |
| Observability | Required metrics, traces and alerts verified | **FAIL** | Metrics: verified (`/api/admin/metrics`, tests). Traces: run trace verified (in-product, not OpenTelemetry). **Alerts: none exist**; delivery needs the operator's monitoring stack. |
| Performance | Documented SLOs satisfied | **NOT TESTED** | No agreed SLO exists. Proposed SLOs (06) measured: met except `/health` P95 under 20 concurrent KPI scorecards (189 ms vs 100 ms proposed). Real-model latency unmeasured. |
| Regression safety | Required tests pass | **PASS (local); CI pending** | 555 / 555 engine tests; ruff F and the S gate clean; UI tests 6 / 6; translations complete. UI typecheck, `pip-audit`, `gitleaks` and `npm audit` run in CI only. |
| Deployment safety | Rollback and recovery procedures validated | **PASS** (for this change set) | D1: data written by this branch is served by `main` and vice versa, and a new turn completes both ways. No schema migration. Retries off with `DESK_LLM_RETRIES=0`; budget with `DESK_LLM_HISTORY_TOKENS`. Multi-instance recovery not applicable (single process, AI-21). |

## Decision

**NOT PRODUCTION-READY for enterprise financial environments.**

Mandatory gates not passed:
1. **Live model accuracy: BLOCKED.** No real model has been measured in the agent loop.
2. **Observability: FAIL.** Alerts do not exist.
3. **Performance: NOT TESTED.** No agreed SLO.

The engine-level gates pass: security, financial correctness, tenant isolation, reliability, response integrity,
regression and deployment safety.

## What changes the decision

1. Run E8 live with authorised, non-production credentials for each supported model, and have the owner approve its thresholds (03).
2. Configure alerting on the new metrics, and verify that each alert fires (proposed rules below).
3. The owner approves SLOs (06 proposes them); then measure end-to-end latency with the chosen model.
4. CI green on the pull request, and a maintainer's review.

## Proposed alert rules (NOT approved; Prometheus syntax, scraping `/api/admin/metrics`)

```yaml
groups:
- name: ofintech-desk-agent
  rules:
  - alert: DeskModelErrors
    expr: sum(rate(desk_llm_calls_total{outcome!="ok"}[10m])) / sum(rate(desk_llm_calls_total[10m])) > 0.05
    for: 10m
  - alert: DeskRunsNotCompleting
    expr: sum(rate(desk_agent_runs_total{outcome!="completed"}[15m])) / sum(rate(desk_agent_runs_total[15m])) > 0.10
    for: 15m
  - alert: DeskEventLoopLag
    expr: histogram_quantile(0.99, sum(rate(desk_event_loop_lag_seconds_bucket[5m])) by (le)) > 0.5
    for: 5m
  - alert: DeskToolErrors
    expr: sum(rate(desk_tool_calls_total{status="error"}[15m])) by (tool) > 0.1
    for: 15m
  - alert: DeskModelSlow
    expr: histogram_quantile(0.95, sum(rate(desk_llm_ttfb_seconds_bucket[10m])) by (le)) > 10
    for: 10m
```
