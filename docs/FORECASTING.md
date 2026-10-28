# Forecasting

The finance team sets a forecast's assumptions; OfinTech Desk does the arithmetic. A forecast projects month by month:

- profit and loss;
- receivables, inventory and payables;
- cash.

The finance lead approves a forecast and can make it **the plan**. The KPIs *Revenue against plan* and *Forecast
accuracy* compare actual figures with the plan.

Forecasts are under **Financial analysis → Forecasts** and read the same general ledger as the KPIs (see
[FINANCIAL_ANALYSIS.md](FINANCIAL_ANALYSIS.md)).

## Assumptions

Every P&L line has a method:

| Method | What it does | Assumption |
|---|---|---|
| Monthly growth on the recent average | the average of the last *n* months, growing by *g* % a month, compounded | *g*, *n* (default 3) |
| Same month last year, plus growth | that month a year earlier × (1 + *g* %). Keeps the seasonality; needs 12 months of history | *g* |
| Percentage of another line | *p* % of a line above it, for example cost of sales 40% of revenue; tax as a % of profit before tax | *p*, the line |
| Average of recent months | the average of the last *n* months | *n* |
| Straight-line trend | a least-squares line through the last *n* months, extended | *n* (default 12) |
| Fixed amount each month | the same figure every month | amount |
| Month by month | figures typed in for each month | figures |

The lines are:

- revenue;
- cost of sales;
- payroll;
- other operating expenses;
- depreciation;
- other income;
- other expenses;
- interest;
- tax on profit.

A new forecast starts from the company's own history:

- revenue as last year's same month when there are 12 months of history, otherwise the 3-month average;
- cost of sales at its share of revenue over the last 12 months;
- tax at the effective rate over the last 12 months;
- other lines as 3-month averages;
- DSO, DIO and DPO at their latest values;
- capital spending and debt repayment at their 6-month averages.

**Working capital and cash** follow the same conventions as the KPIs:

- receivables = revenue of the last three months × DSO ÷ 91.25;
- inventory = cost of sales of the last three months × DIO ÷ 91.25;
- payables = purchases of the last three months × DPO ÷ 91.25, where forecast purchases = cost of sales + other
  operating expenses;
- cash = last month's cash + net profit + depreciation − increase in receivables − increase in inventory + increase
  in payables − capital spending − debt repayment.

Other balances stay as they are. The forecast reports:

- revenue, net profit, net margin and cash at the end of the period;
- the lowest cash and its month;
- the first month in which cash goes below zero, if any.

## Statistical reference

A statistical forecast of revenue is drawn next to the finance team's forecast, as a check:

- **Holt** (level and trend) with at least 6 months of history;
- **Holt-Winters** with a 12-month season when there are at least 24 months of history.

Its parameters are chosen on the history. Its error is measured on the last 3–6 months: it is fitted without those
months and its forecast is compared with what happened. It never replaces the assumptions.

## Approval, versions and scenarios

```
draft ──submit──▶ submitted ──approve (admin)──▶ approved ──▶ plan
  ▲                  │                               │
  └──return to draft─┘                               └──archive (admin)──▶ archived
```

- **Who does what.** Editors create and change drafts and submit them. Only an administrator, the finance lead, can
  approve, make the plan or archive. Every step is audited.
- **Approval.** The forecast is frozen: its results are stored as they were on that day, so later ledger changes do
  not rewrite an approved forecast.
- **New version.** Copies the assumptions into a new draft of the same scenario (v2, v3…).
- **New scenario.** Copies them into a draft with another name, for example *Upside* or *Downside*. Scenarios and
  versions of the same forecast are compared side by side.
- **Deleting.** A draft or submitted forecast can be deleted. An approved one is archived instead.

## Plan and accuracy

One approved forecast per ledger can be **the plan**. Its revenue becomes the `plan_revenue` measure, which these KPIs
use:

- `plan_variance` = revenue ÷ plan revenue − 1;
- `forecast_accuracy` = 1 − the average absolute percentage error of the last three months.

An approved forecast also shows its own accuracy:

- forecast against actual revenue and net profit for each closed month;
- the average percentage error;
- the average bias (above or below). This shows whether forecasts tend to be optimistic.

The latest ledger month is left out because it may still be open.

## The assistant

`forecast_report` lists the forecasts, or reports on one. The report gives:

- its status;
- the assumptions;
- revenue, profit and cash;
- the lowest cash month;
- the statistical reference;
- accuracy so far.

When several forecasts match a name, the assistant reads the plan first, then approved versions. It cannot create,
change or approve a forecast.

## Limits

- A forecast is a projection from the assumptions people set. It is not advice.
- One currency, monthly periods, up to 36 months.
- Other balance-sheet items (tax payable, accruals, prepayments) and new borrowing are held flat.
- Purchases are approximated as cost of sales + other operating expenses.
