---
name: financial-statement-analysis
description: 'Analyse a company''s financial statements: profitability, liquidity, leverage, efficiency, trends and red flags. Use for performance reviews, credit assessments and investor questions.'
version: 1.0.0
tags:
- analysis
- ratios
---

# Financial statement analysis

1. Gather income statement and balance sheet for at least two periods (connector reports or datasets). Note the accounting framework (IFRS, US GAAP, local GAAP) from the workspace profile.
2. Compute ratios with `calc_financial_ratios` for each period; growth with `calc_growth`.
3. Read the story:
   - Growth: revenue and gross profit trend; margin expansion or compression and why.
   - Quality of earnings: one-offs, capitalised costs, unusual other income.
   - Liquidity: current and quick ratios, cash conversion cycle.
   - Leverage and coverage: debt/equity, interest cover; covenant levels if the user provides them.
4. Red flags to check: receivables growing faster than revenue; inventory build-up; negative operating cash flow with positive profit; frequent "other" adjustments; large related-party balances.
5. Output: a short summary (3–5 bullets), a ratio table across periods, then details. State limits of the data (e.g. no cash flow statement).
