---
name: cash-flow-forecast
description: Build a 13-week direct cash flow forecast from receivables, payables, payroll and recurring items. Use for liquidity planning, runway and covenant headroom.
version: 1.0.0
tags:
- treasury
- forecast
---

# 13-week cash flow forecast

## Structure (weekly columns, week 1 starts next Monday)
Opening cash → receipts → payments → net flow → closing cash → minimum cash buffer → headroom.

## Receipts
- Open invoices by due date, adjusted for each customer's actual payment behaviour (average days late from history if available).
- New sales: from budget or recent run-rate; mark as an assumption.
- Other receipts: tax refunds, asset sales, financing.

## Payments
- Open bills by due date (or by the payment-run day the user names).
- Payroll and payroll taxes on their actual dates.
- Rent, loans (`calc_loan_schedule`), subscriptions, tax payments.
- Capital expenditure the user confirms.

## Rules
- Use real balances from tools for opening cash, receivables and payables; every other figure is an assumption listed in an "Assumptions" table with its source.
- Show the lowest closing balance and the week it occurs.
- Offer a downside scenario (e.g. receipts 2 weeks later, sales −15%).
- Offer to save the forecast as a dataset and dashboard, and a weekly workflow to refresh it.
