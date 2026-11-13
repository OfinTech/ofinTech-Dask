# 07 · Security and regression

All runs used isolated temporary engines and synthetic data. Raw output is in `results/`.

## Regression

| Suite | Result |
|---|---|
| Engine suite | **569 / 569 OK** (`results/engine_suite.txt`; 14 new tests in this branch) |
| Security and regression modules (16): AI security, approvals and audit, security boundaries, guard audit, isolation, tenant isolation, embedded tenant and approvals, skill authorisation, response lifecycle, audit regressions, production readiness, observability, figure provenance, finance edge cases, close | **196 / 196 OK** (`results/security_and_regression_suites.txt`) |
| E1 calculators | 1,385 / 1,385 |
| E2 ledger KPIs | 86 / 86 |
| E3 reliability | 17 / 17 |
| E5 cross-workspace | 0 exposures in 657 calls |
| E7 system-role probes | 4 / 4 |
| E10 figure check | precision 100%, recall 99.83% |
| R1 reconciliation | 211 / 211 |
| F1 dashboard vs agent | 168 / 168 |
| E8 scorer (scripted, not a model score) | oracle 21 / 21, fabricator 4 / 21 |
| Static analysis | `ruff --select F` (engine, e2e, reports) clean; the CI S gate clean; mypy on the 11 changed engine files: 37 errors before and after (no new ones) |
| Dependency checks | `pip-audit`, `gitleaks`, `npm audit` **not available** here (no network); they run in CI. No dependency was added. |

## Can the monitoring leak or hurt anything?

| Risk | Control | Evidence |
|---|---|---|
| Financial records, prompts, conversation content in metrics | Only counts and durations; labels are route templates, codes, closed vocabularies; MCP tools collapse to `mcp` | `test_no_customer_data_in_metrics`: a secret project name in a prompt, an amount in an answer, workspace, thread and user ids, email, the API key and the provider host are all absent; label characters are restricted |
| Account identifiers | none are ever labels; alert annotations may only use exported labels | `test_every_alert_is_complete` |
| Bypassing workspace isolation | metrics are process-wide aggregates with no tenant dimension; the trace endpoint is unchanged (workspace role + thread visibility) | PR #71 trace probes re-run (pass) |
| Unauthorised access to diagnostics | admin session or `DESK_METRICS_TOKEN` (≥ 32 characters, constant time); the token opens nothing else; short or missing token never accepted | `ScrapeAccess` (2); D2 on a real process: 200 with the token, 401 without, 401 with a wrong token |
| Excessive database load | no queries added; timers only; gauges are read at scrape time | E6 within about 1 ms; L1 throughput unchanged |
| Unbounded memory | 300 label sets per family, then dropped and counted; Prometheus `sample_limit` | `test_cardinality_is_capped`; L1: RSS flat after the peak stage |
| Trace exposure | unchanged | as above |

## Security events now observable

401s by route, 403s by reason, cross-workspace requests, and guard refusals (payment, sealed, role, unknown tool).
A payment or sealed refusal raises a **critical** alert; a spike of cross-workspace requests or authentication
failures raises a **warning**.

## New findings in this phase

| ID | Severity | Finding | Status |
|---|---|---|---|
| RT-05 | MEDIUM | The database kept every thread's SQLite connection until shutdown; thread churn leaked connections and memory (497 connections in the first load run) | **Fixed** (`b69c3da`), test fails before |

No CRITICAL or HIGH finding is open in the engine layer. Model-level injection resistance still needs E8.
