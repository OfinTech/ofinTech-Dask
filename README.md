<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/brand/ofintech-wordmark-light.svg">
    <img alt="OFINTECH" src="docs/brand/ofintech-wordmark-dark.svg" height="44">
  </picture>
</p>

<h1 align="center">OfinTech Desk</h1>

<p align="center">
  An open-source AI workspace for finance and accounting teams, in which every figure is traced to its source.<br>
  Desktop app for each person · optional Team Server for collaboration · English, العربية, Español, Français
</p>

<p align="center">
  <a href="LICENSE">Apache-2.0</a> ·
  <a href="CHANGELOG.md">Changelog</a> ·
  <a href="CONTRIBUTING.md">Contributing</a> ·
  <a href="SECURITY.md">Security policy</a> ·
  <a href="https://discord.gg/hbzN6udBM">Discord</a>
</p>

---

OfinTech Desk reads your ledger, spreadsheets, documents and inbox, does the arithmetic in code, fills in your own
Excel and Word files with sources for every figure, and asks a person before it sends or changes anything. Bring any
model: a cloud provider with your own key, or a local model.

*Built for finance teams in any country: it works with any ledger through open connectors, any AI model you choose,
and your company's own chart of accounts, currency and fiscal year.*

## What it does

| Area | What you get |
|---|---|
| **Assistant** | Chat about your numbers. Answers stream, use tools, and show tables and KPI tiles drawn by the app, never HTML from the model. A **figure check** lists any number in an answer that does not appear in a tool result, a message or saved memory. It answers in the language you write in. |
| **Any model** | Bring your own key: OpenAI, Anthropic, Google Gemini, DeepSeek, Mistral, Groq, OpenRouter, xAI, Together, Azure OpenAI, or any OpenAI-compatible server. Local models via Ollama or LM Studio. Choose per workspace, conversation and workflow step, plus a cheaper **model for short jobs**. |
| **Lean on tokens** | Each request carries only the tool groups it needs (about half the size of sending every tool), older tool results are shortened, and the prompt is ordered so providers can cache it: Anthropic prompt caching is switched on, and OpenAI-style automatic caching benefits from the stable prefix. Usage shows how much came from cache. See [docs/COSTS.md](docs/COSTS.md). |
| **Your own files** | Attach or upload an Excel or Word file (a company template, last month's pack, an engagement letter). The assistant reads it, fills it in or updates it, and saves a **new version** that keeps your layout, styles and formulas. Each new figure is matched to its source; figures without one are highlighted. The original stays as version 1. |
| **Evidence-linked files** | Workbooks and reports the assistant builds: inputs from dataset queries drill down to the underlying rows, computed columns and totals are **live Excel formulas**, and an **independent reviewer** (a separate model call that never sees the conversation) annotates each file. See [docs/FILES.md](docs/FILES.md). |
| **Memory, with transparency** | The assistant *proposes* lasting facts (a materiality threshold, an account mapping, a close deadline); nothing is saved until a person approves it. Every entry shows where it came from, who saved it and how often it was used, and answers show which memories they consulted. Secrets are refused. See [docs/MEMORY.md](docs/MEMORY.md). |
| **Search** | One search across documents (scans read with built-in OCR), past conversations, memory, files and data, with an optional short answer that cites what it used. Collections can follow a **Google Drive folder**. **PipesHub** can be connected for knowledge-graph retrieval and its connectors. See [docs/KNOWLEDGE.md](docs/KNOWLEDGE.md). |
| **Approvals** | Anything that leaves the conversation waits for a person with the editor role or higher: sending e-mail or chat messages, running workflows, MCP tools. |
| **Finance tools** | NPV/IRR, XNPV/XIRR (checked against Excel's documented examples), loan schedules, depreciation, ratios, variance analysis, VAT/GST/sales-tax splits, break-even, growth/CAGR, currency conversion at a stated rate. |
| **Skills** | 15 built-in finance skills (month-end close, bank reconciliation, variance analysis, 13-week cash forecast, AR collections, IFRS 15/ASC 606, …). Write or import your own (`SKILL.md`). Skills are instructions only; nothing in them is executed. |
| **Financial analysis** | 35 standard KPIs from the general ledger: growth and profitability, liquidity, working capital, leverage, cash flow, and cost and control. They work on any chart of accounts, because each account gets a role (suggested in four languages, confirmed by people). The finance lead sets targets and warning levels, edits formulas or adds KPIs, and can trace every value to the accounts behind it. See [docs/FINANCIAL_ANALYSIS.md](docs/FINANCIAL_ANALYSIS.md). |
| **Forecasting** | A method and an assumption for every P&L line, plus DSO, DIO and DPO, capital spending and debt repayment. The forecast projects profit, working capital and cash month by month, and flags the month cash runs out. Scenarios and versions; a draft → submitted → approved workflow, where the finance lead approves and picks the plan the KPIs compare against. A statistical reference with its measured error is shown next to it, and accuracy is tracked against actuals. See [docs/FORECASTING.md](docs/FORECASTING.md). |
| **Cost centres** | Follow a project or department through the general ledger, the journal and the bank. Cost centres are defined by rules (tags, accounts, suppliers, words), so they work on ledgers that were never tagged; the page shows the untagged share. It shows the cost against the cash that left the bank (paid directly, supplier bills paid or unpaid) and the date the budget runs out at the current pace. Budgets are read from your own Excel or Word file, and each line keeps its cell. A **general ledger template** and a **budget template** download from the page, and a filled-in ledger is uploaded there (new or replacing last month's), with unbalanced entries named. See [docs/COST_CENTRES.md](docs/COST_CENTRES.md). |
| **Data and dashboards** | Excel/CSV import that understands number and date formats from different countries; dashboards with KPI, bar, line, area and table widgets, grouped by month, quarter, year or fiscal year. |
| **Accounting systems** | QuickBooks Online, Zoho Books, Xero and **Odoo** 17+. Any other ledger or ERP links through the **Open Accounting Connector** (OpenAPI 3.1, a reference adapter and a push API): [docs/ACCOUNTING_API.md](docs/ACCOUNTING_API.md). Read-only. |
| **Workflows** | Triggers: by hand, schedule (with a preview of the next runs), incoming e-mail, chat command or a **webhook** from another system. Steps: assistant, tool, **file** (refresh a workbook or fill your own file), approval, condition, notify; retries per step; a run timeline with outputs and durations; a gallery of finance templates. See [docs/WORKFLOWS.md](docs/WORKFLOWS.md). |
| **Accounts and team** (Team Server) | Sign in with e-mail and password or **Google**, also from your public website's own forms (`DESK_SITE_URL`), password reset by e-mail, invitations, roles (viewer, editor, admin, owner), shared or private conversations, comments with @mentions, an approvals inbox, an audit log and token usage per model. |
| **Webhooks** | Under Connections: **incoming** endpoints other systems call with a secret (bearer or HMAC-SHA256), logged and able to start a workflow; **outgoing** subscriptions that post signed JSON for the event groups you choose (approvals, forecasts, files, cost centres, workflow runs…) with retries, a delivery log and re-sends. See [docs/WEBHOOKS.md](docs/WEBHOOKS.md). |
| **MCP, Telegram, WhatsApp** | Connect MCP servers (with an ask/auto/off policy per tool) and expose read-only finance tools at `/mcp`. Members can ask the assistant from Telegram or WhatsApp after linking their own chat. |
| **Languages** | English, Arabic (right to left), Spanish and French, chosen per person. See [docs/LANGUAGES.md](docs/LANGUAGES.md) to add one. |

## Run it

### Desktop app, from source

Requirements: Python 3.11+ and Node.js 22+. Optional: `tesseract` with the `eng ara spa fra` language data for OCR of
scans and images.

```bash
pip install -r engine/requirements.txt
(cd ui && npm install && node build.mjs)
(cd desktop && npm install && npm start)
```

The desktop app starts the engine on `127.0.0.1`. The engine creates a master key and keeps it encrypted with the
operating system's secure storage (macOS Keychain, Windows DPAPI, libsecret/KWallet), and generates a fresh access
token on every launch. Data lives in the app's user-data folder (*File → Open data folder*).

Installers (.dmg, .exe, .AppImage, .deb) are built by `.github/workflows/build.yml`. A lighter desktop client for
macOS and Windows that opens a Team Server such as `https://desk.ofintech.ai` is built by
`.github/workflows/desktop-cloud.yml`; see [docs/DESKTOP.md](docs/DESKTOP.md). Signing and notarisation are
covered in [docs/RELEASING.md](docs/RELEASING.md).

### Team Server (production)

```bash
cd server
cp .env.example .env        # DESK_DOMAIN, ACME_EMAIL, DESK_MASTER_KEY; optional SMTP and Google sign-in
docker compose up -d --build
```

Open `https://<DESK_DOMAIN>`. The first account becomes the administrator; after that, people join by invitation.
To keep files in MinIO (or any S3-compatible store) add `-f docker-compose.minio.yml`; files are encrypted by the
engine before they are uploaded. Backups: `server/backup.sh`. The production checklist (TLS, secrets, SMTP, Google
sign-in, backups, monitoring, upgrades) is in [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md).

### Engine and UI in a browser (development)

```bash
cd ui && node build.mjs && cd ..
cd engine
DESK_DEV=1 DESK_UI_DIR=../ui/dist DESK_LOCAL_TOKEN=$(python -c "print('x'*40)") python -m desk_engine
# open http://localhost:47821/#token=xxxxxxxx… (the same token)
```

## Setting up connections

Each provider needs an OAuth app (client ID and secret) registered by whoever runs the installation. The redirect URI
is `http://localhost:47821/oauth/callback` (desktop) or `https://<domain>/oauth/callback` (Team Server); Google
sign-in reuses the Google app and the same address. Step-by-step instructions, scopes and each provider's review
requirements are in [docs/CONNECTORS.md](docs/CONNECTORS.md).

## Security in brief

- Credentials (API keys, OAuth tokens, bot tokens, webhook secrets) are encrypted with AES-256-GCM or stored only as
  hashes. Stored files can be encrypted per object before they reach S3.
- The desktop engine listens on loopback only, requires the per-launch token and rejects other Host headers.
- The Team Server uses scrypt password hashing, HttpOnly SameSite=Strict session cookies with an origin check, rate
  limits on sign-in and password reset, single-use reset links, and invitation-only sign-up. Google sign-in requires a
  verified e-mail and can be limited to your domains.
- Strict CSP; model output is rendered from Markdown into React elements, never as HTML. The model never writes SQL,
  never runs code and has no shell; formulas it writes into your workbooks are checked and may not reach outside the
  workbook.
- Memory refuses secrets (passwords, keys, IBANs, card and account numbers) and nothing is remembered without a
  person's approval.

Report vulnerabilities as described in [SECURITY.md](SECURITY.md). Design details: [docs/SECURITY.md](docs/SECURITY.md).

## What is not claimed

- Tax, accounting and regulatory output is general guidance. No certification or compliance is claimed for
  e-invoicing (including ZATCA), data protection laws (including PDPL), SOC 2, SAMA or any other regulator.
- Accounting connectors read data; they do not post entries. There is no live bank connection.
- The independent reviewer is a second model check, not an audit.
- There is no live foreign-exchange feed: conversions use rates the user or a dataset supplies.
- Connectors were tested against mocked API responses that follow each provider's documented format. Test against a
  sandbox company before production use.
- Translations of the interface were written for this release and should be reviewed by native speakers in your
  team; messages from the server (errors) are in English.

## Repository

```
engine/      Python engine: API, agent, model providers, tools, memory, search, files, data, connectors,
             workflows, MCP, channels; tests (unittest)
ui/          React + TypeScript UI (esbuild); translations in ui/src/i18n
desktop/     Electron shell: engine sidecar, secure storage, Team Server mode, electron-builder config
server/      Team Server: Dockerfile, docker-compose with Caddy (HTTPS), MinIO overlay, backup script
e2e/         Browser smoke test with a fake model
landing/     The public website (static, nginx in Docker)
examples/    Reference adapter for the Open Accounting Connector; sample output files; a demo company with
             Zoho Books import files and a matching general ledger
docs/        Architecture, security, deployment, connectors, accounting API, files, knowledge, memory,
             workflows, cost centres, financial analysis, forecasting, languages, costs, skills, MCP, testing, releasing; brand assets
```

Tests: `cd engine && python -m unittest discover -s tests -t .`; UI: `cd ui && npx tsc --noEmit && npm run
i18n:check`; browser: `python e2e/ui_smoke.py`. What was run for this release is in [docs/TESTING.md](docs/TESTING.md).

## Licence

Apache License 2.0; see [LICENSE](LICENSE) and [NOTICE](NOTICE). Third-party components and their licences are
listed in [LICENSES.md](LICENSES.md). "OfinTech" and the OFINTECH logo are not covered by the licence; see NOTICE.
