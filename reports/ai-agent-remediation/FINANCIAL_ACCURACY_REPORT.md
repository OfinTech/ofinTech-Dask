# Financial accuracy

Covers AI-05 (figure verification), AI-13 to AI-17 and AI-20 (calculator and ledger edge cases).
The golden financial tests were **not** changed and still pass.

## Calculators and ledger (E1, E2)

| Finding | Before | After | What the tool now does |
|---|---|---|---|
| AI-13 multiple IRRs | a single IRR returned for non-conventional flows (one of several roots) | 30 / 30 E1 cases correct | counts sign changes, then scans a log-spaced grid (4,000 points) and bisects every bracket; with more than one root, `irr` is `None`, `irr_status` is `multiple`, every root is listed, and the text says "IRR: not unique (multiple IRRs)". XIRR the same. |
| AI-14 IRR/XIRR above 1,000% | "no IRR" | correct | the bracket widens to 1e6 (100,000,000%), with an overflow-safe NPV used only for the root search |
| AI-15 growth from a negative or zero base | sign-inverted growth; division by zero | correct | growth = (cur − prev) / \|prev\|; from zero → n/a; CAGR with a non-positive end says it is not meaningful |
| AI-16 rate given as a percentage (e.g. tax 15) | 1,500% tax applied silently | refused | rates ≥ 1 are refused with "must be a decimal fraction (0.15 for 15%)"; negative tax/loan rates, cost ≤ 0, negative salvage and salvage above cost are refused |
| AI-17 month outside the ledger | another month's figures substituted silently | refused | "No ledger data for {month}: the ledger covers {span}. No figures are given for {month}…"; bad formats get a format message |
| AI-20 ratios mutate input | arguments altered | fixed | reads with `.get`, no `pop` |

| Evaluation | `main` | This branch |
|---|---:|---:|
| E1 calculators, corrected ground truth (1,385 cases: 1,331 standard + 54 edge) | 1,355 | **1,385** |
| E1 as audited (faulty XIRR ground truth) | 1,359 | — (see below) |
| E2 ledger KPIs vs independent ground truth | 85 / 86 | **86 / 86** |
| New edge-case unit tests (`test_finance_edge_cases.py`) | 13 fail | **13 pass** |

**E1 ground truth correction.** The audit's E1 found XIRR roots by scanning only up to 1,000%. Four generated cases
have a real XIRR above that, so the audit expected "none" and counted the old code's failure to find one as a pass.
The corrected ground truth bisects in 60-digit decimal arithmetic up to 1e8%. Case #29 checked by hand: XIRR 6,716.4%,
XNPV at that rate −2.5×10⁻¹¹. With that correction the old code scores 1,355 (four fewer), and this branch 1,385.

## Figure verification (AI-05)

**Root cause.** The old check pulled every number out of the answer and accepted it if the same number appeared
anywhere in tool output, ignoring unit and precision, and treating the user's own messages as evidence. It missed
mis-copied amounts, cross-unit coincidences (a ratio of 0.25 "verifying" 25%), wrong day counts and figures the user
asserted, and it flagged correct rounding and correct arithmetic.

**Fix.** `engine/desk_engine/agent/figures.py`:
- parses each figure with its kind (amount, percent, ratio, count) and unit, masking dates, citations and list markers;
- matches only like with like, with a tolerance set by the precision shown ("1.2m" covers 1.15m–1.25m; "1,234,567" does not cover 1,234,000);
- accepts derived figures (sum, difference, share and change of verified figures stated in the same answer, chained);
- classifies each figure as **tool**, **derived**, **memory**, **user** or **unsupported**.

The answer payload carries `unverified_figures`, `user_figures` and `figure_provenance`. The app shows the user's
own figures under "From your messages, not checked against the books".

### E10: adversarial figure-check evaluation (new; `evals/e10_figure_provenance.py`, seed 1113)

24,000 synthetic answers in 16 classes of 1,500, over evidence with 79 amounts, 28 percentages, 16 ratios and
12 day counts. The old check is the exact code from `bd1e952` (`evals/baseline_figure_check.py`).

| Class | Expected | Old correct | New correct |
|---|---|---:|---:|
| Amount copied | pass | 100% | 100% |
| Amount rounded / k / m | pass | 87.0% | 100% |
| Percentage copied | pass | 100% | 100% |
| Ratio copied | pass | 100% | 100% |
| Day count copied | pass | 100% | 100% |
| Derived from stated figures | pass | 37.1% | 100% |
| User's own figure | user | 100% | 100% |
| Fabricated amount | flag | 92.5% | 100% |
| Amount mis-copied by 0.01%–0.45% | flag | 0.0% | 100% |
| Fabricated percentage | flag | 41.4% | 100% |
| Cross-unit percentage (ratio × 100) | flag | 0.0% | 100% |
| Cross-unit percentage (day count) | flag | 0.0% | 100% |
| Wrong day count | flag | 0.0% | **98.47%** |
| User-asserted figure repeated as fact | user, not verified | 0.0% | 100% |
| Wrong arithmetic on stated figures | flag | 58.3% | 100% |
| Result of figures the answer does not state | flag | 75.0% | 100% |

| | Old | New |
|---|---:|---:|
| Precision | 77.89% | **100%** |
| Recall | 29.69% | **99.83%** |
| False-positive rate | 12.64% | **0%** |
| False negatives | 9,492 | **23** |

**The 23 misses** are all "91 days" in the wrong-day-count class: the KPI tool's text states the formula constant
91.25 (days in a quarter), and 91 is within the rounding tolerance of 91.25 shown to two decimals. This is a known,
documented limitation; it needs the tool output to mark constants, which is not done here.

**Limits.** E10 is synthetic: it measures the checker against figures whose truth is known by construction, not
answers from a real model. The check is still **advisory**: it marks figures, it does not block an answer.
