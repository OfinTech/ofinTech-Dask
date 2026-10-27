---
name: bank-reconciliation
description: 'Reconcile a bank statement to the ledger: match transactions, list unmatched items and explain the difference. Use for bank or card reconciliations.'
version: 1.0.0
tags:
- reconciliation
- cash
---

# Bank reconciliation

## Inputs
- Bank statement lines (imported CSV/Excel dataset, or a Gmail attachment imported with `gmail_import_attachment`).
- Ledger cash-account transactions for the same period (connector sync or an export).

## Method
1. Confirm opening balances agree (statement opening = ledger opening reconciled balance).
2. Match in passes, strongest first:
   - same amount and same date;
   - same amount, date within 3 days, reference/description overlap;
   - one-to-many (one deposit = several receipts) only when the totals agree exactly.
3. Classify what is left:
   - in bank, not in ledger: bank fees, interest, direct debits, customer receipts not yet recorded;
   - in ledger, not in bank: outstanding cheques/payments, deposits in transit;
   - errors: transposed digits (a difference divisible by 9 is a hint), duplicates, wrong account.
4. Reconciliation statement:
   statement closing balance + deposits in transit − outstanding payments ± errors = ledger balance.
   The unexplained difference must be zero; if it is not, say so and show the amount.

## Output
- Summary KPIs: matched count, unmatched in bank, unmatched in ledger, unexplained difference.
- A table of unmatched items with a suggested action.
- Proposed journal entries for bank-only items; posting them is the user's decision.
