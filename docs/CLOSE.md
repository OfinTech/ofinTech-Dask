# Month-end close

**Month-end close** prepares a month from the general ledger and lists what is left before it can be closed:

- costs that come every month and are missing;
- the bank against its statement;
- what moved more than usual;
- regular customers who were not invoiced.

People decide each item. Approved entries go into a journal file that a person imports into the accounting system.
**Nothing is posted to the accounting system by OfinTech Desk.**

It reads the same general-ledger dataset as cost centres and financial analysis (see
[COST_CENTRES.md](COST_CENTRES.md) for the sources: a sync, the Open Accounting Connector, or the general ledger
template uploaded under Cost centres).

## Preparing a month

Open **Month-end close**, choose the ledger and the month, and press *Prepare the close*. The suggested month is the
ledger's last complete month. You can prepare the same month again at any time (*Refresh from the ledger*), for
example after a new sync. Decisions are kept across refreshes, because each item has a stable identity: the account,
the customer, or the statement line.

When a refresh finds that an item is no longer needed, it moves to *No longer needed*. For example, the depreciation
was posted in the accounting system and synced, or the customer was invoiced. Items are never deleted silently.

## The checks

| Check | How it is worked out |
|---|---|
| **Every entry balances** | Debits and credits of each entry dated in the month. |
| **Account roles confirmed** | Accounts used in the month whose role is only suggested (Financial analysis → Account roles). This is a warning; it does not block the close. |
| **Missing costs and bank items decided** | See *Proposed entries* below. |
| **Bank reconciled** | See *The bank statement* below. |
| **Large variances explained** | See *What moved* below. |
| **Regular customers invoiced** | A customer with revenue in each of the previous three months and none this month. |

**Readiness** is the share of checks that are done; a warning counts as half. It is a progress measure, not an
opinion on the accounts.

## Proposed entries

**Missing recurring costs.** A cost account qualifies when:

- it was charged in each of the previous three months;
- the largest of those three amounts is no more than 25% above the smallest;
- it has nothing in the month being closed.

The proposal is last month's amount, with the three months and their ledger lines as evidence:

- **Depreciation** (an account whose role is depreciation): credit the account last month's depreciation was
  credited to, or an *Accumulated depreciation* account.
- **Accruals** (rent, salaries, subscriptions, interest…): credit the ledger's accrued-expenses liability account,
  found by its name in English, Arabic, Spanish or French.

When no suitable account exists in the chart of accounts, the proposal names a generic one and warns you to enter the
account you use before importing.

**Bank items.** Lines that are on the statement but not in the ledger are handled by their description:

- bank charges and fees become an entry: debit bank charges, credit the bank;
- interest received becomes an entry: debit the bank, credit interest income;
- anything else is listed under *To review*, because a person has to find what it is.

**Deciding an entry:**

- **Approve** puts it in the journal.
- **Reject** needs a reason, which is kept in the close pack.
- Before approving, the amount and the accounts can be changed; the change is kept across refreshes.
- **Undo** returns an entry to *proposed*.

## The bank statement

Upload the month's statement as Excel or CSV. The reader looks for a header row with:

- a date column;
- either an amount column (money in positive) or money-in and money-out columns;
- optionally, a description and a balance.

Headings in English, Arabic, Spanish and French are recognised.

**Matching.** Each statement line in the month is matched to a ledger line on the bank account with the same amount
(to the cent) dated within five days, nearest first. With several bank accounts, choose which one the statement
belongs to; the default is the account with the most lines in the month.

**The reconciliation:**

```
balance per statement   + in the ledger, not yet on the statement   = adjusted bank balance
balance per ledger      + on the statement, not in the ledger       = adjusted ledger balance
unexplained difference  = adjusted bank balance − adjusted ledger balance
```

- The **closing balance** comes from the statement's balance column on the month's last date, or you type it in.
- The **ledger balance** is the sum of every ledger line on that bank account up to the month end. A ledger that
  starts mid-way without an opening-balance entry shows that missing opening balance as an unexplained difference.

## What moved

A P&L account is listed when this month differs from the average of the previous three months by at least:

- the **materiality amount**, by default 1% of average monthly revenue, at least 500; and
- **20%** of that average.

Revenue is shown as a positive amount. Each variance shows the month's largest entries on the account and needs a
short explanation before it counts as done. Accounts with a proposed missing-cost entry are not listed twice.

## Journal and close pack

**The journal** (`journal.xlsx` or `journal.csv`) has the general ledger template's columns. It holds one row per
line of every approved entry, numbered `CLOSE-<month>-001` onwards and dated at the month end (bank items keep the
statement date). Import it into the accounting system with that system's journal import.

**The close pack** (`pack.xlsx`) has these sheets:

- **Summary:** the readiness score and checklist;
- **Proposed entries:** with status and reasons;
- **Bank reconciliation:** with outstanding items;
- **Variances:** with explanations;
- **To review;**
- **Evidence:** every ledger line behind a proposal or variance. The other sheets link to it.

## Closing the month

An administrator can:

- **Close the month.** Nothing can be changed afterwards, and refreshes are refused.
- **Reopen** a closed month.
- **Delete** a close.

Editors prepare, upload statements, decide entries and explain variances.

Every step is audited: `close.prepare`, `close.statement`, `close.item`, `close.closed`, `close.reopened` and
`close.delete`. Outgoing webhooks can subscribe to the `close` event group.

## Close while you sleep

The workflow template **Close while you sleep** runs `prepare_month_end_close` at 2 a.m. on the 1st of each month,
then tells the finance team with a link to the page. It is saved as a draft. Before switching it on:

- change the schedule, for example to the 3rd, if your team posts during the first days;
- add a sync step if your ledger comes from a connection that is not synced automatically.

If a statement was added to the month before the run, the bank is matched too.

The assistant has two tools:

- `month_end_close` answers "where is the September close?";
- `prepare_month_end_close` prepares a month on request.

## Limits

- Matching is one statement line to one ledger line; a deposit that the ledger records as several receipts is shown
  on both sides as unmatched.
- Recurring costs are found by account. A cost posted to a different account this month shows as a missing cost and
  as a variance on the other account.
- The checks are arithmetic on the ledger. They do not replace a review of the accounts or an audit.
