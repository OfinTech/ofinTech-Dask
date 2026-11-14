# 01 · Executive decision

## NO-GO

**Score:** 83 / 100 (provisional); model accuracy is unscored.

**Branch:** `fix/ai-agent-release-blockers`, on PR #72's head (`ff0413c`).

**PR state:**
- PR #71 is merged into `main`.
- PR #72 is open. Its base is `fix/ai-agent-production-readiness`; retargeting it to `main` is now possible.
- Nothing was merged by this work.

| Blocker | Result here | What it still needs |
|---|---|---|
| Live E8 | **BLOCKED** | an authorised model endpoint and credentials (`DESK_EVAL_*`) |
| Alertmanager delivery | **BLOCKED** | a container runtime (or permission to run the upstream binaries) and an authorised test receiver URL |
| SLO approval | **PENDING** | the owner's decision on 04 (approval-ready) |
| Event loop | **FIXED** (with a documented trade-off) | — |
| Resource limits | **CONFIGURED**; runtime **unverified** | `docker inspect` on the target host |

## What was done

- **Event loop.**
  - **Profile:** 60 reads per turn ran on the loop.
  - **Fix:** tool availability checks, prompt context and history now run in a dedicated read pool.
  - **Result:** 22 reads per turn remain on the loop. With a slow database (2 ms per read), turn throughput is 7.0 → 15.8 per second, `/health` P95 2.3 s → 0.36 s, and turn P95 2.8 s → 1.2 s.
  - **Cost:** on a fast database with an instant model, throughput is 12–25% lower. `DESK_AGENT_OFFLOAD_READS=0` restores the old path.
- **Two defects caught and fixed before review,** each with a test:
  1. A shutdown crash: a connection was closed while a thread was mid-query (signal 11).
  2. Thread-pool starvation behind hung tools: a plain question waited 2.7 s.
- **Resource limits.** Engine: 2 CPUs, 2 GB, 512 MB reserved, 512 pids, 30 s stop grace. Caddy and the monitoring stack have limits too. They are sized from a real-process measurement: 274 MB and 11 threads at 100 users. The configuration is validated by `docker compose config`.
- **Monitoring.** Prometheus and Alertmanager could not be run: there is no Docker daemon, and running the downloaded upstream binaries was refused by the environment's permission system.
- **E8.** No credentials and no configured model. The evaluation and its scorer are ready (oracle 21/21, fabricator 4/21).

## Regression

| Check | Result |
|---|---|
| Engine tests | 574 / 574, twice, no crash |
| Security and regression suites | 213 / 213 |
| E1 | 1,385 / 1,385 |
| E2 | 86 / 86 |
| E3 | 17 / 17 |
| E5 | 0 / 657 exposures |
| E7 | 4 / 4 |
| R1 | 211 / 211 |
| F1 | 168 / 168 |
| D2 (deployment) | pass |
| ruff | clean |

## To reach GO

1. Run E8 with the approved model, with credentials from your secret manager, and approve its thresholds (02).
2. In staging, follow 03's procedure with an authorised receiver, and keep the payloads.
3. Approve, or change, the SLOs in 04.
4. After deploying, check the limits with `docker inspect` (06).
5. CI green on the PRs, then merge #72 and this PR in order.

## Reports

| Report | Covers |
|---|---|
| [02](02_E8_LIVE_MODEL_RESULTS.md) | Live model E8 |
| [03](03_ALERTMANAGER_VALIDATION.md) | Alertmanager validation |
| [04](04_SLO_APPROVAL_AND_VALIDATION.md) | SLO approval and validation |
| [05](05_EVENT_LOOP_OPTIMIZATION.md) | Event-loop optimisation |
| [06](06_RESOURCE_LIMITS.md) | Resource limits |
| [07](07_SECURITY_REGRESSION.md) | Security regression |
| [08](08_RELEASE_GATE_MATRIX.md) | Release gate matrix |
| [09](09_FINAL_SCORECARD.md) | Final scorecard |
| [10](10_OPERATIONS_HANDOVER.md) | Operations handover |
