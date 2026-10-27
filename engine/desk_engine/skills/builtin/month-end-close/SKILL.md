---
name: month-end-close
description: 'Run or review a month-end close: checklist, reconciliations, accruals, cut-off and sign-off. Use when the user asks to close a month or check close readiness.'
version: 1.0.0
tags:
- close
- controls
---

# Month-end close

Goal: a closed period whose balances are reconciled, explained and approved, with an evidence trail.

## Before you start
- Confirm the period (month and fiscal year from the workspace profile) and the entity.
- Ask which ledger is the source of truth (QuickBooks, Zoho Books, Xero or an imported trial balance).

## Checklist (adapt to the user's process)
1. **Cut-off**: sales and purchases dated in the period are recorded; goods received not invoiced are accrued.
2. **Bank and cash**: every bank account reconciled to the statement balance (use the `bank-reconciliation` skill).
3. **Receivables**: aging reviewed; doubtful debts provided for; credit notes posted.
4. **Payables**: supplier statements reconciled for the largest suppliers; duplicate bills checked.
5. **Accruals and prepayments**: recurring accruals posted; prepayments amortised.
6. **Fixed assets**: additions capitalised; depreciation run (`calc_depreciation` for checks).
7. **Payroll**: payroll journal and liabilities agree to payroll reports.
8. **Tax**: sales tax / VAT / GST control accounts agree to the return workings.
9. **Intercompany and FX**: balances agree between entities; foreign currency balances revalued at period-end rate.
10. **Review**: P&L and balance sheet compared to last month and budget (`variance-analysis`); unusual movements explained.
11. **Sign-off**: reviewer and date recorded; period locked in the accounting system.

## How to work
- Pull figures with tools (`accounting_report`, `query_dataset`); never estimate balances.
- Present the checklist as a table: step, status (done / open / not applicable), evidence, owner.
- Anything you cannot verify from data is "open", with the question that resolves it.
- Locking a period or posting journals changes the books: describe the entry and let the user approve or do it.
