---
name: budget-builder
description: Build a driver-based annual budget or reforecast from history and assumptions. Use for budgeting and planning cycles.
version: 1.0.0
tags:
- fp&a
- budget
---

# Budget builder

1. Agree scope: entity, fiscal year (workspace profile), currency, level of detail (account or section).
2. Baseline from history: last 12 months by account (`query_dataset` grouped by month). Remove one-offs the user identifies.
3. Drivers:
   - revenue = volume × price (or customers × average revenue), with seasonality from history;
   - cost of sales as % of revenue or per unit;
   - payroll = headcount plan × cost per head incl. employer costs, with start dates;
   - other operating costs: run-rate + known changes (contracts, inflation % the user gives).
4. Build monthly figures; check totals; show the key assumptions table.
5. Sanity checks: margin vs history, growth vs market, cash impact.
6. Save as a dataset (budget lines) so dashboards and `calc_variance` can compare actuals to it.
