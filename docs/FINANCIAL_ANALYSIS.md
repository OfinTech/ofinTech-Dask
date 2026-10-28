# Financial analysis

*Financial analysis* shows the company's performance as 35 standard KPIs, computed from the general ledger:

- growth and profitability;
- liquidity;
- working capital;
- leverage;
- cash flow;
- cost and control.

The finance lead sets the targets and can change any formula. Every value can be traced to the measures and ledger
accounts behind it. All arithmetic is done in code; the assistant only reads and explains the results.

It works on any chart of accounts, currency and fiscal year. KPI names and methods follow common international
practice, and no country-specific rules are built in. There are no industry benchmarks: comparisons are against the
company's own targets and history.

## The data

The analysis reads a **general-ledger dataset**, the same one cost centres use (see
[COST_CENTRES.md](COST_CENTRES.md#where-the-journal-lines-come-from)). It can come from:

- a QuickBooks Online, Xero or Odoo connection;
- the Open Accounting Connector;
- an Excel or CSV file with the general-ledger columns.

When there are several ledgers, an administrator chooses which one the analysis uses.

### Account roles

Every account has a **role**. Ratios need to know which accounts are cash, inventory, debt, payroll, depreciation and
so on, and accounting systems do not say this in a common way.

| Balance sheet | Profit and loss |
|---|---|
| cash, receivables, inventory, other current assets, fixed assets, other non-current assets, payables, other current liabilities, short-term debt, long-term debt, other non-current liabilities, equity | revenue, other income, cost of sales, payroll, other operating expenses, depreciation, interest, tax on profit, other expenses |

**Suggestions.** Roles are suggested from the account's class and from words in its name, in English, Arabic,
Spanish and French. For example:

- *Inventory*, *المخزون*, *Existencias* → inventory;
- *Bank loan – long term* → long-term debt;
- *Salaries*, *الرواتب*, *Salaires* → payroll;
- *Dotation aux amortissements* → depreciation.

**Confirmation.** People confirm suggestions or choose another role under *Account roles*. A balance-sheet account
cannot take a profit-and-loss role, and the other way round. Every change is audited.

### Month-end balances

The analysis builds a balance for every account at the end of every month from the journal lines.

- **Opening balances.** A ledger synced from a connection that also synced a balance sheet starts from that balance
  sheet: opening balance = balance on the balance-sheet date − movements up to that date. Accounts are matched by
  name, and the page says how many matched.
- **No balance sheet.** Balances start at zero, which is right for a ledger that begins with an opening entry.
- **Equity** is assets minus liabilities, so it includes profit that has not been closed yet.
- **Balance check.** The page warns when debits and credits do not agree.

### Cash flow

Cash flow comes from bank lines (accounts with the *cash* role). Each entry's net bank movement is classed by the
entry's other lines:

- **financing** when they touch debt or equity;
- **investing** when they touch fixed or other non-current assets;
- **operating** otherwise.

Transfers between bank accounts are left out.

## Measures

KPI formulas use these monthly measures:

- **Profit and loss:** `revenue`, `other_income`, `cogs`, `payroll`, `opex`, `depreciation`, `interest`, `tax`,
  `other_expense`.
- **Derived results:**
  - `gross_profit` = revenue − cogs;
  - `ebitda` = gross profit − payroll − opex;
  - `operating_profit` = EBITDA − depreciation;
  - `net_profit` = operating profit + other income − other expense − interest − tax;
  - `total_opex` = payroll + opex + depreciation.
- **Balances at month end:**
  - by role: `cash`, `receivables`, `inventory`, `other_current_assets`, `fixed_assets`,
    `other_non_current_assets`, `payables`, `other_current_liabilities`, `short_term_debt`, `long_term_debt`,
    `other_non_current_liabilities`;
  - totals: `current_assets`, `total_assets`, `current_liabilities`, `total_liabilities`, `debt`, `net_debt`,
    `equity`, `working_capital`.
- **Cash:**
  - `ocf`, `icf` and `fcf_fin` (operating, investing and financing);
  - `cash_in` and `cash_out`;
  - `burn` = −(operating + investing);
  - `debt_repaid`.
- **From payables and receivables:**
  - `purchases`: supplier bills raised;
  - `invoiced`: customer invoices raised;
  - `collections`: customer receipts.
- **Other:**
  - `top5_share`: revenue from the five largest customers over 12 months; needs customer names on most revenue
    lines;
  - `ar_overdue_pct` and `ap_overdue_pct`: from synced invoices and bills, latest month only;
  - `plan_revenue`: from the approved plan; see [FORECASTING.md](FORECASTING.md).

## The 35 KPIs

- Margins, cost ratios and turnover days use the **last three months** (91.25 days), so one unusual month does not
  swing them.
- Returns, coverage and cash conversion use **twelve months**.
- Percentages are fractions (0.25 = 25%).

| # | KPI | Formula | Shown as | Better |
|---|---|---|---|---|
| | **Growth and profitability** | | | |
| 1 | Revenue growth (year on year) (`revenue_growth`) | `revenue / PREV(revenue, 12) - 1` | percent | higher |
| 2 | Gross margin (`gross_margin`) | `SUMN(gross_profit, 3) / SUMN(revenue, 3)` | percent | higher |
| 3 | EBITDA margin (`ebitda_margin`) | `SUMN(ebitda, 3) / SUMN(revenue, 3)` | percent | higher |
| 4 | Operating margin (`operating_margin`) | `SUMN(operating_profit, 3) / SUMN(revenue, 3)` | percent | higher |
| 5 | Net profit margin (`net_margin`) | `SUMN(net_profit, 3) / SUMN(revenue, 3)` | percent | higher |
| 6 | Return on equity (`roe`) | `SUMN(net_profit, 12) / AVGBAL(equity)` | percent | higher |
| 7 | Return on assets (`roa`) | `SUMN(net_profit, 12) / AVGBAL(total_assets)` | percent | higher |
| 8 | Return on capital employed (`roce`) | `SUMN(operating_profit, 12) / AVGBAL(total_assets - current_liabilities)` | percent | higher |
| | **Liquidity** | | | |
| 9 | Current ratio (`current_ratio`) | `current_assets / current_liabilities` | ratio | higher |
| 10 | Quick ratio (`quick_ratio`) | `(current_assets - inventory) / current_liabilities` | ratio | higher |
| 11 | Cash ratio (`cash_ratio`) | `cash / current_liabilities` | ratio | higher |
| 12 | Working capital (`working_capital`) | `working_capital` | money | higher |
| 13 | Days of cash on hand (`days_cash`) | `cash / (SUMN(cash_out, 3) / 91.25)` | days | higher |
| | **Working capital** | | | |
| 14 | Days sales outstanding (DSO) (`dso`) | `receivables / SUMN(revenue, 3) * 91.25` | days | lower |
| 15 | Days payables outstanding (DPO) (`dpo`) | `payables / SUMN(purchases, 3) * 91.25` | days | higher |
| 16 | Days inventory outstanding (DIO) (`dio`) | `inventory / SUMN(cogs, 3) * 91.25` | days | lower |
| 17 | Cash conversion cycle (`cash_conversion_cycle`) | `dso + dio - dpo` | days | lower |
| 18 | Receivables more than 30 days overdue (`ar_overdue`) | `ar_overdue_pct` | percent | lower |
| 19 | Payables more than 30 days overdue (`ap_overdue`) | `ap_overdue_pct` | percent | lower |
| 20 | Collection rate (`collection_rate`) | `SUMN(collections, 3) / (PREV(receivables, 3) + SUMN(invoiced, 3))` | percent | higher |
| | **Leverage** | | | |
| 21 | Debt to equity (`debt_to_equity`) | `debt / equity` | ratio | lower |
| 22 | Net debt to EBITDA (`net_debt_to_ebitda`) | `net_debt / SUMN(ebitda, 12)` | ratio | lower |
| 23 | Interest cover (`interest_cover`) | `SUMN(operating_profit, 12) / SUMN(interest, 12)` | ratio | higher |
| 24 | Debt service coverage (DSCR) (`dscr`) | `SUMN(ocf, 12) / (SUMN(debt_repaid, 12) + SUMN(interest, 12))` | ratio | higher |
| 25 | Equity ratio (`equity_ratio`) | `equity / total_assets` | percent | higher |
| | **Cash flow** | | | |
| 26 | Operating cash flow (3 months) (`operating_cash_flow`) | `SUMN(ocf, 3)` | money | higher |
| 27 | Free cash flow (3 months) (`free_cash_flow`) | `SUMN(ocf, 3) + SUMN(icf, 3)` | money | higher |
| 28 | Cash runway (`cash_runway`) | `cash / IFPOS(SUMN(burn, 3) / 3)` | months | higher |
| 29 | Cash conversion of profit (`earnings_quality`) | `SUMN(ocf, 12) / SUMN(net_profit, 12)` | ratio | higher |
| | **Cost and control** | | | |
| 30 | Operating expenses to revenue (`opex_ratio`) | `SUMN(total_opex, 3) / SUMN(revenue, 3)` | percent | lower |
| 31 | Payroll to revenue (`payroll_ratio`) | `SUMN(payroll, 3) / SUMN(revenue, 3)` | percent | lower |
| 32 | Margin of safety over break-even (`margin_of_safety`) | `1 - SUMN(total_opex, 12) / SUMN(gross_profit, 12)` | percent | higher |
| 33 | Revenue against plan (`plan_variance`) | `revenue / plan_revenue - 1` | percent | higher |
| 34 | Forecast accuracy (revenue, 3 months) (`forecast_accuracy`) | `1 - AVGN(ABS(revenue - plan_revenue) / revenue, 3)` | percent | higher |
| 35 | Revenue from top 5 customers (`customer_concentration`) | `top5_share` | percent | lower |

Notes on some of the formulas:

- **Margin of safety** is 1 − break-even revenue ÷ revenue, where break-even revenue = operating expenses ÷ gross
  margin. The formula treats cost of sales as variable and operating expenses as fixed; change it if your cost
  structure differs.
- **DPO** divides by supplier bills raised, so it also works for service companies with no cost of sales.
- **DSCR** puts interest paid together with loan repayments when they are in the same bank entry.

## Formulas, targets and your own KPIs

**The formula language** (`engine/desk_engine/analysis/formula.py`) has:

- the arithmetic operators `+ − × ÷` and parentheses;
- measures, and the ids of other KPIs (for example `dso + dio - dpo`);
- these functions:
  - `SUMN(x, n)` and `AVGN(x, n)`: sum or average over the last *n* months;
  - `PREV(x, n)`: *n* months earlier;
  - `AVGBAL(x)` = (x + PREV(x, 12)) ÷ 2;
  - `IFPOS(x)`: only when positive;
  - `ABS`, `MIN`, `MAX`.

It is parsed into a tree and evaluated by walking it, with no `eval`.

**When a KPI has no value.** A value that needs months before the ledger starts, or that would divide by zero, is
left empty with the reason, instead of being shown as a misleading number.

**Status.** An administrator (the finance lead) sets a **target** and a **warning level** for each KPI. The card then
shows:

- *on target* when the KPI meets the target;
- *watch* when it is between the target and the warning level;
- *act* beyond the warning level.

**Changing and adding KPIs.** An administrator can change a formula, go back to the standard one, switch a KPI off,
or add a KPI with its own formula, unit and direction. Changes are audited.

**Drill-down.** Opening a KPI shows:

- its last 24 months;
- the formula, and the standard one when it was changed;
- the value of each measure;
- the largest ledger accounts behind each measure.

## The assistant

The assistant has three read-only tools:

- `financial_kpis`: the scorecard, optionally one group;
- `kpi_detail`: one KPI, with its history and the accounts behind it;
- `forecast_report`: see [FORECASTING.md](FORECASTING.md).

It cannot change targets, formulas, roles or forecasts.

## Limits

- Inventory ratios need inventory in the ledger. A company that expenses its purchases has no DIO.
- History is what the ledger holds: 24 months from a connection, or the whole file for an import.
- Overdue shares need invoices and bills synced from a connection. They are a snapshot of the latest month, not a
  history.
- One currency: the ledger's base currency. There is no exchange-rate feed.
- Statement layouts (IFRS, US GAAP or local) are not produced. The measures are management figures from the ledger.
