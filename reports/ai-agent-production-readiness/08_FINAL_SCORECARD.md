# 08 · Final scorecard

Same method, bands and weights as `reports/ai-agent-audit/AI_AGENT_SCORECARD.md`. A score moves only where this
review produced new evidence (a test that fails before and passes after, a measurement, a found defect). It does not
move because a commit exists. Model intelligence stays **unscored**: no live model was evaluated.

| Category | Weight | Audit (PR #68) | Remediation (PR #70) | **Now** | New evidence that moved it, and what still holds it down |
|---|---:|---:|---:|---:|---|
| Accuracy (agent answers, real model) | 20 | not scored | not scored | **not scored** | E8 blocked |
| Financial grounding | 15 | 70 | 85 | **86** | + R1 found and fixed a missed-pair defect (211 / 211); F1 dashboard = agent (168 / 168); insights can no longer be cut. − Figure check advisory; E10 and R1 synthetic; no currency in reconciliation. |
| Security | 15 | 68 | 84 | **84** | Re-verified (154 tests, E5, E7, 14 red-team categories), no new HIGH. Unchanged limits: model-level injection untested; no tool-output envelope (AI-22). |
| Reliability | 15 | 45 | 78 | **83** | + AI-10 closed for tools (P1: the server stays responsive, 4.8 → 27 turns/s under slow queries); AI-03 closed in 3 more paths; a leak caught by E6; rollback validated (D1). − Loop's own DB reads; single-instance recovery; real provider failure rates unmeasured. |
| Tool usage (engine side) | 8 | 72 (prov.) | 80 (prov.) | **82 (provisional)** | + Timeouts hold for 18 DB tools; tool duration and status measured. − Model tool choice untested. |
| Context management | 7 | 55 | 72 | **80** | + History size budget (820k → 122k characters); earlier answers carried and labelled. − Characters, not tokenizer counts; no per-model context window; truncation, not summary. |
| Latency (engine overhead) | 5 | 88 (prov.) | 86 (prov.) | **86 (provisional)** | E6 unchanged within noise; P1 better under slow queries, similar for CPU-bound KPI work. Real-model TTFT unmeasured. |
| Observability | 7 | 40 | 55 | **72** | + Prometheus metrics (model, retries, TTFT, tools, runs, loop lag) without tenant labels, admin-only; per-run trace. − No alerts, no OpenTelemetry, in-memory only. |
| Scalability | 4 | 55 (prov.) | 60 (prov.) | **62 (provisional)** | + 0 errors at 50 concurrent in four workloads on 2 vCPU; bounded connections. − Single process on SQLite; GIL-bound KPI work. |
| Maintainability | 4 | 80 | 82 | **83** | + One `require_complete` for every model path; one `offload` flag; 25 new tests (17 fail on `main`). |

## Overall

Weighted over the 9 scored categories (weights 80 of 100, as before):

(86·15 + 84·15 + 83·15 + 82·8 + 80·7 + 86·5 + 72·7 + 62·4 + 83·4) / 80
= (1,290 + 1,260 + 1,245 + 656 + 560 + 430 + 504 + 248 + 332) / 80 = 6,525 / 80 = **81.6 → 82 / 100 (provisional)**

| Assessment | Score |
|---|---|
| Original audit (PR #68) | 62 / 100 (provisional) |
| Remediation (PR #70) | 78 / 100 (provisional) |
| **Current (this review)** | **82 / 100 (provisional)** |

Band: "Strong: measured, open MEDIUM issues only", for the part that could be measured. The largest category (20%),
the accuracy of a real model's answers, has no evidence. Once E8 runs, the overall score can move in either
direction. **The score is not a release decision**; 07_PRODUCTION_GATES.md is, and it says not production-ready.
