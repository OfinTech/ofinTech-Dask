# Executive overview and drill-down

The overview is the landing page of every workspace. It is written for someone who runs the business and may have
no accounting background, and it answers five questions from the general ledger:

1. **How is the business performing?** A health score from 0 to 100 with a one-word label (strong, stable, needs
   attention, at risk) and the reasons behind it.
2. **How much money does the company have?** Cash at the end of the month, days of cash on hand, operating and free
   cash flow, what customers owe and what is owed to suppliers.
3. **Is the company profitable?** Revenue, expenses and net profit for the month against last month and the same
   month last year, year to date, and the three-month margin.
4. **What needs attention?** KPIs off target, an unbalanced ledger, account roles not yet confirmed.
5. **What should management do next?** Approvals waiting, forecasts waiting for sign-off, open month-end closes,
   project budgets that run out before their planned end.

Every figure has a one-line explanation in business language, and every figure says what kind of number it is.

## Provenance

| Badge | Meaning |
|---|---|
| Actual | Booked in the ledger, with account roles confirmed by the finance team under Financial analysis → Account roles. |
| Unverified | Booked in the ledger, but some account roles are still the engine's suggestion, or the ledger does not balance. |
| Budget | From the approved forecast marked as the plan. |
| Forecast | From the approved forecast. |
| AI estimate | Observations the workspace's model wrote from the figures on the page, on request. Never a financial result; never stored as one. |

An estimate is never shown as an actual result.

## The health score

Each signal is judged by the finance lead's target when one is set (Financial analysis → KPI → target and warning
level); until then by a reference level, so the score exists from the first ledger and tightens as targets are set:

| Signal | Good | Watch |
|---|---|---|
| Net profit margin (3 months) | ≥ 5% | ≥ 0% |
| Revenue growth (year on year) | ≥ 0% | ≥ −5% |
| Operating cash flow (3 months) | > 0 | — |
| Days of cash on hand | ≥ 60 | ≥ 30 |
| Current ratio | ≥ 1.2 | ≥ 1.0 |
| Receivables more than 30 days overdue | ≤ 10% | ≤ 25% |
| Collection rate | ≥ 85% | ≥ 70% |
| Debt to equity | ≤ 1.0 | ≤ 2.0 |
| Interest cover | ≥ 3× | ≥ 1.5× |
| Revenue from top 5 customers | ≤ 50% | ≤ 70% |

Every other KPI with a target counts too. Good scores 1, watch ½, act 0; the score is the average × 100. A month
closed at a loss caps the score at 59; fewer than 30 days of cash caps it at 39. Labels: ≥ 80 strong, ≥ 60 stable,
≥ 40 needs attention, otherwise at risk.

## Drill-down

Every path keeps the reporting month in the address (`?month=YYYY-MM`) and shows breadcrumbs back to the overview.

| Level | Page | API (viewer role) |
|---|---|---|
| 1 | Overview | `GET /api/w/{ws}/overview?month=` |
| 2 | Profitability: revenue to net profit, with last month, last year, year to date, the plan and % of revenue | `GET …/overview/profitability?month=` |
| 3 | The accounts behind a line, with share and change | `GET …/overview/breakdown/{measure}?month=` |
| 4 | The ledger lines of one account in the month | `GET …/overview/transactions/{measure}?account=&month=` |
| 5 | One journal entry: its lines and roles, whether it balances, the source dataset, the audit trail, attached documents | `GET …/overview/entries/{entry}` |

`POST …/overview/insights?month=&lang=` asks the workspace's utility model (or default model) for three to five
observations from the overview figures only; the result is returned as an estimate and not stored.

Everything is read-only: nothing here is posted to the accounting system.

## Views

One application, three views of the navigation rail (docs/DESIGN.md):

| View | Default for | Shows |
|---|---|---|
| Executive | viewer | Overview, Assistant, Dashboards, Inbox, Search |
| Finance | admin, owner | plus Financial analysis, Cost centres, Month-end close, Data, Files, Documents |
| Accounting | editor | everything |

A view only changes what the rail shows. The person can switch at any time; the server checks the role on every
request, so a view never grants access.

## Acceptance

- CEO test: the health banner, the four figures and their notes are the first thing on the page; `e2e/ui_smoke.py`
  opens it, then follows a figure to one journal entry and back with the month preserved.
- Reconciliation: `engine/tests/test_overview.py` checks, on the demo company, that net profit on the overview equals
  the profit and loss total, each line equals the sum of its accounts, each account equals the sum of its ledger
  lines, and each entry balances.
- Bilingual: the smoke test renders the overview and the profitability page in Arabic.
