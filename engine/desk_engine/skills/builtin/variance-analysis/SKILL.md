---
name: variance-analysis
description: Explain budget vs actual or period-over-period variances with materiality thresholds and clear commentary. Use for monthly reviews, board packs and cost investigations.
version: 1.0.0
tags:
- fp&a
- reporting
---

# Variance analysis

1. Get actuals and the comparison (budget, forecast, prior period, prior year) for the same scope and currency.
2. Compute variances with `calc_variance` (set higher_is_better = false for costs).
3. Materiality: comment on lines where |variance| ≥ 10% **and** ≥ a threshold the user gives (default: 1% of revenue). Ask for the threshold if unknown.
4. For each material line explain **why**, split where possible into:
   - volume (more/fewer units, customers, hours),
   - price/rate (price changes, FX, wage rates),
   - mix,
   - timing (in the wrong month; will reverse),
   - one-offs.
   Use data to support each driver (`query_dataset` by account, customer, vendor or month). If the data cannot explain it, say "not explained by the data available" and suggest who to ask.
5. Commentary style: one sentence per line, number first: "Marketing 18,400 over budget (+23%), mainly the trade show in March (timing; budgeted in April)."
6. End with the net impact on operating profit and any forecast implications.
