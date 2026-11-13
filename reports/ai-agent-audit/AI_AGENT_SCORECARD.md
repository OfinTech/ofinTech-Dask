# OfinTech Desk AI Agent: scorecard

## How the scores work

Each category is scored 0–100 against the criteria below, using only the executed evidence named. A category
whose central question needs a live model is scored only on the part the evidence covers, and is marked
**provisional**. A category with no adequate evidence gets **no score**. The overall score is the weighted mean of
the scored categories, with weights renormalised over those that have a score.

| Score band | Meaning |
|---|---|
| 90–100 | Enterprise-grade: measured, no open issue above LOW |
| 75–89 | Strong: measured, open MEDIUM issues only |
| 60–74 | Adequate with gaps: an open HIGH issue, or important parts unmeasured |
| 40–59 | Weak: several failures on core behaviour |
| < 40 | Not fit for purpose |

## Scores

| Category | Weight | Score | Criteria and evidence |
|---|---:|---:|---|
| Accuracy (answers of the agent as a whole) | 20 | **not scored** | Needs a live model (E8 written and validated, not run). The deterministic layer is scored under financial grounding. |
| Financial grounding | 15 | **70** | Every KPI matched ground truth (E2 60/60, series 19/19); calculators exact on 1,331/1,331 standard cases (E1); unbalanced ledger reported. Against: 26 edge-case failures in 5 classes (E1); a missing month silently substituted (E2); the figure check misses near misses, small counts and user-asserted figures (0%) and 13.4% of fabricated percentages (E4); it is advisory only. |
| Security | 15 | **68** | Tenant isolation held (E5 0/657; 100 existing tests pass); writes bound to approvals; payment and sealed guards hold. Against: two HIGH bypasses of the skill-approval control and one MEDIUM system-role surface (E7, 4/4 probes succeeded). Model-level injection resistance not tested. |
| Reliability | 15 | **45** | Passes 7/17 E3 scenarios. Recovers 4/8 recoverable failures. Stores cut or empty model output as complete answers (HIGH). No retries. An unexpected tool exception loses the turn. Holds: invalid JSON, unknown tools, schema errors, async timeouts, runaway loops, valid pairing in long threads. |
| Tool usage (engine side) | 8 | **72 (provisional)** | Schema validation, role checks at offer and at call, timeouts, approval binding, audit of every call (E3, existing tests). Against: no de-duplication (E3), and blocking handlers defeat timeouts (E3). Whether models choose the right tool is not tested. |
| Context management | 7 | **55** | Request size bounded (E3: at most 47,704 characters over 80 turns), call/result pairing always valid, tool-group selection keeps requests small (E6 p50 ≈ 7,300 tokens). Against: a fixed 60-message window with no token budget or summary; a turn-1 fact is lost after about 15 turns (E3). |
| Latency (engine overhead) | 5 | **88 (provisional)** | Engine overhead P95 is 8.8–30.2 ms per turn and tools stay under 11 ms warm up to 100,000 ledger lines (E6). End-to-end and TTFT with a real model are not measured. Blocking handlers can add seconds under load (E3). |
| Observability | 7 | **40** | Has: audit rows for every tool run, denial and refusal, with `run_id`; token usage per model call. Missing: model and tool latency, TTFT, finish reason, retry counts, error categories, metrics endpoint, traces. |
| Scalability | 4 | **55 (provisional)** | 61 turns/s on 2 vCPU with an instant model; 1.6 KB/conversation retained (E6, E9). A single process on SQLite, and run recovery assumes one instance (`app.py` 134–136). Blocking handlers serialise the event loop. Multi-host not tested. |
| Maintainability | 4 | **80** | Clear module boundaries; tools declare effect, role, schema and timeout in one place; 472 engine tests plus golden financial tests; prompt caching designed in. Against: the skill-approval rule is enforced in one path and not in two others, so a rule lives in several places. |

## Overall

Weighted over the 9 scored categories (weights 80 of 100):

(70·15 + 68·15 + 45·15 + 72·8 + 55·7 + 88·5 + 40·7 + 55·4 + 80·4) / 80 = 4,966 / 80 = **62.1 → 62 / 100 (provisional)**

The score excludes the largest category, the accuracy of the agent's answers, because no evidence exists for it.
It would move in either direction once E8 runs. After the P0 items in the roadmap, the expected re-scores are:
- Security to about 85 (AI-01 and AI-02 closed, AI-09 still open).
- Reliability to about 65 (AI-03 closed). To about 80 if P1-1, P1-2 and P1-5 are also done.

Those are expectations to be confirmed by re-running E3 and E7, not results.

## Evidence index

| Code | File |
|---|---|
| E1 | `results/e1_calculators.json` |
| E2 | `results/e2_ledger_grounding.json` |
| E3 | `results/e3_reliability.json` |
| E4 | `results/e4_figure_check.json` |
| E5 | `results/e5_tenant_isolation.json` |
| E6 | `results/e6_benchmarks.json`, `results/e6_benchmarks.log` |
| E7 | `results/e7_instruction_surfaces.json` |
| E8 | `results/e8_scorer_validation.json` (scorer validation only) |
| E9 | `results/e9_memory_growth.json` |
| Existing tests | `results/existing_security_suites.txt` |
