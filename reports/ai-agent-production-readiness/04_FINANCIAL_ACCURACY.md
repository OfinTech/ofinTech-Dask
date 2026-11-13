# 04 · Financial accuracy

Every figure here is compared with independently computed values or an authoritative tool output. None is judged by
how plausible it sounds. **This covers the deterministic layer only** (tools, calculators, reconciliation, figure
check). Whether a real model uses that layer correctly is E8, which is blocked.

## Summary

| Capability | Evaluation | `main` (`b9d0161`) | This branch |
|---|---|---:|---:|
| Calculators (ratios, NPV/IRR/XIRR, loans, depreciation, tax, growth, FX) | E1, 1,385 cases | 1,385 | **1,385** |
| Ledger KPIs and series vs ground truth | E2 | 86 / 86 | **86 / 86** |
| Figure check (synthetic answers) | E10, 24,000 answers | P 100%, R 99.83% | **P 100%, R 99.83%** |
| Bank reconciliation | R1 (new), 211 cases | 210 / 211, 1 false match | **211 / 211, 0 false matches** |
| Dashboard vs agent figures | F1 (new), 14 months × 12 KPIs | — | **168 / 168 identical** |
| Golden financial tests (`test_financial_regression`, `test_financial_analysis`) | engine suite | pass | **pass (unchanged)** |

## A. Ask Your Finance (the agent's financial answers)

| Area | What is verified | Result |
|---|---|---|
| Natural-language queries, period comparisons, KPI explanations, cash flow, breakdowns | The tools the agent calls (`financial_kpis`, `kpi_detail`, `query_dataset`, `forecast_report`) against ground truth computed directly from the synthetic journal (E2) | 86 / 86 |
| Missing data | A month outside the ledger returns "No ledger data for …" and no figures (E2, AI-17) | pass |
| Source attribution | Every figure in an answer is classified as tool / derived / memory / user / unsupported (E10) | precision 100%, recall 99.83% |
| Refusal to fabricate | Unsupported figures are flagged and shown as unchecked, but **not blocked** (advisory) | see risks |
| Multi-turn, real explanations, real refusals | Needs a live model | **not tested (E8 blocked)** |
| Cut or empty answers | Never shown as complete, now also in the documents answer and dashboard insights | 5 new tests |

## B. Autonomous reconciliation

What exists in the repository: bank reconciliation inside the month-end close (`close/statement.py`). Statement lines
are matched to the ledger's bank-account lines on two conditions: the amount agrees to the cent, and the dates are at
most 5 days apart. The rest is reported as ledger-only (uncleared items) and statement-only (fees, interest,
unrecorded receipts), with the identity statement + ledger-only = book + statement-only.

Nothing is posted to the accounting system. Proposed entries (bank charges, interest) are proposals only.
`prepare_month_end_close` is editor-only.

No separate "autonomous reconciliation" agent, payout matching, or currency-aware matching exists in this
repository. These were **not assumed** and not evaluated.

**R1** (`evals/r1_reconciliation.py`): 11 hand-specified cases plus 200 generated months of 40 lines each, all with
known pairs.

| Case | Expected | `main` | Branch |
|---|---|---|---|
| Exact match | paired | pass | pass |
| Date offset ≤ 5 days | paired | pass | pass |
| Date offset of 6 days | both sides exceptions | pass | pass |
| Amount off by one cent | both sides exceptions | pass | pass |
| Duplicate posting in the ledger | one paired, duplicate reported as ledger-only | pass | pass |
| Duplicate line on the statement | one paired, duplicate statement-only | pass | pass |
| Ambiguous (two candidates) | nearest date paired, other ledger-only | pass | pass |
| **Nearest-first must not cost a match** | 2 pairs | **fail: 1 pair, 2 false exceptions** | pass |
| Bank charge only on the statement | statement-only | pass | pass |
| Cheque not yet cleared | ledger-only | pass | pass |
| Line in another month | ignored | pass | pass |
| 200 generated months | all pairs and exceptions | pass | pass |
| Reconciliation identity with a closing balance | difference 0.00 | pass | pass |

| Metric | `main` | Branch |
|---|---:|---:|
| Pair recall | 99.96% | **100%** |
| False matches (wrong pair) | 1 | **0** |
| Exception recall | 100% | 100% |

**Defect found and fixed (RT-04):** matching took the nearest ledger line for each statement line in turn. It could
take a line a later statement line needed, leaving both unmatched although a full pairing existed (two false
exceptions for an accountant to chase). Each amount now gets a maximum matching (augmenting paths, nearest date
still tried first, iterative so 3,000 equal amounts match in seconds). The rule itself is unchanged.

| Not supported by the product (documented, not defects of this change) | Effect |
|---|---|
| Currency | Statement lines have no currency. A statement in another currency matches nothing and shows as a large difference, which is visible but not explained. |
| Ambiguity flag | When two ledger lines qualify, the nearest wins and the other becomes an exception; nothing marks the pair as ambiguous. |
| Duplicate label | Duplicates surface as exceptions, not labelled "possible duplicate". |
| The agent's use of reconciliation | Only through the close summary; untested with a live model. |

## C. Financial intelligence

| Area | Evidence | Result |
|---|---|---|
| Ratios and KPI formulas | E1 (calculators) and E2 (35 scorecard KPIs from the journal) | pass |
| Trends, period-over-period | E2 series checks; growth edge cases (AI-15) | pass |
| Cash-flow figures | E2 (operating and free cash flow, cash, days cash) | pass |
| Data completeness | Missing-month refusal; ledger coverage stated in every refusal | pass |
| **Dashboard = agent** | F1: the executive dashboard (`GET /overview`) and the agent's `financial_kpis` tool give identical values for every KPI in every month (14 months, 168 values, the HarborLine demo ledger) | **168 / 168** |
| Dashboard insights | AI-written lines are labelled "estimate"; a cut or empty reply is now refused (502) instead of shown | new test |

Net margin and similar KPIs use a 3-month trailing window (`SUMN(net_profit, 3) / SUMN(revenue, 3)`) on both the
dashboard and the agent. They are not a single month's ratio. The agent must say so; that can only be checked live.

## Limits

- E10 and R1 are synthetic and measure the deterministic layer; they say nothing about a model's choices.
- The figure check marks unsupported figures; it does not stop an answer from being shown.
- One E10 class misses 23 of 1,500: a wrong "91 days" matches the formula constant 91.25.
