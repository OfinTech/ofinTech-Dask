---
name: cash-flow-forecast
description: Build a 13-week direct cash flow forecast from receivables, payables, payroll and recurring items. Use for liquidity planning, runway and covenant headroom.
version: 1.1.0
tags:
- treasury
- forecast
---

# 13-week cash flow forecast

## Start from the ledger
When the workspace has a general ledger, call `weekly_cash_forecast` first, with the terms and the minimum cash
buffer (`floor`) if the user gave them. It returns the thirteen weeks from open invoices, open bills and standing
payments, with the entries behind each figure, what it assumed and what it leaves out. Report its figures and its
limits as given: do not rebuild or re-add them. Its weeks start the day after the month end it reads; say so.
Everything below is what the user may add on top. Each addition is an assumption, kept apart from the tool's
figures, listed with its source and added with a calculator tool.

## Structure (weekly columns; without a ledger, week 1 starts next Monday)
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
