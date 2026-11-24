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

## Thirteen weeks of cash, by the direct method

The forecast above carries a plan forward by month. A lender or a board often asks for something else: cash week
by week for the next thirteen weeks, from what is actually owed and owing. The assistant's read-only
`weekly_cash_forecast` tool works that out from the general ledger (`analysis/weekly_cash.py`). It needs no plan
and no setup, and it does not replace the monthly forecast.

It starts the day after a month end (the latest month unless another is asked for) and gives, for each of
thirteen weeks, opening cash, receipts, bills, standing payments and closing cash, and the first week the balance
falls below zero or below a floor you give.

| Line | Where it comes from |
|---|---|
| Receipts | Each open customer invoice, in the week it is expected: the invoice date plus the days that customer has taken to pay (its invoices settled in the last twelve months, weighted by amount; at least three of them), or plus the payment terms where there is no such history |
| Bills | Each open supplier bill, placed the same way from the days this company has taken to pay that supplier |
| Standing payments | Payments made straight from the bank to the same account in each of the last three months for amounts within 20% of their middle value (rent, payroll, a loan instalment), or every third month three times running (a quarterly tax). Carried forward on the same days at the latest amount |

Settlements are set against the oldest open document first, as the ageing report does, so the open invoices and
bills add up to the receivables and payables in the ledger. A credit note closes an invoice and is not counted as
a payment. Money received ahead of an invoice covers that customer's next one.

**What it assumes, and says in every answer**

- The ledger carries no due date, so a customer or supplier without a history is placed by terms: 30 days unless
  the question gives others (`customer_terms_days`, `supplier_terms_days`).
- An invoice or bill already past its expected date is put in week 1, and the amount is stated.
- What falls after week 13 is stated and is in no week.

**What it leaves out, and says in every answer**

- Sales not yet invoiced and bills not yet received. Later weeks therefore understate both sides; for scale the
  answer gives the monthly average of the last three months for money received from customers, paid to suppliers,
  other money received and other payments.
- Payments with no pattern, and payments made every month for amounts too far apart to carry: these are named.
- A receivable or payable balance with no document behind it (an opening balance): the amount is stated and given
  no week.

With fewer than three months of ledger the standing payments cannot be found. The answer then gives receipts and
bills by week and **no weekly balance**, rather than a balance that silently leaves rent and payroll out.

Every figure traces to entries: each placed invoice and bill carries its entry reference, its date, the days used
and why; each standing payment lists the past entries it was found from and the dates it is carried to. It is
arithmetic on the ledger, not a statistical model, and it changes nothing.
