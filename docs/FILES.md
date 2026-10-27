# Evidence-linked files

When asked for a workbook, a schedule, a pack or a report, the assistant produces an Excel workbook, and for a
report also a Word document. Every figure in these files can be traced. A reviewer who is not part of the
conversation then checks the file before anyone shares it.

## 1. Every figure has a source or a formula

| What the figure is | How it was produced | What you see |
|---|---|---|
| **Input from a dataset query** | The table's rows come from a query of an imported spreadsheet or of data synced from QuickBooks, Zoho Books, Xero, Odoo or another connected system. | Blue in Excel. Its note names the dataset, the system and the filters, for example *Account = Rent · Period = Mar 2026 · sum of Amount over 14 rows*. In the app you can drill down to those 14 rows. |
| **Input typed into a table** | The engine looks for the number in the conversation's tool results and the user's messages. It prefers a line that also mentions the row's label. | Blue, with the line it was found in, for example a row of an accounting report or a passage of a contract. |
| **Input with no source** | The number appears nowhere in the conversation. | Shaded yellow, with a note and an automatic *high* finding. It stays in the file, but nobody can mistake it for a checked figure. |
| **Formula** | Computed columns, totals and KPIs. | A live Excel formula (black), for example `=B5-C5` or `=IFERROR((B8-C8)/C8,"")`. Totals of ratio columns are ratios of the totals, not sums of ratios. |

Every row also has a **Ref** column. It links to the **Sources** sheet, which lists each source with its system,
the time it was retrieved, what was used, and a link that opens the source in OfinTech Desk. The desktop app
registers `ofintech-desk://` links for this; on a Team Server the links are the server's own addresses.

In **reports**, the narrative quotes figures from the tables through placeholders such as `{{pl.total.variance}}`
or `{{[pl:Revenue:actual] / [pl:total:actual] | percent}}`, so a sentence cannot drift from its table. Each
figure carries a marker:

- `[S2]` links to source S2.
- `[C1]` links to calculation C1, shown with its formula and the workbook cell it refers to.

Literal numbers in the text are traced the same way as typed inputs, and highlighted with a Word comment when they
have no source.

## 2. Live formulas, not pasted values

The assistant never writes Excel formulas itself. It writes short expressions over column names:

```
variance      actual - budget
variance %    (actual - budget) / budget          (format: percent)
with VAT      ROUND(amount * 1.15, 2)
KPI           ([pl:Rent:actual] + [pl:Travel:actual]) / pl.Revenue.actual
```

The engine parses each expression (there is no `eval`) and compiles it to an Excel formula with the right cell
references. It also calculates the value itself, using Excel's rules:

- an empty cell counts as zero;
- a division by zero shows blank;
- `ROUND` rounds half away from zero at 15 significant digits.

The app therefore shows the same numbers Excel will. The tests recalculate every generated workbook in LibreOffice
and compare each formula cell with the engine's value.

Available functions: `ABS`, `ROUND`, `MIN`, `MAX`, `SUM` and `AVERAGE`, with `+ - * /` and brackets.

You can refer to a cell as `[table:row:column]`, where the row is `total` or the label in the table's first
column. `table.row.column` also works when the row is a single word. Pivot tables (for example months as columns)
get a row-total column automatically.

## 3. An independent reviewer (maker and checker)

Each new file and each new version goes through two checks.

1. **Automatic checks**, run instantly:
   - figures without a source;
   - formulas left blank by a division by zero;
   - tables whose query returned no rows;
   - report figures without a source.
2. **The reviewer.** This is a separate model call. You can choose a different model under **Settings → Files and
   review**, which makes the check more independent.
   - **What it sees:** only the file (every cell with its address, value, formula and source), the list of sources,
     and read-only tools to re-query datasets, search documents or recalculate. It never sees the conversation that
     produced the file.
   - **What it looks for:** figures that disagree with their sources or with each other; arithmetic, sign, period,
     unit and currency errors; formulas that do not match their labels; statements the tables do not support; and
     tax or regulatory claims stated as definitive.
   - **How it reports:** it records each finding against a cell (`P&L!C7`), a paragraph (`P3`) or the whole file,
     with a severity of high, medium or low.

**Where findings appear:**

| Place | Form |
|---|---|
| Excel | A note on the cell and a row on the **Review** sheet |
| Word | A comment from *OfinTech Reviewer* on the paragraph |
| App | The finding is listed next to the cell or paragraph it concerns |

**Handling findings:**

- **Resolve or dismiss.** Editors can resolve or dismiss a finding, and the files are rewritten without it. Every
  decision is audited.
- **Revise.** The assistant can read the open findings (`get_file_review`) and save a corrected version.
- **Refresh.** *Refresh data* runs the queries again, saves a new version, and lists the figures that changed since
  the previous version.

The reviewer's findings help a person review the file. They are not an audit opinion and do not replace one.

## 4. Your own Excel and Word files

The assistant can also work **in a file you already have**: a company template, last month's management pack, a
reconciliation, an engagement letter. Your layout, styles, number formats and formulas are kept.

1. **Add the file.** Attach it in a conversation (paperclip) or use **Files → Add your Excel or Word file**. It is kept
   as version 1, untouched.
2. **The assistant reads it** with `inspect_file`: for a workbook, the non-empty cells of a sheet or range with
   formulas and their last saved values; for a document, numbered paragraphs (P0, P1, …), tables (T0 with rows R0,
   R1, …) and placeholders such as `{{client_name}}`, `[[fee]]` or `<<date>>`.
3. **It edits** with `edit_file`, and each call saves a new version:

| Workbook (.xlsx) | Document (.docx) |
|---|---|
| `set_cell` with a value or a formula | `fill` placeholders by name |
| `append_row` (at the end or below a row, copying the row's style) | `replace_text` |
| `add_sheet` with rows | `set_paragraph`, `insert_paragraph` (with a style such as *Heading 2*) |
| `rename_sheet` | `set_table_cell`, `add_table_row` |

**Every new figure is checked.** A number written into a cell or text is matched to the conversation's evidence (tool
results, documents, your messages, saved memory). A match is recorded in the version's change list and, in Excel, as
a cell note *Source: …*. A number without a source is shaded yellow (Excel) or highlighted (Word), listed as *without
a source*, and becomes a finding to resolve. Formulas are written as live formulas, after a check that they stay
inside the workbook.

The file page shows the contents, the changes of each version with the source of every new figure, the checks, and
all versions for download. **Ask the assistant** opens a conversation about the file.

Limits: `.xlsx` and `.docx` up to 25 MB. Macro-enabled (`.xlsm`), old (`.xls`, `.doc`) and password-protected files
are refused; save a copy as `.xlsx`/`.docx` first. Charts, images and pivot tables in an existing workbook are not
kept by the Excel library used here; the app warns before the first edit of such a file, and version 1 keeps them.

## In the app

The **Files** page lists every file with its verification count, for example *46 traced · 30 formulas · 2 without
source*, and its review status. Open a file to see each sheet with column letters and row numbers. Select any figure
to see its evidence:

- for an input: the source, the quoted line and the underlying dataset rows;
- for a formula: its readable and Excel forms;
- any review findings on it.

Files can be downloaded as Excel or Word for any version and can be commented on. They are stored in the
workspace's file storage: the local folder, or MinIO / S3, encrypted.

## API

`POST /api/w/{ws}/files` takes the same JSON definition the assistant uses. Workflows and integrations can call it
to produce evidence-linked files without a conversation. Without a conversation there is nothing to trace typed
figures against, so prefer query tables there. The other endpoints:

- `GET /api/w/{ws}/files/{id}` returns the model, versions and findings.
- `GET …/cell?a=Sheet!C7` returns the evidence of one figure.
- `GET …/download?format=xlsx|docx&version=n` downloads a file.
- `POST …/refresh` and `POST …/review` refresh the data and start a review.
- `PATCH …/findings/{id}` resolves, dismisses or reopens a finding.
- `POST /api/w/{ws}/files/upload` (multipart `file`) keeps your own `.xlsx`/`.docx`; `GET …/inspect?sheet=&range=`
  lists its contents; `POST …/edit` with `{"edits": [...], "note": "…"}` applies the same edits as the assistant (used
  by workflow file steps).
