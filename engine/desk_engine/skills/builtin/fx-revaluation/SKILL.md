---
name: fx-revaluation
description: Revalue foreign-currency balances at period end and explain realised vs unrealised FX gains and losses. Use for multi-currency closes.
version: 1.0.0
tags:
- close
- fx
---

# Foreign currency revaluation

- Monetary items (cash, receivables, payables, loans) in foreign currency are retranslated at the closing rate; differences go to profit or loss (unrealised until settled).
- Non-monetary items at historical cost (inventory, fixed assets, prepayments) are not retranslated.
- Realised gains/losses arise on settlement: difference between the booking rate and the settlement rate.

Steps:
1. List open foreign-currency balances by currency (connector or dataset).
2. Get closing rates from the user or their chosen source (the app has no live FX feed) and record source and date.
3. Compute: balance_fc × closing rate − carrying amount in base currency (`calc_currency_convert` helps).
4. Propose the journal (by currency) and the reversal on day 1 if the user's system uses reversing revaluations.
5. For group reporting, translation of foreign operations goes to other comprehensive income (IFRS IAS 21 / ASC 830) — mention when consolidating.
