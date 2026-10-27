---
name: expense-audit
description: Audit expenses or card transactions for policy breaches, duplicates and anomalies. Use for expense claim reviews and spend control.
version: 1.0.0
tags:
- controls
- audit
---

# Expense and spend audit

Inputs: an expenses or card-transactions dataset (date, employee/card, merchant, category, amount, currency, description) and the expense policy (ask the user, or use a policy skill they added).

Tests:
1. Policy limits per category (meals, hotels, travel class) and missing receipts if a receipt column exists.
2. Duplicates: same amount, merchant and date ± 1 day across claims or cards.
3. Split transactions just under an approval limit on the same day/merchant.
4. Weekend/holiday spend and unusual merchants (by category and frequency).
5. Round amounts and amounts just under thresholds.
6. Benford first-digit test only for large populations (> 500 items), as an indicator not proof.

Report findings as a table (test, item, amount, reason, suggested follow-up). Be careful with wording: these are exceptions to review, not accusations.
