# Wanted

Named gaps, each with the folder it goes in. Nothing here needs a design discussion first: if you can fill one of
these rows, open a pull request.

Two rules apply to everything on this page, and a contribution that breaks either is refused however good it is:

- **Invented data only.** Never a real statement, a real customer, a real account number or a real name — not even
  with the digits changed. `fixtures/validate.py` and `fixtures/charts-of-accounts/validate.py` check a fixture
  before you send it.
- **An expectation is what the file says, not what the code does.** If the product reads your fixture wrongly,
  record that as a gap with a reason. A fixture bent to match today's behaviour protects nothing.

## 1. Bank statement shapes — no code needed

`fixtures/statements/<country>/<bank-or-shape>/` with `statement.csv` (or `.xlsx`, `.xlsm`, `.tsv` — what the
reader accepts) and `expected.json`.
Full instructions, and two worked examples, in [`../fixtures/README.md`](../fixtures/README.md). If you would
rather not open a pull request, the issue form
[*A bank statement is not read correctly*](https://github.com/OfinTech/ofinTech-Dask/issues/new?template=statement-not-read.yml)
asks for the top of the file with invented figures and what the file says.

Three shapes are in the corpus, from one country (`sa`) and two examples. Everything below has never been read:

| Wanted | Why it is awkward |
|---|---|
| **`us`** — checking and business accounts | one signed column, or deposits/withdrawals split; `MM/DD/YYYY` |
| **`gb`** — current and business accounts | `DD/MM/YYYY`, which is the same ten characters meaning another day |
| **`ae`**, **`kw`**, **`qa`**, **`bh`**, **`om`** | Arabic and English headers in one file, `CR`/`DR` in a column of its own |
| **`eg`**, **`jo`**, **`lb`** | Arabic headers, Arabic-Indic digits, three decimal places in some currencies |
| **`in`**, **`pk`** | lakh/crore grouping (`1,23,456.00`) |
| **`de`**, **`fr`**, **`es`**, **`it`** | comma as the decimal separator, `.` as the thousands separator, SEPA reference lines |
| **`tr`**, **`id`**, **`br`** | the same reversed separators, with the year first in some exports |
| Any country — **a workbook of several sheets** | the statement on one tab, a summary or a pivot on the others |
| Any country — **a transaction over two lines** | the description wrapping onto a second row with no date and no figure |
| Any country — **a row with a balance and no readable amount** | the amount column empty or unparseable while the running balance moves |
| Any country — **a Hijri or dual-calendar date column** | `1447/05/03`, or both calendars in adjacent columns. Not read today: a fixture is how it starts being read |

## 2. Charts of accounts — no code needed

`fixtures/charts-of-accounts/<country>/<slug>/` with `accounts.csv` and `expected.json`;
[`../fixtures/charts-of-accounts/README.md`](../fixtures/charts-of-accounts/README.md) says what each column is.
What matters is the **class word your country's systems actually write** and the role an accountant would give the
account — and a **gap** recorded where the two disagree with what the product suggests.

Two charts are in the corpus, `gb` and `sa`, each carrying one gap found while writing it. Wanted: `us`, `ae`,
`eg`, `fr`, `de`, `in`, `pk`, `tr`, `es`, `br` — and a second chart for `gb` or `sa` from a different kind of
business, because a trading company's chart is not a contractor's or a clinic's.

## 3. Finance terms in your language

The interface is English, Arabic, Spanish and French, and the translations have not been read by a finance
professional in every one of them. *Accrual*, *suspense*, *unallocated earnings*, *cost centre*, *ageing*,
*accrued income* and *retained earnings* are the ones to look at first: a dictionary translation of any of them
reads wrong to an accountant.

Open an issue with the term, the language, what it says now and what it should say. One term is a useful issue; a
whole file reviewed is a useful pull request.

## 4. A ledger that is not connected yet

Built: QuickBooks Online, Zoho Books, Xero, Odoo, Google (Gmail, Drive, Sheets), Telegram, WhatsApp Business.

Everything else goes through the adapter in [`../examples/accounting-adapter/`](../examples/accounting-adapter/) —
a small service that answers the accounting API in [`ACCOUNTING_API.md`](ACCOUNTING_API.md), so a new ledger needs
no change to the engine. Sage, Wave, FreshBooks, Dynamics 365 Business Central, NetSuite, Tally, Moneybird, Fortnox
and the ledger used where you are: none of them has an adapter, and each is a self-contained piece of work that
can be tested against the demo company's books.

## 5. Where a figure is wrong

Not a gap to fill, but the most valuable report this project receives. The issue form
[*A figure is wrong*](https://github.com/OfinTech/ofinTech-Dask/issues/new?template=wrong-figure.yml) asks for the
account, the period, both figures and where yours comes from. Every figure the product shows is meant to be
traceable to a journal entry, so a wrong one is either a bug with an exact reproduction or a disagreement about an
accounting rule — and both are worth the issue.
