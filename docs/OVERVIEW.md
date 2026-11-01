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

## The CFO's pages (Statements)

Under **Statements** in the Finance and Accounting views, from the same ledger, so every figure agrees with the
overview and the KPI scorecard:

| Tab | What it shows | API (viewer role) |
|---|---|---|
| Statements | Profit and loss by month (twelve months and year to date, the plan in grey when one is approved, margins under each subtotal); balance sheet at month end (current and non-current assets and liabilities, equity as assets less liabilities, working capital, net debt); cash flow by month (opening, operating, investing, financing, net change, closing, free cash flow, money in and out). Every line opens the accounts behind it. | `GET …/overview/statements?month=&periods=` |
| Budget vs actual | Each profit and loss line against the approved plan for the month and the year to date, variances marked favourable or not (income over plan, cost under plan), and the accounts that drove the largest cost overruns. | `GET …/overview/budget?month=` |
| Receivables / Payables | Balances by customer or supplier at the month end, opening, invoiced or billed, settled, closing, aged 0–30, 31–60, 61–90 and 90+ days from the document date (each settlement is applied to that party's oldest open documents first), the oldest open document, and a check against the ledger balance. | `GET …/overview/parties/{receivables|payables}?month=` |
| Anomalies | Postings in the month that break the pattern of the previous months, above a materiality of 1% of average monthly revenue (at least 500): an unusual amount against the six-month average, a regular posting that is missing, a first posting to an account, and the same account, party and amount posted twice within a week. | `GET …/overview/anomalies?month=` |

The balance sheet always balances (assets − liabilities = equity by construction); the cash-flow statement reports
any movement of cash the classification cannot explain. Ratios, targets, forecasts, scenarios and account roles stay
under **Financial analysis**.

## The accountant's pages (General ledger)

| Tab | What it shows | API (viewer role) |
|---|---|---|
| Journal entries | Every entry with its date, type, description, party, accounts, lines, debits and credits; filtered by month (or all months), free text, type, account, party, minimum amount, or unbalanced only; 100 a page. An entry opens with its lines and roles, whether it balances, the source dataset and the audit trail. | `GET /api/w/{ws}/ledger/entries?month=&q=&type=&account=&party=&min=&unbalanced=1&offset=&limit=` |
| Accounts | The chart of accounts as the ledger uses it: code, name, class, role (confirmed or suggested), lines, the month's activity, and the month-end balance (or the month's amount for a cost or income account). Suspense and clearing accounts are flagged. | `GET …/ledger/accounts?month=` |
| Validation | The ledger checked the way a reviewer would, for a month or the whole ledger: entries that do not balance, single-line entries, lines without an account or a usable date, lines dated in the future, lines with both a debit and a credit, zero lines, balances on suspense or clearing accounts, accounts whose role is still a suggestion, balances on the wrong side (a bank or receivables account in credit, payables in debit), postings on the unusual side. Each check names its level and up to eight examples that open the entry or account. | `GET …/ledger/validation?month=` |

Nothing is changed from these pages; the ledger is read-only. Imports stay under Data, the bank reconciliation and
proposed entries under Month-end close, evidence under Documents and Files.

## The accounting manager's desk (Oversight)

One page, read-only, every item opening the page where the work is done (`GET /api/w/{ws}/oversight`):

- **Outstanding work:** approvals pending, open month-end close items by kind, forecasts waiting for sign-off,
  workflow runs failed or waiting, validation issues in the last month.
- **Reconciliation and close, last six months:** status (not started, in progress, closed), readiness, open
  items, statement lines matched and unmatched, unexplained difference, who closed it or when it was prepared.
- **Unmatched bank items** from the latest close with a statement: on the statement but not in the ledger, in the
  ledger but not on the statement.
- **Pending approvals**, forecasts and failed workflow runs.
- **Exceptions in the last month:** the validation checks with a count, and the anomalies.
- **Team, last 14 days:** actions per person from the audit log, and the latest twenty-five actions.
- **Supporting documents:** how many are searchable or failed. Documents are not yet linked to journal entries,
  which the page says plainly rather than reporting a count that would mean nothing.
- **Operation:** journal entries last month, ledger lines and freshness, average days after the month end to prepare
  a close, months closed, anomalies per entry.
- **Reporting:** statements, budget against actual, forecasts and close packs, with their state.

## Views

One application, three views of the navigation rail (docs/DESIGN.md):

| View | Default for | Shows |
|---|---|---|
| Executive | viewer | Overview, Assistant, Dashboards, Inbox, Search |
| Finance | admin, owner | plus Statements, Financial analysis, Cost centres, Month-end close, Oversight, Data, Files, Documents |
| Accounting | editor | everything, including General ledger and Oversight |

A view only changes what the rail shows. The person can switch at any time; the server checks the role on every
request, so a view never grants access.

## Acceptance

- CEO test: the health banner, the four figures and their notes are the first thing on the page; `e2e/ui_smoke.py`
  opens it, then follows a figure to one journal entry and back with the month preserved.
- Reconciliation: `engine/tests/test_overview.py` checks, on the demo company, that net profit on the overview equals
  the profit and loss total, each line equals the sum of its accounts, each account equals the sum of its ledger
  lines, and each entry balances.
- Accountant test: the general ledger is searched, filtered and opened down to one entry without leaving the
  professional tools (imports, close, documents, files stay where they are).
- Accounting manager test: the oversight page shows the close status per month, unmatched bank items, approvals,
  exceptions, team activity and outstanding work, each opening the page where it is handled.
- Bilingual: the smoke test renders the overview, profitability, receivables, general ledger and oversight pages
  in Arabic.
