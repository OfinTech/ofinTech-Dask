# Architecture

```
 Desktop app (Electron)                               Team Server (Docker)
 ┌───────────────────────────────┐                    ┌──────────────────────────────┐
 │ Renderer: React UI (sandboxed)│                    │ Caddy (HTTPS) ─► engine       │
 │   ▲ token via preload bridge  │   or, in team mode │   server mode, accounts,      │
 │ Main: starts engine, keeps    │ ─────────────────► │   invitations, webhooks       │
 │   master key in OS storage    │   loads server UI  │   (WhatsApp, Telegram)        │
 │ Engine (Python) on 127.0.0.1  │                    └──────────────────────────────┘
 └───────────────────────────────┘

 Engine (one code base, two modes)
   web.py        routing, auth (local token | sessions), workspace roles, JSON errors
   routes/auth   e-mail + password, Google sign-in (OIDC + PKCE), password reset; mail.py (SMTP)
   agent/        harness (tool loop, approvals, figure check), tools: finance, data, accounting,
                 Google, files, memory, skills, collaboration; context.py (tool groups per request,
                 shortened history); system prompt in a stable and a changing part
   memory.py     proposals, approval, secret detection, retrieval (pinned + FTS5), use counts
   llm/          OpenAI-compatible and Anthropic streaming clients (prompt caching); provider catalogue
   knowledge/    document RAG: extraction, OCR (Tesseract), chunking, FTS5 + embeddings, hybrid search,
                 chat attachments, Google Drive folders; search.py (workspace search
                 and cited answers)
   files/        evidence-linked workbooks and reports: spec → model (values, live formulas, sources) →
                 Excel / Word writers; automatic checks and the independent reviewer; versions;
                 editable.py (inspect and edit the user's own .xlsx/.docx, keeping their layout)
   storage.py    local folder or S3 (SigV4 client), AES-256-GCM per object
   skills/       SKILL.md loader, 15 built-in finance skills, import/export
   data/         Excel/CSV import, datasets (SQLite tables), query compiler, dashboards
   connectors/   OAuth 2.0 (+PKCE), QuickBooks, Zoho Books, Xero, Google; Odoo (API key); the Open Accounting
                 Connector (pull adapter / push ingest, OpenAPI); sync to datasets
   workflows/    definitions (pydantic), runner with retries and timings, cron, webhooks, templates
   mcp_client.py MCP servers (stdio / Streamable HTTP) as agent tools, per-tool policy
   mcp_server.py the workspace as an MCP server (read-only tools, access keys)
   channels/     Telegram and WhatsApp linking, routing to the agent, notifications
   db.py         SQLite (WAL), migrations; security.py AES-GCM, scrypt, URL policy
```

## Key decisions

- **One engine, two modes.** The desktop app and the Team Server run the same engine. The desktop app is a single
  user on loopback. The Team Server adds accounts and public webhooks. Collaboration happens on the Team Server; the
  desktop app can open it instead of its local engine.
- **SQLite.** WAL mode, one connection per thread, write transactions serialised in-process. SQLite suits a desktop
  app and a team-sized server, and needs no administration. Datasets are tables named `ds_<id>` with generic column
  keys. PostgreSQL support is on the roadmap for larger deployments.
- **Own agent loop, no agent framework.** Approvals need full control over the loop: pausing mid-run, persisting
  pending calls, and resuming after a decision or a restart. The loop speaks two wire protocols (OpenAI Chat
  Completions and Anthropic Messages), which together cover every listed provider.
- **One model for every view of a file.** A file definition is resolved once into a model in which every cell has its
  value, number format, live formula, readable formula, source and address. The Excel writer, the Word writer, the
  in-app viewer and the reviewer all read that model, so they cannot disagree. The formula language is parsed by the
  engine (no `eval`), compiled to Excel and evaluated with Excel's rules (blank as zero, division by zero shown blank,
  half-away-from-zero rounding at 15 significant digits).
- **Maker and checker are separate calls.** The reviewer gets a fresh context with only the file, its sources and
  read-only tools; it never sees the drafting conversation.
- **Small requests.** Tool definitions are grouped; each request carries the groups the conversation needs and a
  `more_tools` tool for the rest, in a fixed order after a stable system prefix, so provider prompt caches stay valid.
  Old tool results are shortened in what is resent, never in what is stored. See [COSTS.md](COSTS.md).
- **Interface text is data.** The UI is written in English with `tr()`; dictionaries per language; Arabic flips the
  layout with logical CSS properties. See [LANGUAGES.md](LANGUAGES.md).
- **Typed output blocks.** Tools return text for the model plus typed blocks (`table`, `kpis`, `link`, `skill`) that
  the UI renders natively.
- **Events.** Each run publishes events to an in-process hub. The UI subscribes with a streaming GET (server-sent
  events over `fetch`, so the bearer token can be sent). Late subscribers receive the events of the current run.
- **Workflows are data.** Definitions are validated by pydantic (discriminated unions). Templating is
  `{{path.lookup}}` only, with no expressions. Runs persist step index, context and log, so they resume after
  approvals and restarts.
- **Dependencies.** All dependencies are permissive and few: Starlette, uvicorn, pydantic, httpx2, mcp, openpyxl,
  cryptography, PyYAML, python-multipart; React and Electron. Charts, the Markdown renderer and the workflow editor
  are written in the project, so there is no runtime CDN or font download.

## Data model (main tables)

`users, sessions, workspaces, members, invites, secrets, settings, audit_log, llm_providers, threads, messages,
agent_runs, approvals, usage, files, file_versions, file_findings, ingest_keys, doc_collections, documents,
doc_chunks (+ FTS5 index), messages_fts, memories (+ FTS5 index), password_resets, login_states, skills,
skill_settings, datasets (+ ds_* tables), dashboards, connections, oauth_states, mcp_servers, mcp_keys, workflows,
workflow_runs, comments, notifications, channel_links, pairing_codes`

## Roadmap

- Write-back to accounting systems: draft bills and journals, with approval.
- Close room and auditor request lists (PBC) built on evidence-linked files; house-style templates learned from a
  sample report; slide output for board packs.
- More connectors: Sage, NetSuite, FreshBooks, MYOB. Bank feeds through aggregators by region.
- PostgreSQL for large Team Servers; SAML SSO and TOTP.
- Keeping charts and images when editing the user's own workbooks.
- Scheduled dashboard e-mails and PDF export.
- Auto-update for the desktop app.
