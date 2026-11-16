# Roadmap

**What this file is.** Where the work is, and where somebody else's half hour helps most. It carries no dates
and no promises: an item is here because the repository can show the state it is in — a file, a test, a line in
the changelog — and it leaves when that state changes.

The changelog says what has shipped. This says what has not.

## The part that cannot be invented: breadth

Four things in this repository are right for the files they have been shown and quietly wrong for the files they
have not. No amount of work by one person in one country fixes that, which is why these are first.

### Bank statement shapes

A reader that works for one country's banks breaks for another's: the header three rows down under a letterhead,
money in and money out as two columns or as one signed column, the minus written *after* the figure, `CR`/`DR` in
a column of its own, `03/04/2026` meaning two different days depending on where you are, Arabic headers, a totals
row in the middle.

What the corpus holds today — `fixtures/statements/`:

| Country | Shape |
|---|---|
| `example` | money-in / money-out columns under a letterhead |
| `example` | one signed amount column, minus after the figure, negatives in brackets |
| `sa` | one joined column, Arabic-Indic digits |

Three shapes, one country. `engine/tests/test_statement_corpus.py` reads every one on every build, so a shape
that is read today keeps being read while the reader is changed for somebody else's bank. **Contributing one needs
no code and no real statement** — [`fixtures/README.md`](fixtures/README.md), and
[`docs/WANTED.md`](docs/WANTED.md) names what is missing.

### Charts of accounts

The product reads a chart of accounts and judges what each account is. The words a country's systems write differ,
and so does what an accountant means by them. `fixtures/charts-of-accounts/` holds two charts, `gb` and `sa`, each
with one recorded **gap** — a place where the product is wrong and the fixture says so instead of bending the
expectation to match the code. Both gaps were found by writing the fixture, which is the argument for more of them.

### Languages

The interface is English, Arabic, Spanish and French. The translations exist; what they have not had is a finance
professional reading them in their own language and saying which term is wrong. *Accrual*, *suspense*,
*unallocated earnings* and *cost centre* are not dictionary words.

### Connectors

Built: QuickBooks Online, Zoho Books, Xero, Odoo, Google (Gmail, Drive, Sheets), Telegram, WhatsApp Business.
Anything else goes through the adapter in `examples/accounting-adapter/` — a small service that answers the
accounting API in [`docs/ACCOUNTING_API.md`](docs/ACCOUNTING_API.md). An adapter for a ledger that is not on that
list is a self-contained piece of work.

## Decided and not built

Two, both of which the documentation already admits in its own words:

- **A scorecard that feeds the measured probability.** `engine/desk_engine/decide/` turns a list of (score,
  accepted or rejected) verdicts into a curve — the share of proposals scoring like this one that a person
  accepted — and refuses to answer under 30 verdicts or outside the range it has seen. There is no scorecard
  producing such scores and no record of verdicts, so no page shows a figure from it
  ([`docs/MEASURED_PROBABILITY.md`](docs/MEASURED_PROBABILITY.md)).
- **Documents linked to journal entries.** Supporting documents are searchable, and a document is not yet
  attached to the entry it supports ([`docs/OVERVIEW.md`](docs/OVERVIEW.md)).

## Not planned

- **Any claim of certification or regulatory compliance.** The product states what it does not claim, in the
  README, and that section is not a gap to be closed by a pull request.
- **Sending money, or moving it.** The tool registry refuses any tool that initiates, approves, schedules or
  changes a payment, a transfer, a beneficiary or bank details, and that refusal is tested rather than promised
  ([`docs/DATA_BOUNDARIES.md`](docs/DATA_BOUNDARIES.md)). A pull request that adds a payment tool will be closed.
- **Payment initiation through open banking (PIS).** Account information, yes, through a licensed provider; asking
  a bank to move money, no.

## Picking something up

1. [`docs/WANTED.md`](docs/WANTED.md) — the named gaps, with the folder each one goes in.
2. [`good first issue`](https://github.com/OfinTech/ofinTech-Dask/labels/good%20first%20issue) ·
   [`help wanted`](https://github.com/OfinTech/ofinTech-Dask/labels/help%20wanted)
3. [`CONTRIBUTING.md`](CONTRIBUTING.md) — how to run it, what a pull request is checked against, and that an
   issue or a pull request written in Arabic is as welcome as one in English.

If what you want to work on is not here, open an issue and say so before you write it. The answer may be that it
belongs in a different repository, and that is worth knowing before an evening is spent on it.
