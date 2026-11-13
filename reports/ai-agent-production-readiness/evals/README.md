# Production-readiness evaluations

Run from `engine/` (each script adds it to the path). All data is synthetic; every model is scripted.

| Script | What it measures |
|---|---|
| `p1_staged_load.py [out.json]` | Turn latency P50 / P95 / P99, throughput, errors and `/health` latency at 1, 5, 20 and 50 concurrent conversations, for four workloads (answer, calculator, KPI scorecard, a 200 ms database read) |
| `r1_reconciliation.py [out.json]` | Bank matching of the month-end close against 11 specified cases and 200 generated months with known pairs |
| `f1_dashboard_vs_agent.py [out.json]` | That the executive dashboard and the agent's KPI tool give identical values, KPI by KPI, month by month |
| `ai11_request_size.py` | Request size of the 60-message window filled with maximum-size messages |
| `d1_rollback.py write\|read <dir>` | Data written by one version is served by the other (run each phase from a different checkout) |

E1–E9 are in `reports/ai-agent-audit/evals/` (E8 extended in this branch), and E10 is in
`reports/ai-agent-remediation/evals/`.

`../results/before/` holds results on `main` (`b9d0161`); `../results/after/` holds results on this branch.
