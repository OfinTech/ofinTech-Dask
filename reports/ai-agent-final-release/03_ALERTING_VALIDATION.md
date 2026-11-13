# 03 · Alerting validation

## Status: alert delivery NOT VALIDATED — BLOCKED (no Prometheus, Alertmanager or receiver in this environment)

The rules, routing and receiver configuration are written and checked statically. The signal behind every alert was
shown to move under its injected failure. **No alert has been evaluated by Prometheus, routed by Alertmanager, or
received by a receiver.** This environment has no Docker daemon, no Prometheus / Alertmanager / `promtool` /
`amtool` binaries, and no network to install them. No receiver address was provided, and none was invented.

## Rules (`deploy/monitoring/prometheus/alerts.yml`, `slo.yml`)

| # | Required alert | Rule | Severity | Threshold, window | Basis |
|---|---|---|---|---|---|
| 1 | High agent error rate | `DeskAgentErrorRate`; `DeskAgentSuccessBudgetBurn` | warning | > 5% of non-cancelled runs fail, 15 m, ≥ 20 runs; burn 14.4× / 6× of a 1% budget | proposed SLO 99% (04) |
| 2 | Increased response latency | `DeskAgentLatencyHigh` | warning | completed-run P95 > 30 s, 15 m | 3× the proposed 10 s end-to-end target, to page only on a real degradation |
| 3 | Model inference unavailable | `DeskModelUnavailable` | critical | > 50% of model calls fail with unavailable / network / auth / not-found after retries, 5 m, ≥ 5 calls | L1 E (an outage gives 100%) |
| 4 | Repeated model timeouts | `DeskModelTimeouts` | warning | > 5 timeouts after retries in 15 m | — |
| 5 | Excessive tool failures | `DeskToolFailures` | warning | per tool > 20% errors and ≥ 5 errors, 15 m | L1: 0 tool errors in normal load |
| 6 | DB pool exhaustion (SQLite equivalent) | `DeskDatabaseSaturated` | warning | write P95 > 0.5 s, or > 64 connections, 10 m | L1 healthy write P95 is milliseconds; per-thread connections are bounded by the pools (~40) |
| 7 | High API 5xx rate | `DeskApi5xxHigh`; `DeskApiAvailabilityBudgetBurn` | critical | > 2% 5xx, 10 m, ≥ 50 requests; burn of a 0.1% budget | proposed 99.9% availability |
| 8 | Failed financial calculations | `DeskFinancialCalculationFailures` | warning | > 3 errors in calculators, KPI, cost-centre, dataset or close tools, 30 m | E1 / R1: 0 in tests |
| 9 | Reconciliation error spike | `DeskReconciliationExceptionSpike` | info | > 50% of ≥ 50 reconciled lines are exceptions, 6 h | R1 generated months: 30% exceptions by design |
| 10 | Sustained memory growth | `DeskMemoryGrowth` | warning | + 512 MB in 6 h and > 1 GB, 30 m | L1 / E9: flat |
| 11 | Service unavailable | `DeskServiceDown` | critical | scrape fails 2 m | — |
| 12 | Critical security events | `DeskPaymentOrSealedRefusals` (critical, any); `DeskCrossWorkspaceAttempts` (> 20 / 10 m); `DeskAuthFailureSpike` (> 100 401s or > 50 403s / 5 m) | critical / warning | as stated | payment and sealed refusals should never happen in normal use |
| + | Event loop blocked | `DeskEventLoopLag` | warning | P99 lag > 0.5 s, 10 m | L1 E: slow DB reads |
| + | Unsupported figures | `DeskUnsupportedFiguresHigh` | warning | > 10% of ≥ 20 answers, 1 h | E10 |
| + | Background jobs | `DeskBackgroundJobFailures` | info | > 3 / h per kind | — |

## Noise control (`deploy/monitoring/alertmanager/alertmanager.yml`)

- **Grouping:** by alert name and severity, waiting 30 s (10 s for critical alerts), regrouping every 5 m.
- **Repeat intervals:** 4 h by default, 1 h for critical alerts, 24 h for info.
- **Inhibition:**
  - `DeskServiceDown` silences everything else from the service.
  - `DeskModelUnavailable` silences agent errors, latency, timeouts and burn rate.
  - A critical alert silences the warning with the same name.
- **Minimum traffic:** every ratio alert needs a minimum count of calls or requests, so a quiet instance doesn't page on a single recoverable failure.

## Receiver

The `default` receiver is a webhook with `url_file: /etc/alertmanager/secrets/alert_webhook_url`. The operator writes
their own URL into that git-ignored file (`deploy/monitoring/secrets/`); no address is committed. `send_resolved: true`
makes recoveries visible. To use email or an on-call service instead, replace the receiver in the same file, keeping
credentials in files.

## Evidence obtained here

| Check | Method | Result |
|---|---|---|
| Rules, routing, compose and dashboards parse | YAML / JSON parse in `test_observability` | pass (17 alerts, 2 burn-rate alerts, 11 recording rules, 5 dashboards) |
| Every metric used by a rule or panel exists | names compared with the engine's exported names | pass, 0 unknown |
| Every alert has a severity (critical, warning, info), summary and runbook | test | pass |
| No customer data in alert text | annotations may reference only exported labels; no workspace, user, thread, email or account label exists | pass |
| The signal moves when the failure is injected (`AlertSignals`, 6 tests) | model 503 → `desk_llm_calls_total{outcome="llm_unavailable"}` and 2 retries; timeouts → `llm_timeout`; tool exception → `desk_tool_calls_total{status="error"}`; a handler crash → `desk_http_requests_total{code="5xx"}`; a payment attempt → `desk_tool_refusals_total{reason="payment"}`; recovery → successful calls and completed runs resume | pass |
| Monitoring after a restart | D2: a real engine process, SIGTERM, restart; the scrape returns 200 with `desk_up 1` and a new start time | see 05 / 08 |
| Alert evaluation, firing, recovery, routing, suppression, delivery | requires Prometheus + Alertmanager + a receiver | **NOT DONE (blocked)** |

## How to complete the validation (staging, ~30 minutes)

1. Start the overlay (`deploy/monitoring/docker-compose.monitoring.yml`, header comment) next to a staging engine.
2. Run `promtool check rules alerts.yml slo.yml` and `amtool check-config alertmanager.yml`. These were not run here.
3. Point `secrets/alert_webhook_url` at an authorised test receiver; any HTTP endpoint that logs requests will do.
4. Break the staging model provider's key, then send 6 chat messages. `DeskModelUnavailable` should fire within about 10 minutes, and the receiver should get a `firing` payload with no workspace data. Fix the key: a `resolved` payload should follow.
5. Stop the engine container. `DeskServiceDown` should fire after 2 minutes, and the inhibition should leave only that alert.
6. Record the payloads in this report. Only then can the observability gate be PASS.
