# Demo company: Harbor Line Logistics Ltd

A fictional freight and warehousing company with **14 months of books**, from 1 September 2025 to 27 October 2026,
in **USD**, in English. Use it to try OfinTech Desk end to end:

- a Zoho Books connection;
- the assistant;
- cost centres;
- financial analysis with 35 KPIs;
- forecasting.

Every name, figure and e-mail address is invented; the e-mail addresses use the reserved `.example` domain.

| | |
|---|---|
| Revenue | 2,393,943.37 over 182 invoices to 8 customers (Meridian Retail Group ≈ 31%) |
| Suppliers | 98 bills from 9 suppliers |
| Payments | 164 customer payments and 91 supplier payments |
| Journals | 47 manual journals: opening balances, payroll, depreciation, loan instalments, a new van, an unallocated payment |
| Open at 27 Oct 2026 | receivables 315,462.59 (25 invoices); payables 133,662.22 (7 bills) |
| Overdue more than 30 days | 67,506.01 (see below) |
| Project | Eastport Warehouse Fit-out (EWF), budget 160,000, February–December 2026 |

## What is in the data on purpose

Each item below is something the product should find.

- **Seasonality and growth.** December peaks and January–February dip; volumes grow about 1.2% a month.
- **A slow payer.** *Kestrel Auto Parts* pays 40–70 days late and often pays half an invoice first.
- **A disputed invoice.** *Lumen Home Goods* invoice INV-1104 (April 2026) is never paid.
- **Customer concentration.** *Meridian Retail Group* is the largest customer, on 45-day terms.
- **The EWF project** is coded "EWF" in the bill descriptions. It includes:
  - one bill without the code (*Dock levellers installation*, 7,300) from the project's contractor;
  - a 4,800 payment to that contractor posted to **Suspense**.
- **The EWF budget runs out before the planned end.** At the current pace it runs out around 25 November 2026,
  against a planned end of 31 December. The project costs bought on credit are paid oldest first, and 42,500 is still
  unpaid.
- **Equipment.** A new van is paid straight from the bank in June 2026. Pallet racking is bought on supplier bills and
  paid later; the payment counts as investing cash flow, not operating.
- **A fleet loan** at 6.5%, with monthly instalments of 5,000 plus interest.

## Files

```
zoho-books/                      import into Zoho Books in this order
  01_chart_of_accounts.csv       Accountant → Chart of Accounts → Import
  02_customers.csv               Sales → Customers → Import
  03_vendors.csv                 Purchases → Vendors → Import
  04_items.csv                   Items → Import
  05_invoices.csv                Sales → Invoices → Import (one row per line; rows with the same number form one invoice)
  06_customer_payments.csv       Sales → Payments Received → Import
  07_bills.csv                   Purchases → Bills → Import
  08_vendor_payments.csv         Purchases → Payments Made → Import
  09_manual_journals.csv         Accountant → Manual Journals → Import Journals
ofintech-desk/
  HarborLine_General_Ledger.xlsx the same transactions as journal lines, for OfinTech Desk → Data → Import
  EWF_Project_Budget.xlsx        the project budget, for Cost centres → Read budget from a file
  HarborLine_Bank_Statement_2026-10.csv  the bank's October statement, for Month-end close
generate.py                      rebuilds everything (deterministic; needs openpyxl)
```

## 1. Prepare Zoho Books

1. **Create a separate organisation for the demo**, so it doesn't mix with real books (a trial or free plan is
   enough).
   - Base currency **USD**, fiscal year **January–December**.
   - Choose an edition where tax is optional, for example the global or US edition. The data has no tax lines. An
     edition that requires VAT or GST on every invoice will ask for a tax treatment; disable tax or pick "no tax".
2. **Create the bank account by hand**, because bank accounts can't be imported: Banking → Add Bank or Credit Card.
   Name it exactly **Operating Account** and give it account code 1000.
3. **The system accounts already exist:** *Accounts Receivable*, *Accounts Payable* and *Owner's Equity*.

## 2. Import into Zoho Books

Import the nine files in the numbered order. For each one:

- On **Map Fields**, set the date format to **yyyy-MM-dd** and the decimal format to **1234567.89**.
- The column names follow Zoho's sample files. Check that each column is matched and match any that isn't. Zoho
  doesn't publish the exact names, and they can differ slightly between editions.

Rules for specific files:

- **Invoices (05).** They must be **sent**, not drafts: drafts do not post to the ledger, and payments cannot be
  applied to them. If they import as drafts, select them all and use *Mark as Sent*.
- **Bills (07).** They must be **Open**, not Draft.
- **Payments (06, 08).** They link to their invoice or bill through the *Invoice Number* or *Bill Number* column.
  Import them after the invoices and bills.
- **Journals (09).**
  - Leave *Auto-generate Journal Numbers* **unticked** so the JV numbers are kept.
  - Keep *Status* = Published.
  - JV-OPEN (1 Sep 2025) holds the opening balances: bank, fleet, depreciation to date, the loan and owner's equity.
- **To check the import**, compare Zoho's reports with the summary table above:
  - Profit and Loss for 1 Sep 2025 – 27 Oct 2026;
  - Balance Sheet at 27 Oct 2026;
  - Receivables Summary.

## 3. Connect OfinTech Desk

1. **Connections → Zoho Books.** Use the data centre of the demo organisation, and choose that organisation. The
   first sync creates the profit and loss, balance sheet, invoices and bills datasets.
2. **Ask the assistant**, for example:
   - "Which customers are overdue and by how much?"
   - "What was gross margin in Q2 2026?"
   - "Draft a reminder for Kestrel Auto Parts."
3. **Import the ledger.** The Zoho Books connector does not read journal lines yet. For cost centres, the KPIs and
   forecasts, import **ofintech-desk/HarborLine_General_Ledger.xlsx** under **Cost centres → Import ledger** (or Data → Import Excel or CSV). It holds
   exactly the same transactions as the Zoho files.

## 4. Try every part

**Financial analysis → Account roles**
- 23 accounts with suggested roles. Check that *Warehouse Racking* is a fixed asset, then **Confirm all
  suggestions**.

**Financial analysis → KPIs** (approximate values in October 2026)

| KPI | Value |
|---|---|
| Revenue growth (year on year) | ≈ 9% |
| Gross margin | ≈ 57% |
| Net margin | ≈ 9% |
| DSO | ≈ 51 days |
| DPO | ≈ 34 days |
| Current ratio | ≈ 5.4 |
| Interest cover | ≈ 11.7× |
| DSCR | ≈ 2.4× |
| Cash runway | not shown: the company is not burning cash |
| Revenue from top 5 customers | ≈ 79% |

Then:
- Set a target on DSO (for example 45 days, warning 55) and see the card change status.
- Open DSO to see the receivables account behind it.

**Financial analysis → Forecasts**
1. Create *FY2027*; it starts from the company's own history.
2. Set revenue to grow 1% a month.
3. Submit it, approve it (administrator) and make it the plan.
4. Create a *Downside* scenario with DSO at 70 days and compare the cash.

**Cost centres**
1. Create *Eastport Warehouse Fit-out*:
   - rule: words in the description "EWF";
   - starts 1 Feb 2026, planned end 31 Dec 2026;
   - tick *Count capitalised cost* (the racking is a fixed asset).
2. Read the budget from **EWF_Project_Budget.xlsx**. Expected:
   - 3 lines that add up to 160,000;
   - *Contingency* not matched to an account.
3. Expected results:
   - **Cost to date:** 146,800.
   - **Possibly missing** (12,100 in total):
     - the dock levellers bill (7,300);
     - the 4,800 advance in Suspense, as an asset line from the same supplier.
   - **Bank payment that may belong here:** the 4,800 Suspense payment.
   - **Run-out date:** before the planned end.
4. Add a second rule for the supplier *Eastport Fitout Contractors* and watch the untagged share and the missing
   lines change.

**Month-end close** (October 2026)
1. Prepare **October 2026**. Expected:
   - depreciation of the vehicles (7,800) proposed: it was charged every month and is missing in October;
   - *Professional Fees* listed under *What moved*: 19,900 against a 6,700 average, mostly Eastport fit-out work;
   - *Fleet Maintenance* also listed.
2. Upload **ofintech-desk/HarborLine_Bank_Statement_2026-10.csv**. Expected:
   - 19 of 21 statement lines matched;
   - the 31,415.57 payment of 26 October not yet on the statement;
   - a 45.00 bank fee proposed as an entry;
   - a 3,150.00 transfer from Meridian Retail Group to review;
   - an unexplained difference of **0.00**.
3. Approve the depreciation, explain the variances, and download the journal and the close pack.

**Assistant**
- "Where is the October close?"
- "How is the Eastport project doing against its budget?"
- "Show our liquidity KPIs."
- "What does the FY2027 forecast say about cash?"

## Rebuild

```bash
python examples/demo-company/generate.py
```

The seed is fixed, so the files are the same every time. To change the company, edit the lists at the top:
customers, suppliers, items, seasonality, and the project bills.
