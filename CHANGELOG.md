# Changelog

All notable changes are listed here. Versions follow [Semantic Versioning](https://semver.org/); database
migrations are applied automatically on start-up and are never rewritten once released.

## Unreleased

### Added

- **Desktop client for macOS and Windows** that opens the Team Server at `https://desk.ofintech.ai` (configurable at
  build time), without a local engine: `desktop/electron-builder.cloud.yml` and the `desktop-cloud` workflow, an
  offline page with *Try again*, and docs/DESKTOP.md.
- **Google sign-in from the desktop app** through the system browser, handed back to the app with a single-use,
  two-minute code bound to the app that started it (migration 12).

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
