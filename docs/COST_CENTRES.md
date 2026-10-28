# Cost centres

A cost centre follows a project or a department through the general ledger, the journal and the bank. For each
one, OfinTech Desk shows:

- what it cost;
- how much of that has actually been paid out of the bank;
- what is still owed to suppliers;
- when the budget runs out at the current pace.

The figures are worked out in code from journal lines, never by the model. Every figure states its method, and you
can open the ledger lines behind it. Nothing is written to the accounting system.

## Where the journal lines come from

Cost centres read a **general-ledger dataset**. A connection creates it as `<connection> · General ledger`:

| Source | What is read | Tags (dimensions) | Supplier on each line |
|---|---|---|---|
| QuickBooks Online | the GeneralLedger report, a quarter at a time | Class, Location | yes |
| Xero | `GET /Journals` (scope `accounting.journals.read`) | tracking categories | **no**: Xero journals carry no contact (see below) |
| Odoo 17+ | posted `account.move.line` | analytic distribution, with percentages | yes |
| Open Accounting Connector | `GET /v1/journal-lines` (pull) or `POST …/journal-lines` (push); see [ACCOUNTING_API.md](ACCOUNTING_API.md) | as sent | as sent |
| Excel or CSV under **Data** | a sheet whose first columns are the general-ledger columns below | as typed | as typed |

Zoho Books is not covered yet. Use the Open Accounting Connector or an Excel export.

General-ledger columns, in this order: `Date`, `Entry`, `Type`, `Account code`, `Account`, `Account class`,
`Debit`, `Credit`, `Description`, `Party`, `Cost centre tags`.

- `Account class` is one of `Bank`, `Receivable`, `Payable`, `Asset`, `Liability`, `Equity`, `Income`,
  `Other income`, `Cost of sales`, `Expense`, `Other expense`.
- Tags are written as `Project: Riyadh tower | Department: Site (60%)`.

## Rules, not a ledger field

A cost centre is a list of conditions. A ledger line belongs to it when **any** *include* condition matches and no
*except* condition matches. Within one condition, every filled-in part must match:

- **Tag**: `Project: Riyadh tower`, or just `Riyadh tower` for any dimension. When a line is split (Odoo analytic
  distribution, or `(60%)` in the tag), the cost centre takes only that share.
- **Accounts**: a code also takes its sub-accounts (`51` takes 5100 and 5150), or an account's full name.
- **Suppliers or customers**: the party on the line.
- **Words in the description**.
- **From / to dates**.

Matching ignores case, spacing, Arabic diacritics and the different forms of alef, teh marbuta and yeh.

Because the rules do not depend on a cost-centre field, they also work on a ledger that was never tagged. The page
shows the **untagged share**: how much of the cost was picked up by an account, supplier or keyword rule rather than
by a tag in the ledger. A high share means the ledger itself should be tagged better.

Two checks run on every report:

- **Possibly missing**: cost lines that no cost centre took, from this cost centre's suppliers or naming it (its
  name, code or keywords) in the description.
- **Counted twice**: lines that this cost centre and another one both take in full. A line split 60/40 between two
  projects is not counted twice.

Every change to the rules is kept as a new version, with who made it and an optional note. The report states the
version it used.

Assets are not cost by default. Tick *Count capitalised cost* for projects that are capitalised (work in progress).

## Cost against cash

The ledger records cost when it is incurred, but the bank shows when it is paid. Each journal entry that charges the
cost centre is put into one of three groups:

| Entry | Counts as |
|---|---|
| Charges the cost centre and pays from a **Bank** account (no payables line) | paid directly |
| Charges the cost centre and raises a **Payable** (a supplier bill) | through supplier bills |
| Neither: accruals, depreciation, reclassifications | not cash |

Each supplier's later debits to payables settle that supplier's bills, **oldest first**. A settlement that has a bank
line in the same entry counts as paid. Any other settlement (a credit note or an offset) counts as *settled without
cash*. The cost centre's part of a bill is paid in the same proportion as the bill.

So: cost to date = paid directly + supplier bills (paid + settled without cash + still unpaid) + not cash.

When payables lines carry no supplier (Xero journals), bills are settled oldest first across all suppliers together.
The page says so when that happens.

**Bank payments that may belong here**: money out of the bank whose entry pays no supplier bill and charges no cost
centre (for example, posted to a suspense account), from this cost centre's suppliers or naming it.

## When the budget runs out

- **Budget**: the sum of the budget lines (below), or the amount typed on the cost centre.
- **Pace**: the average daily cost over the last *n* months up to the report date (*n* = 1, 2, 3, 6 or 12; default
  3). The window never starts before the cost centre's start date.
- **Run-out date**: report date + (remaining budget ÷ daily pace), rounded up to whole days.
- With a planned end date, the report also states:
  - the cost projected at the end (cost to date + pace × days left);
  - how far that is over or under the budget;
  - whether the budget runs out before or after the end.
- When the cost to date has passed the budget, the report states the day it did.
- With fewer than 14 days of history, or no cost in the window, there is no run-out date.

The report date defaults to the latest date in the ledger, or today if that is earlier. *As of* picks another date.
Each budget line has its own run-out date, measured on the accounts matched to it.

## Budgets from your own file

*Read budget from a file* takes the Excel or Word file the team already uses.

1. **Find the table.** It looks for a header row with an amount column, in the first 40 rows of each sheet. Headers
   such as *Budget*, *Approved budget*, *Amount*, *الموازنة*, *المبلغ*, *Presupuesto*, *Importe* and *Montant* are
   recognised, with or without a unit such as *Budget (SAR)*. In Word files it looks at tables.
2. **Read the labels and codes.** The labels come from the label column, and an account code is read when there is a
   *Code* or *رمز* column.
3. **Skip totals.** Total and subtotal rows (*Total*, *الإجمالي*, *المجموع*, *Sous-total*…) are not read as budget
   lines. The last one is used to check the sum, and the page warns when they differ.
4. **Keep the source.** Every budget line keeps its cell, for example `Budget!C6`, with the file and version. When
   someone changes an amount by hand, the line keeps the cell it first came from.
5. **Match to accounts.** A line is matched by its code, or else by the ledger account whose name shares at least
   half its words. *Match accounts* corrects the match.
6. **Watch for formulas without results.** A formula cell is read by its last calculated result. A workbook that was
   never opened in Excel has no result, and the page says which cells are affected.

## The assistant

The assistant reads the same reports with three read-only tools:

- `list_cost_centres`;
- `cost_centre_report`: totals, cash, run-out, budget lines and methods;
- `cost_centre_lines`: the ledger lines behind a figure, the possibly missing lines and the unassigned bank payments.

People create and change cost centres on the page. A scheduled workflow can call `cost_centre_report` every week and
notify the team.

## Limits

- Cash is inferred from the ledger's bank lines. There is no live bank feed, and bank lines are only as complete as
  the ledger's bank reconciliation.
- Payments are matched to bills by supplier and date, not by document links. Partial payments, prepayments and
  payments covering several bills are therefore allocated oldest first, which can differ from how the accounting
  system allocated them.
- Open purchase orders (commitments) are not read.
- Amounts are in the ledger's base currency.
- The parsers for QuickBooks, Xero and Odoo were tested against documented sample responses, not live companies.
  Compare the first synced month with the accounting system.
