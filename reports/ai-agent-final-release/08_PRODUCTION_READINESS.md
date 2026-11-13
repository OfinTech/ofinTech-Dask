# 08 · Production readiness

## Decision: **NOT PRODUCTION READY**

Two mandatory gates are blocked: the live model evaluation (E8) and alert delivery. Performance SLOs are unapproved
and cannot be checked end to end without a live model. A conditional (restricted) release would need the owner's
explicit risk acceptance of those gaps; none has been given, so none is recommended here.

## Gates

| # | Gate | Acceptance | Status | Evidence |
|---|---|---|---|---|
| 1 | Security | no open critical / high | **PASS** (engine layer) | 07: 196 security and regression tests, red-team matrix (PR #71), RT-05 fixed; model-level injection is part of E8 |
| 2 | Financial correctness | defined critical tests pass | **PASS** | E1 1,385 / 1,385, E2 86 / 86, R1 211 / 211, F1 168 / 168, golden tests |
| 3 | Agent reliability | failure-recovery tests pass | **PASS** | E3 17 / 17; L1 scenario E (model 503, timeout, network, tool timeout, slow DB): honest failures, immediate recovery |
| 4 | Tenant isolation | no unauthorised access | **PASS** | E5 0 / 657; tenant suites; metrics carry no tenant dimension |
| 5 | Response integrity | no failed output marked complete | **PASS** | lifecycle and completeness suites; L1: 2,357 injected model failures, none stored as an answer |
| 6 | Alerting validated end to end | alerts fire, route, recover and reach an authorised receiver | **BLOCKED** | rules, routing, receiver and dashboards are written and statically verified; signals verified (6 tests). No Prometheus, Alertmanager or receiver here (03). |
| 7 | Performance SLOs tested | defined SLOs met | **BLOCKED** | SLOs proposed, **not approved** (04). Orchestration and API parts measured (05): 0% errors up to 200 users, tool P95 within 1 s up to 50 users. The end-to-end P95 needs the live model. |
| 8 | Live model E8 | approved model run and accepted | **BLOCKED** | no authorised endpoint or configured model (06) |
| 9 | Deployment and rollback | verified | **PASS** | D1 (PR #71): each version serves the other's data. D2: config check, refusal without a master key, health and readiness in about 1 s, token-only scraping, graceful SIGTERM shutdown (3 of 3), monitoring after restart, encrypted backup → check → restore → a new instance serves the restored workspace. |
| 10 | CI checks | required checks pass | **PENDING** | local equivalents pass (569 tests, lint, types); the CI run happens on the PR |
| 11 | Monthly availability | — | **NOT TESTED** | needs production traffic over 30 days; never inferred from a short test |

## Deployment observations (D2 and configuration review)

| Item | Finding |
|---|---|
| Secure configuration | Master key is required, or the server refuses to start. Secrets come from env or files. The monitoring token is optional and opens one route only. |
| Health and readiness | `/health` is cheap, with no authentication. `/ready` checks migrations, database, disk, storage and scheduler, returning status codes only. The Dockerfile has a HEALTHCHECK on `/health`. |
| Graceful shutdown | Uvicorn completes the lifespan shutdown (background tasks stopped, database closed) in about 0.2 s, then re-raises SIGTERM (exit status −15 is expected). |
| Database lifecycle | Per-thread connections are now closed when their thread ends (RT-05); all are closed at shutdown. |
| Resource limits | **Not set** in `server/docker-compose.yml` (no `mem_limit` or `cpus`). Recommended: 2 GB memory and 2 CPUs for one instance, based on L1 RSS up to about 620 MB at 200 users. |
| Backup and recovery | Encrypted backups, an integrity check and restore all work (D2). The restore needs the same `DESK_MASTER_KEY`. |

## What changes the decision

1. Run E8 live, with approved credentials, for each supported model, and have the owner approve its thresholds.
2. Bring the monitoring overlay up in staging with an authorised test receiver. Record firing, routing, inhibition and resolution (03, "How to complete the validation").
3. The owner approves the SLOs (04). Then measure end-to-end latency with the chosen model and an out-of-process load generator (k6 against staging).
4. CI green on the PR, and a maintainer's review.
