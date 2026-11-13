# 09 · Final scorecard

The method, bands and weights are the audit's own. A category moves only on new evidence from this phase. Model
intelligence stays **unscored**.

| Category | Weight | Audit | Remediation | Readiness | **Final** | New evidence (+) and limits (−) |
|---|---:|---:|---:|---:|---:|---|
| Accuracy (real model) | 20 | — | — | — | **not scored** | E8 blocked |
| Financial grounding | 15 | 70 | 85 | 86 | **86** | no new evidence beyond re-runs; financial metrics added |
| Security | 15 | 68 | 84 | 84 | **84** | + scrape token tested on a real process, monitoring leak tests. − model-level injection untested |
| Reliability | 15 | 45 | 78 | 83 | **84** | + L1 E: honest failure and immediate recovery for 3 model failure types, tool timeouts and slow DB; RT-05 leak fixed; D2 graceful shutdown and restore. − single instance; loop DB reads |
| Tool usage (engine) | 8 | 72 p | 80 p | 82 p | **82 (provisional)** | unchanged |
| Context management | 7 | 55 | 72 | 80 | **80** | + D: no drift over 60 turns; budget-use metric |
| Latency (engine) | 5 | 88 p | 86 p | 86 p | **86 (provisional)** | E6 within noise; real-model latency unknown |
| Observability | 7 | 40 | 55 | 72 | **78** | + every layer instrumented and tested, SLI rules, 19 alerts, 5 dashboards, runbook. − delivery not validated; no OTel, Tempo or Loki; panels not rendered |
| Scalability | 4 | 55 p | 60 p | 62 p | **66 (provisional)** | + 0 errors up to 200 concurrent users, bounded connections, measured ceiling (~80 actions/s per process). − single process, CPU-bound, no multi-host |
| Maintainability | 4 | 80 | 82 | 83 | **84** | + version-controlled monitoring, runbook, tests guarding rule and dashboard metric names |

(p = provisional)

## Overall

(86·15 + 84·15 + 84·15 + 82·8 + 80·7 + 86·5 + 78·7 + 66·4 + 84·4) / 80
= (1,290 + 1,260 + 1,260 + 656 + 560 + 430 + 546 + 264 + 336) / 80 = 6,602 / 80 = **82.5 → 83 / 100 (provisional)**

| Assessment | Score |
|---|---|
| Initial audit (PR #68) | 62 |
| Remediation (PR #70) | 78 |
| Production-readiness audit (PR #71) | 82 |
| **Final (this PR)** | **83 (provisional)** |

The small change is deliberate. This phase built and verified tooling, but the gates that matter most (live model,
alert delivery, approved SLOs) could not be closed in this environment. The score covers 80% of the weight. The
remaining 20%, real-model accuracy, can move the overall score in either direction. **The score is not the release
decision; 08 is.**
