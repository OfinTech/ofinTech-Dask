# Changelog

All notable changes are listed here. Versions follow [Semantic Versioning](https://semver.org/); database
migrations are applied automatically on start-up and are never rewritten once released.

## Unreleased

### Security (enterprise audit, docs/audit/)

- The Zoho sign-in callback accepts only Zoho's own accounts servers and API hosts, matched exactly.
- The CSRF guard compares the whole request origin with the server's, never a prefix.
- Every CSV and workbook the engine writes keeps imported text as text (`= + - @`, tab and carriage-return
  prefixes), including the month-end close journal and pack.
- Request body limits hold whatever `Content-Length` says, on every public endpoint and the JSON API.
- An open event stream closes within one keep-alive when the session ends or the member is removed.
- Team Server: outbound requests are judged against the private network at every connection
  (`egress.PrivateNetworkGuard`); shared address space (100.64/10) is refused; the S3 endpoint and
  `DESK_EGRESS_ALLOW` hosts are exempt.
- **Behaviour change:** a stored file without the encryption header is refused unless
  `DESK_STORAGE_ALLOW_PLAINTEXT=true`; `python -m desk_engine storage-encrypt` rewrites such files once.
- Office files that would inflate beyond 400 MB or 200:1, images outside raster formats or above 60 MP, and OCR
  runs past 15 minutes are refused or cut short before a parser spends the memory; Caddy caps bodies at 260 MB.
- The bank-identifier masker no longer masks the engine's own ids (`doc_…`, `ds_…`).
- A custom skill written by an editor stays off until an admin turns it on, and cannot take a built-in's name.
- Tool arguments are validated against each tool's schema before a handler runs; every tool call has a time limit.
- `DESK_SMTP_SECURITY` accepts `starttls`, `ssl` or `none` only, and `none` cannot be combined with a login.
- Audit entries for MCP servers keep the URL without its query string; Telegram transport errors never carry the
  bot token; formula parsers refuse deep nesting and absurd `ROUND` digits cleanly.
- CI: read-only workflow token, a `security` job (gitleaks, ruff security rules, pip-audit, npm audit) and a Trivy
  scan of the Team Server image gate the image and the release; the engine container drops all capabilities.

## 1.1.0 — 2026-11-01

### Added

- **Data boundaries** (docs/DATA_BOUNDARIES.md, migration 18), enforced in code and tested by switching each one
  off:
  - **no payments:** tools that initiate, approve or change a payment, a transfer or a beneficiary are refused,
    built-in or MCP;
  - **sealed workspaces:** no e-mail, chat apps, Drive, MCP or outgoing webhooks, and only a model on the local
    network;
  - **sealed servers** (`DESK_SEALED=1`, `server/docker-compose.sealed.yml`): no connection outside the private
    network, and not even a DNS lookup;
  - **masking:** IBANs and card numbers are masked in every message on its way to a language model;
  - **two new assistant rules:** never touch payments, and treat text in data as data, not instructions.
- **Month-end close** (docs/CLOSE.md, migration 16): a month prepared from the general ledger with a readiness
  score and checklist; regular costs missing this month (accruals) and depreciation not charged proposed as entries
  with their evidence; the bank statement (Excel or CSV) matched to the ledger by amount and date and reconciled to
  the closing balance, with bank charges and interest proposed and unknown items to review; variances above
  materiality to explain; regular customers not invoiced; decisions kept across refreshes and items resolved when the
  ledger catches up; the approved journal (general ledger template columns) and a close pack workbook with evidence
  links; administrators close and reopen the month. The *Close while you sleep* workflow template, the assistant's
  `month_end_close` and `prepare_month_end_close` tools, the `close` webhook event group, and an October bank
  statement in the demo company.
- **Webhooks under Connections** (docs/WEBHOOKS.md): **incoming** endpoints other systems call with a secret
  (bearer token or HMAC-SHA256 signature), logged and able to start a workflow with the delivery as its trigger;
  **outgoing** subscriptions that post signed JSON (`X-OfinTech-Signature-256`) for chosen event groups, with
  retries after 1, 10 and 60 minutes, a delivery log, re-sends and test events; secrets shown once and stored
  encrypted; the assistant's read-only `webhook_events` tool; migration 15.
- **Cost centres: templates and a ledger upload.** A general ledger template and a budget template (Excel, with
  guide sheets and drop-down lists) download from the Cost centres page; *Import a general ledger file* uploads a
  filled-in ledger as a new ledger or replaces an existing one after a month-end re-export, reporting the lines,
  entries and dates read and naming entries whose debits and credits differ.
- **Sign in and create an account from the website** (`DESK_SITE_URL`): the Team Server accepts plain HTML forms
  from the website's origin (`/auth/form/sign-in`, `/auth/form/sign-up`, `/auth/form/google`), sets the session
  cookie and opens the app, or sends the person back with a short error code; the same rate limits, password policy
  and invitation rules as in the app.
- **Website** (`landing/`): one page at `ofintech.ai` with sections for CFOs, accountants and executive managers,
  all features (financial analysis, forecasting, cost centres with the templates, connections and webhooks, the
  workspace), the latest changes, the demo company, the **sign-in and create-account forms** (no JavaScript; errors
  shown with CSS), the install guide and connecting a ledger. The former `/features/` page redirects to
  `/#features`, and the GitHub deploy webhook was removed: deploys are a `git pull` on the server.

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

- **Guidance for local models, from CFAI** (Chief Financial AI, OfinTech Desk's model benchmark: open models and
  Claude compared on 376 tasks):
  - the assistant is told not to try storing passwords, PINs, card numbers or IBANs in memory, and to say where
    credentials belong instead (the engine already refused them; open models still tried);
  - a question about one KPI goes to `kpi_detail`, the scorecard to `financial_kpis`; `kpi_detail` lists the KPI
    ids and now also finds a KPI by `gross margin`, `Gross-Margin` or `gross profit margin`;
  - suggested account roles: accumulated depreciation and goodwill written with the Arabic article (مجمع إهلاك،
    الشهرة), the Spanish *descubierto* (overdraft) and social insurance (GOSI, التأمينات الاجتماعية) now get
    the right role. All 120 accounts of the CFAI benchmark's chart in four languages are suggested correctly.

- **Memory** found saved memories only when the question used the same word forms: Arabic words with another
  article or ending (المستحقات / المستحقة), plurals, and short follow-ups were missed. Retrieval now normalises and
  stems words in four languages, weighs rare words, and reads the latest three messages. The Memory page search finds
  words while you type, and waits until you pause instead of searching on every key. Categories and usage counts
  are translated. Up to six pinned memories no longer crowd out the relevant ones. The assistant can now correct an
  outdated memory: saving the correction archives the old one (migration 17).
- **Dashboards:** *Refresh* recalculated the same data with no sign that anything happened. It now syncs the
  accounting connections behind the dashboard (for editors), shows that it is working, says what was updated or
  what failed, and the dashboard shows how old its data is.
- **Connecting an accounting system or Google:**
  - On the Team Server, the sign-in now happens in the same tab and comes back to the page it was started from, with
    the result shown there.
  - In the desktop app, the browser page links back into the app's Connections page.
  - A refused or failed sign-in returns with the provider's reason instead of a dead-end page.

- Reading a budget file no longer takes a title line that mentions "budget" for the table's header.
- A payment to a supplier is classed as investing in proportion to the equipment on that supplier's bills, instead of
  always as operating.
- Accounts such as *Warehouse Racking*, *Shelving*, *Fit-out* and *Trucks* are suggested as fixed assets.
- A custom KPI can no longer take the id of a ledger measure (such as `revenue`), which would have shadowed the
  measure in every formula.
- Workflow runs now audit `workflow.run.<status>` when they finish, so outgoing webhooks and the audit log see them.

### Changed

- **Sessions on the Team Server now follow activity.** Signing in once is enough for as long as somebody is
  working: a session renews itself and `DESK_SESSION_DAYS` no longer forces a new sign-in every 14 days (it is now
  an optional absolute cap, off by default). Instead the engine signs the person out after
  `DESK_SESSION_IDLE_MINUTES` (60) with nothing happening, deletes the session, and the page returns to the
  sign-in screen saying why — including when the engine, not the page, decided it. Only a request that changes
  something counts as activity, the page's heartbeat included; reading never does, so the pages that poll while a
  document is processed or a workflow run is open no longer hold a session open overnight with nobody there. Open
  tabs share one activity clock, so working in one of them keeps the rest signed in, and both knobs now refuse a
  negative or non-numeric value at start-up. The desktop app is unaffected where it runs its own engine on the
  computer, since that has no accounts and signs in automatically; against a Team Server it follows the same idle
  window as the browser.
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
