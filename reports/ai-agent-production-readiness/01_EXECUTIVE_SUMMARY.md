# 01 · Executive summary

**Scope:** final hardening and production-readiness review of the OfinTech Desk AI agent, after the audit (PR #68) and
the remediation (PR #70, merged into `main` at `b9d0161`).
**Branch:** `fix/ai-agent-production-readiness`, based on `b9d0161`. **Nothing is merged.**

## Recommendation

**Do not release to enterprise financial environments yet.** The engine is in a strong state, but three mandatory
gates are not met:
- **Live-model evaluation: BLOCKED.** No authorised model endpoint was available.
- **Observability: FAIL.** No alerts exist.
- **Performance: NOT TESTED.** No agreed SLO exists.

**Score:** 62 (audit) → 78 (remediation) → **82 / 100 (provisional)**. The accuracy of a real model's answers (20% of
the weight) remains unscored.

## Baseline reproduced before any change

| Metric | Reported | Reproduced on `b9d0161` |
|---|---|---|
| Engine tests | 530 | **530 OK** |
| E1 | 1,385 / 1,385 | **1,385 / 1,385** |
| E2 | 86 / 86 | **86 / 86** |
| E3 | 17 / 17 | **17 / 17** |
| E7 | 4 / 4 | **4 / 4** |
| Figure precision / recall | 100% / 99.83% | **100% / 99.83%** |

**Environment:**
- Linux VM, 2 vCPU Intel Xeon @ 2.10 GHz, 7 GB RAM.
- Python 3.13.18, SQLite 3.53.1.
- Commands: `cd engine && python -m unittest discover -s tests -t .`; evaluations in `reports/ai-agent-audit/evals/`.
- Synthetic data and a scripted model throughout.

## What this review changed

| Change | Finding | Proof |
|---|---|---|
| 18 database-reading tools run off the event loop; their timeouts hold | AI-10 → **resolved for tools** | With 50 conversations each running a slow query, `/health` max wait went from 10.2 s to 97 ms, and throughput from 4.8 to 27 turns/s |
| History size budget and a labelled note of earlier answers | AI-11 → **resolved** | A maximal thread went from 820,062 to 122,135 characters, and the early fact was kept |
| Prometheus metrics (admin only, no tenant labels) and a per-run trace | AI-12 → **resolved in code** (alerts not done) | 5 tests |
| Cut or empty model replies refused in the documents answer, dashboard insights and file reviewer | AI-03 residual (RT-01) → fixed | 5 tests, all failing on `main` |
| Bank matching finds every valid pair | RT-04 → fixed | R1: 211 / 211 (`main` 210, one false match) |
| A connection leak in the first AI-10 version, caught by the benchmark | RT-02 → fixed before release | E6 RSS 640 → 466 MB |
| E8: provider kind, tool-failure scenario, full metrics | AI-04 tooling | Scorer: oracle 21 / 21, fabricator 4 / 21 |

**Tests:** 555 engine tests pass (25 new, 17 of them fail on `main`). Lint gates are clean. UI tests and translations
pass.

## Results

| Area | Result |
|---|---|
| Security | No open CRITICAL / HIGH; AI-01 and AI-02 re-verified; 154 security tests; 14 red-team categories; E5 0 / 657 exposures; E7 4 / 4 |
| Financial accuracy | E1 1,385 / 1,385; E2 86 / 86; R1 211 / 211; dashboard = agent 168 / 168; E10 precision 100%, recall 99.83% |
| Reliability | E3 17 / 17; 0 errors across 16 load stages up to 50 concurrent; E9 flat memory; rollback validated in both directions |
| Live model | **NOT EXECUTED — BLOCKED** (03) |

## Unresolved risks

See 09_REMAINING_RISKS.md. The main ones:
- **Real-model accuracy is unknown.**
- **There is no alerting.**
- **There are no SLOs.**
- The figure check is advisory only.
- The agent loop's own database reads still run on the event loop.
- The engine is single-process on SQLite.

## Reports

| Report | Covers |
|---|---|
| [02_FINDINGS_CLOSURE](02_FINDINGS_CLOSURE.md) | AI-01 to AI-20 |
| [03_LIVE_MODEL_EVALUATION](03_LIVE_MODEL_EVALUATION.md) | E8 and its blocker |
| [04_FINANCIAL_ACCURACY](04_FINANCIAL_ACCURACY.md) | Calculators, ledger, reconciliation, dashboard |
| [05_SECURITY_RED_TEAM](05_SECURITY_RED_TEAM.md) | Re-verification and red-team matrix |
| [06_PERFORMANCE_BENCHMARKS](06_PERFORMANCE_BENCHMARKS.md) | P1, E6, E9, proposed SLOs |
| [07_PRODUCTION_GATES](07_PRODUCTION_GATES.md) | The release decision |
| [08_FINAL_SCORECARD](08_FINAL_SCORECARD.md) | Score with evidence |
| [09_REMAINING_RISKS](09_REMAINING_RISKS.md) | Owners and next steps |

The evaluations are in `evals/`; the raw results are in `results/before` (`main`) and `results/after` (this branch).
