# Changelog

All notable changes are listed here. Versions follow [Semantic Versioning](https://semver.org/); database
migrations are applied automatically on start-up and are never rewritten once released.

## Unreleased

### Added

- **Website:**
  - a features page (`landing/site/features/`) for financial analysis, forecasting and cost centres, with screenshots
    of the demo company, linked from the home page;
  - a **GitHub deploy webhook** (`landing/deploy`) that checks GitHub's signature and rebuilds the website when a push
    to `main` changes `landing/`.

- **Financial analysis** (docs/FINANCIAL_ANALYSIS.md):
  - 35 standard KPIs from the general ledger in six groups, with a scorecard, sparklines and the change on the
    previous month;
  - targets and warning levels set by the finance lead, editable formulas in a small safe formula language, and KPIs
    of your own;
  - drill-down to the measures and ledger accounts behind each value;
  - account roles suggested in four languages and confirmed by people;
  - month-end balances with opening balances from the synced balance sheet, and cash flow split into operating,
    investing and financing.
- **Forecasting** (docs/FORECASTING.md):
  - seven methods per P&L line, plus working-capital days, capital spending and debt repayment, projecting profit,
    working capital and cash;
  - the month cash goes below zero is flagged;
  - scenarios and versions; draft → submitted → approved, with approval and the plan reserved for administrators;
  - approved results are frozen and their accuracy is tracked against actuals;
  - a statistical reference (Holt / Holt-Winters) with its measured error.
- Three read-only assistant tools (`financial_kpis`, `kpi_detail`, `forecast_report`) and migration 14.
- **IBM Plex Sans Arabic** for the Arabic interface, bundled with the app (works offline and under the strict content
  security policy; SIL Open Font License 1.1). Letter spacing is turned off in Arabic so letters stay joined.
- **Cost centres** (docs/COST_CENTRES.md): a project or department followed through the general ledger, the journal
  and the bank, with:
  - **rules instead of a ledger field**: tags, accounts, suppliers, words and dates, with exceptions and a version
    history;
  - the **untagged share**, plus checks for possibly missing lines and lines counted twice;
  - **cost against cash**: paid directly, supplier bills settled oldest first, still unpaid, not cash, and bank
    payments that may belong to the cost centre;
  - the date the **budget runs out** at the current pace, overall and per budget line, with the projected cost at
    the planned end;
  - **budgets read from the customer's own Excel or Word file**, each line keeping its cell and matched to ledger
    accounts.

  The page has three read-only assistant tools and is translated into Arabic, Spanish and French (migration 13).
- **General-ledger lines** from QuickBooks Online (GeneralLedger report), Xero (Journals; adds the
  `accounting.journals.read` scope), Odoo (journal items with analytic distribution) and the Open Accounting
  Connector (`/v1/journal-lines`, pull or push), synced as a `· General ledger` dataset. An Excel or CSV file with the
  same columns works too.

- **Desktop client for macOS and Windows** that opens the Team Server at `https://desk.ofintech.ai` (configurable at
  build time), without a local engine: `desktop/electron-builder.cloud.yml` and the `desktop-cloud` workflow, an
  offline page with *Try again*, and docs/DESKTOP.md.
- **Google sign-in from the desktop app** through the system browser, handed back to the app with a single-use,
  two-minute code bound to the app that started it (migration 12).

- **A demo company** in `examples/demo-company`: Harbor Line Logistics Ltd, 14 months of books in USD. It has Zoho
  Books import files (chart of accounts, contacts, items, invoices, bills, payments, manual journals), the same
  transactions as an OfinTech Desk general ledger, a project budget and a step-by-step guide.

### Fixed

- Reading a budget file no longer takes a title line that mentions "budget" for the table's header.
- A payment to a supplier is classed as investing in proportion to the equipment on that supplier's bills, instead of
  always as operating.
- Accounts such as *Warehouse Racking*, *Shelving*, *Fit-out* and *Trucks* are suggested as fixed assets.

### Changed

- The README describes the product for finance teams in any country.

## 1.0.0 — 2026-10-27

First open-source release, under the Apache License 2.0.

### Added

- **Your own Excel and Word files.** Attach or upload an `.xlsx`/`.docx`; the assistant inspects it and fills it in or
  updates it with `open_file_for_editing`, `inspect_file` and `edit_file`. Every edit is a new version that keeps the
  file's layout, styles and formulas; new figures are matched to sources or highlighted; model-written formulas may
  not reach outside the workbook.
- **Memory with transparency.** The assistant proposes; people approve. Workspace and personal memory, pinning,
  archive, use counts and origin of every entry, *Memory consulted* under answers, secret detection, audit trail.
- **Workspace search** across documents, conversations, memory, files and data, with a short cited answer.
- **OCR** of scanned PDFs and images with Tesseract (English, Arabic, Spanish, French).
- **Collections that follow a Google Drive folder**, checked hourly.
- **Accounts:** sign in with Google (OIDC with PKCE, verified e-mail, optional domain allow-list), password reset by
  e-mail, invitations by e-mail (existing accounts are added directly), SMTP settings.
- **Delete a conversation.**
- **Workflows:** webhook trigger with a per-workflow secret, file steps (refresh a workbook, edit your own file),
  retries with back-off and *continue on error*, a run timeline with durations and outputs, a schedule preview, and a
  gallery of six finance templates in four languages.
- **Languages:** Arabic (right to left), Spanish and French alongside English, chosen per person; the assistant
  answers in the language you write in. `npm run i18n:check` reports coverage.
- **Lower token use:** tool groups chosen per request with a `more_tools` fallback, a stable cacheable system prefix,
  Anthropic prompt caching, shortened older tool results, a *model for short jobs*, and cache figures under Usage.
- The OFINTECH logo in the app, the icons and the README.
- Open-source project files: LICENSE (Apache-2.0), NOTICE, CONTRIBUTING, SECURITY policy, code of conduct, issue
  and pull-request templates; CI runs OCR tests, the translation check and the browser smoke test.

### Changed

- PipesHub replaces Onyx as the optional external knowledge engine (migration 7 removes Onyx connections).
- Workflow drafts can be saved incomplete; they are checked when switched on or run.
- The system prompt is split into a stable part and a changing part; the date moved to the changing part.
- A Team Server refuses to start with `DESK_DEV` set or with an `http://` public address (`DESK_ALLOW_HTTP=true` for a
  private test install). `DESK_MASTER_KEY_FILE` can supply the master key as a Docker secret.
- Google sign-in honours `DESK_GOOGLE_ALLOWED_DOMAINS` for every sign-in, not only new accounts.

### Database migrations

- 8: Google sign-in, password resets, login states.
- 9: memories and their full-text index.
- 10: full-text index of conversations; Drive-folder source for collections.
- 11: workflow webhook secrets, the model for short jobs, each person's language, cache token counts.

## 0.1.0 — 2026-10

Internal preview: assistant with approvals and figure check, finance tools, skills, evidence-linked workbooks and
reports with an independent reviewer, documents (RAG), data and dashboards, QuickBooks, Zoho Books, Xero, Odoo and
the Open Accounting Connector, Google, workflows, MCP client and server, Telegram and WhatsApp, Team Server, S3 and
MinIO storage.
