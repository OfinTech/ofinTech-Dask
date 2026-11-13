# 01 · Executive summary

**Decision: NOT PRODUCTION READY.** Score **83 / 100 (provisional)**; model accuracy is unscored.

**Branch:** `feat/ai-agent-final-release-gates`, stacked on PR #71 (still open). PR #68 and PR #70 are merged. Nothing is merged by this work.

## The three gates this phase set out to close

| Gate | Before | Now | Why not PASS |
|---|---|---|---|
| Observability and alerting | FAIL (no alerts) | **BLOCKED** | Built and statically verified: metrics for every layer, 19 alerts, routing, 5 dashboards, runbook. Not yet shown firing and delivering through Prometheus, Alertmanager and an authorised receiver, which cannot run here (no Docker daemon, binaries or network). |
| Performance SLOs | NOT TESTED | **BLOCKED** | SLOs, SLIs, error budgets and burn-rate rules are defined (04), but **not approved**. Load tested at 10–200 users: 0% errors, recovery from every injected failure. End-to-end latency needs the live model. |
| Live model E8 | BLOCKED | **BLOCKED** | No approved endpoint, credentials or configured model. Nothing was simulated as a pass. |

## Baseline (reproduced on PR #71's head `e085271`)

| Check | Result |
|---|---|
| Engine tests | 555 OK |
| E1 | 1,385 / 1,385 |
| E2 | 86 / 86 |
| E3 | 17 / 17 |
| E5 | 0 / 657 exposures |
| R1 | 211 / 211 |
| F1 | 168 / 168 |

No discrepancies.

## What this phase delivered

| Area | Delivered | Proof |
|---|---|---|
| Engine metrics | Agent, LLM, financial, backend and security metrics; the cardinality cap; `DESK_METRICS_TOKEN` scrape access | `test_observability` (13 tests) |
| Monitoring stack | `deploy/monitoring/`: Prometheus (30 days), Alertmanager (grouping, inhibition, file-based receiver), Grafana (local-only, five dashboards), SLI and burn-rate rules | every metric referenced exists; YAML and JSON parse |
| Load tests | L1 scenarios A–E | 05 |
| Deployment | D2 on a real process: config, readiness, token scrape, graceful shutdown, restart, backup → restore | 08 |
| Defect found and fixed | RT-05: SQLite connections of ended threads were never closed (497 in the first load run) | test fails before the fix |

**Final regression:**
- 569 engine tests, and 196 security and regression tests: all pass.
- All evaluations pass: E1, E2, E3, E5, E7, E10, R1, F1.
- Lint is clean, and there are no new type errors.

## Measurements (scripted model, 2 vCPU)

| Scenario | Users | Throughput | Errors | P95 latency | Note |
|---|---:|---:|---:|---:|---|
| Normal mix | 25 | 88.5 actions/s | 0 | 280 ms | |
| Concurrency | 100 | 74.6 actions/s | 0 | 1.1 s | |
| Peak | 200 | 75 actions/s | 0 | 2.3 s | |
| Long conversations | 10 × 60 turns | — | 0 | — | no latency drift, no memory growth |
| Injected model failures | — | — | — | — | 2,357 failed honestly; recovery immediate |

**Bottlenecks:**
- One process saturates one CPU core.
- The agent loop's own database reads run on the event loop: with a 2 ms delay per read, P95 rises to 1.9 s.

## Outstanding risks

- Real-model accuracy is unknown.
- Alert delivery is unproven.
- The SLOs are unapproved.
- The engine runs as a single process and is CPU-bound.
- The agent loop's database reads still run on the event loop.
- No resource limits are set in the compose file.
- There is no OpenTelemetry, Tempo or Loki.
- CI and dependency audits are still pending.

## Recommendation

Do not release. The fastest path to a decision:
1. E8 with an approved model.
2. Half an hour in staging to prove alert delivery (03).
3. Owner approval of the SLOs (04).

The engine-level gates already pass.

## Reports

| Report | Covers |
|---|---|
| [02](02_OBSERVABILITY_ARCHITECTURE.md) | Observability architecture |
| [03](03_ALERTING_VALIDATION.md) | Alerting validation |
| [04](04_PERFORMANCE_SLOS.md) | Performance SLOs |
| [05](05_LOAD_TEST_RESULTS.md) | Load test results |
| [06](06_LIVE_MODEL_E8.md) | Live model E8 |
| [07](07_SECURITY_REGRESSION.md) | Security and regression |
| [08](08_PRODUCTION_READINESS.md) | Production readiness |
| [09](09_FINAL_SCORECARD.md) | Final scorecard |
| [10](10_OPERATIONS_RUNBOOK.md) | Operations runbook |

The evaluations are in `evals/`; the raw results are in `results/`.
