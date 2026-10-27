---
name: ap-review
description: 'Review payables before a payment run: due dates, duplicates, early-payment discounts and cash impact. Use for payment proposals and AP controls.'
version: 1.0.0
tags:
- payables
- controls
---

# Payables review and payment proposal

1. Get open bills (connector or dataset) with vendor, number, dates, amount, currency.
2. Duplicate check: same vendor and amount within 30 days; same invoice number; same amount with reference differing only by spaces/punctuation. List suspects, do not delete anything.
3. Early-payment discounts: annualised cost of not taking a discount = discount% / (1 − discount%) × 365 / (full days − discount days). Recommend taking it when that exceeds the user's cost of funds.
4. Payment proposal: bills due before the next run date, ordered by due date; show total by currency and the effect on cash.
5. Controls to mention when relevant: new or changed vendor bank details need independent verification by phone using a known number (payment fraud); segregation between approver and payer.
6. Creating payments in an accounting or banking system is a write action requiring approval.
