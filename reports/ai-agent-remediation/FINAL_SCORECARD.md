# Final scorecard

Same method, bands and weights as the audit's `AI_AGENT_SCORECARD.md`. A category with no adequate evidence gets no
score; "provisional" means the evidence is partial. Scores are judgements made against the evidence cited, not
counts of passing tests, and passing scripted tests does not earn 100.

| Category | Weight | Before | After | Evidence and what still holds the score down |
|---|---:|---:|---:|---|
| Accuracy (agent answers) | 20 | not scored | **not scored** | Needs a live model; E8 blocked. |
| Financial grounding | 15 | 70 | **85** | E1 1,385/1,385; E2 86/86; month substitution gone; E10 precision 100%, recall 99.83%. Against: the figure check is advisory, not blocking; the 91.25 coincidence; E10 is synthetic. |
| Security | 15 | 68 | **84** | AI-01, AI-02, AI-09 closed; E7 4/4; E5 0/657; 100 existing security tests pass. Against: model-level injection untested; no structural envelope for tool output (AI-22). |
| Reliability | 15 | 45 | **78** | E3 17/17; completion evidence in both adapters; classified retries; tool exceptions contained. Against: DB tools on the event loop; restart recovery is single-instance; real provider failure rates unmeasured. |
| Tool usage (engine side) | 8 | 72 (prov.) | **80 (provisional)** | Repeated reads reused, writes never; calculators off the loop with real timeouts. Model tool choice untested. |
| Context management | 7 | 55 | **72** | Early user facts carried past the window; names moved out of the system role. Against: no token budget, no summary of earlier answers and tool results. |
| Latency (engine overhead) | 5 | 88 (prov.) | **86 (provisional)** | +0–4 ms P50 per turn; no stall from a blocking calculator. End-to-end and TTFT with a real model unmeasured. |
| Observability | 7 | 40 | **55** | Run outcome and error code; per-answer timing, attempts, finish. Against: no metrics endpoint, no tracing, no dashboards. |
| Scalability | 4 | 55 (prov.) | **60 (provisional)** | Tasks released; 0.6 KB/conversation. Single-process SQLite unchanged; multi-host untested. |
| Maintainability | 4 | 80 | **82** | One skill-authorization lookup instead of three; figure logic in its own module; 56 new tests. |

## Overall

Weighted over the 9 scored categories (weights 80 of 100):

(85·15 + 84·15 + 78·15 + 80·8 + 72·7 + 86·5 + 55·7 + 60·4 + 82·4) / 80 = 6,232 / 80 = **77.9 → 78 / 100 (provisional)**

Before: 4,966 / 80 = **62.1 → 62 / 100 (provisional)**.

The band moves from "Adequate with gaps" to "Strong: measured, open MEDIUM issues only" for the measured part.
The largest category, the accuracy of answers from a real model, is still unscored, and the score could move in
either direction once E8 runs. **This score is not a production-readiness verdict**; see PRODUCTION_READINESS.md.
