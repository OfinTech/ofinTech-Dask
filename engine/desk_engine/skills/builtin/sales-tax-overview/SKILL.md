---
name: sales-tax-overview
description: 'Work through VAT, GST or sales tax questions: tax-inclusive/exclusive maths, input vs output tax, return reconciliation. Use when tax amounts or tax returns come up in any country.'
version: 1.0.0
tags:
- tax
---

# VAT, GST and sales tax

This skill gives general mechanics. Tax rules differ by country and change often: confirm the current rate and rules with the tax authority's official source or the user's adviser, and never present a rate from memory as authoritative.

Mechanics:
- Split amounts with `calc_sales_tax` (amount_includes_tax true/false).
- VAT/GST (credit-invoice systems): output tax on sales − recoverable input tax on purchases = payable (or refundable). Check: exempt vs zero-rated supplies, partial exemption, reverse charge on imported services, non-recoverable items (often entertainment, some vehicles).
- US-style sales tax: collected on taxable sales where the business has nexus; rates stack by state/county/city; exemption certificates for resale.
- Return reconciliation: tax control accounts in the ledger = return boxes; differences are timing (invoice vs payment basis), corrections or errors.

Output: the calculation table, the reconciliation, and a list of items needing confirmation with the source to check.
