# 08 · Release gate matrix

| Release gate | Status | Evidence |
|---|---|---|
| Financial accuracy | **PASS** | E1 1,385 / 1,385; E2 86 / 86; R1 211 / 211; F1 168 / 168; E10; `test_financial_regression` (07) |
| Agent reliability | **PASS** | E3 17 / 17; L1 E: honest failures, immediate recovery; hung tools no longer starve others; shutdown race fixed (05) |
| Tenant isolation | **PASS** | E5 0 / 657; tenant suites; concurrency probe (07) |
| Live E8 | **BLOCKED** | no authorised endpoint, credentials or configured model (02) |
| Monitoring | **BLOCKED** | the engine's metrics endpoint was verified on a real process (D2: token scrape 200, 40 series); Prometheus collection not run (no runtime) (03) |
| Alert delivery | **BLOCKED** | no runtime for Prometheus or Alertmanager; downloading the upstream binaries was refused by the environment; no authorised receiver (03) |
| SLO approval | **PENDING** | no owner approval documented (04) |
| Load testing | **PASS (engine, scripted model)**; live-model load BLOCKED | L1 A–E, P4, R2: 0 errors at 10–200 users; end-to-end with a real model not possible (04, 05, 06) |
| Event-loop safety | **PASS** | P3 60 → 22 reads per turn on the loop; slow-DB `/health` P95 2.3 s → 0.36 s; regression tests; trade-off documented (05) |
| Resource limits | **BLOCKED** | configuration validated (`docker compose config`); runtime enforcement not verifiable without Docker (06) |
| Deployment rollback | **PASS** | no schema change; D1 (PR #71); D2 re-run on this branch (graceful shutdown 3 of 3, backup and restore); off switch `DESK_AGENT_OFFLOAD_READS=0` |

## Decision: **NO-GO**

Not passed:
- E8 live evaluation: BLOCKED.
- Monitoring collection and alert delivery: BLOCKED.
- SLO approval: PENDING.
- Runtime enforcement of the resource limits: BLOCKED.

Each needs something this environment cannot provide: model access, a container runtime, an authorised receiver,
an owner's approval.
