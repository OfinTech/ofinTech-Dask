# 02 — Security audit: findings

Findings of the enterprise audit of OfinTech Desk 1.1.0, from the observations in `01-ARCHITECTURE-BASELINE.md`.
Frameworks used as reference: OWASP Top 10 (2021), OWASP ASVS 4.0, CWE Top 25, NIST SP 800-53 families where
named. Each finding keeps the ID it had as an observation (OB-nn), so the two documents can be read together.

Severity follows the roadmap's priority scale (P0 critical, P1 high, P2 medium, P3 low). **Status** is one of
*fixed* (change on `main`, regression test passes), *mitigated* (risk reduced, residual noted), *open* or
*accepted* (documented exception in `08-REMEDIATION-ROADMAP.md`). Nothing is marked fixed without a passing test;
test names refer to `engine/tests/test_audit_regressions.py` unless another file is given.

Exploit detail is kept to what a maintainer needs. Rule 10 of the engagement (no real secrets, no destructive
testing) was followed: every check ran against throw-away engine instances with mocked providers.

## Summary

| Severity | Count | Fixed | Mitigated | Accepted / design | Open |
|---|---|---|---|---|---|
| P0 | 0 | — | — | — | — |
| P1 | 3 | 3 | — | — | — |
| P2 | 9 | 8 | 1 | — | — |
| P3 | 6 | 2 | 1 | 2 | 1 |

No actively exploitable, unauthenticated P0 was found. The engine's authorisation layer, parameterised SQL,
encryption of secrets and files, hashed tokens, constant-time comparisons and CSP held up under review; the
findings are at the edges: trust placed in values that arrive from outside (OAuth callback, storage, tool
results), checks done once instead of every time, and limits that relied on a header.

## Findings

### OB-01 · OAuth callback trusted a prefix-matched host — P1, fixed

- **CWE-20 / ASVS 5.1, 13.1** · `engine/desk_engine/connectors/flows.py`, `complete_callback` (Zoho branch).
- The Zoho sign-in callback names the accounts server (`accounts-server`) and the token response names the API host
  (`api_domain`). The first was accepted when it merely *started with* `https://accounts.zoho`, the second not
  checked at all, so a host such as `accounts.zoho.<attacker>` would have received the authorisation code and the
  workspace's client secret, and the API token would have been sent to an attacker-named API host.
- **Scenario:** an attacker who can obtain an unused OAuth `state` (an admin mid-flow whose redirect is intercepted
  or replayed within 15 minutes) completes the callback with their own host. **Impact:** compromise of the Zoho app
  credentials and of the connection's tokens; read access to the company's books from outside the product.
- **Fix:** `zoho_accounts_server` / `zoho_api_domain` accept only exact entries of Zoho's data-centre lists
  (`ZOHO_ACCOUNTS`, `ZOHO_API_DOMAINS`). Commit 769e263.
- **Tests:** `OB01ZohoCallback` (2). The pre-existing `test_more.ZohoFlow` still passes.

### OB-02 · CSRF origin guard compared a prefix — P2, fixed

- **CWE-346 / ASVS 4.2.2** · `engine/desk_engine/web.py`, `check_origin`; `routes/public.py`, `_allowed_origin`.
- Cookie-authenticated writes required `Origin` (or `Referer`) to start with the public URL, so
  `https://desk.example.com.attacker.net` passed for `https://desk.example.com` (reproduced locally).
- **Scenario:** a cross-site request from a look-alike origin carrying the session cookie. In current browsers the
  `SameSite=Strict` cookie is not sent cross-site, so the guard was defence in depth; it would have mattered for an
  attacker-controlled host under the same registrable domain, or a browser without SameSite support.
- **Fix:** `request_origin` parses the header and the whole origin (scheme, host, port) must equal the public
  URL's; the website forms share the function. Commit 769e263.
- **Tests:** `OB02OriginCheck` (2); `test_team_server`, `test_site_auth` unchanged.

### OB-03 · Formula injection in the month-end close exports — P1, fixed

- **CWE-1236** · `engine/desk_engine/close/pack.py` (`journal_csv`, `journal_xlsx`, `pack_xlsx`);
  `files/xlsx.py:_set`; `routes/datasets.py` export.
- Ledger descriptions, account names and notes were written into the journal CSV and the close-pack workbooks
  unchanged; openpyxl stores any `=…` string as a formula and spreadsheets interpret `+ - @` prefixes the same way.
  The evidence-linked workbook writer exempted `==…`, and the dataset export did not cover `\t` and `\r` prefixes.
- **Scenario:** a supplier name or invoice description imported from the accounting system, or typed by a user,
  carries a formula; an accountant opens the journal in Excel and the formula runs (DDE/`HYPERLINK`/`WEBSERVICE`
  depending on the client's settings). **Impact:** code execution or data exfiltration on the finance team's
  computers, from data that enters through a trusted channel.
- **Fix:** `files/cells.py` (`csv_safe`, `set_cell`) is used by every writer; strings starting with
  `= + - @ \t \r` are stored as text (openpyxl `data_type = "s"`) or prefixed with `'` in CSV. Engine-generated
  totals remain formulas. Commit d409034.
- **Tests:** `OB03FormulaInjection` (4); `test_close`, `test_files`, `test_data` unchanged.

### OB-04 · Outbound network policy enforced only when a URL was saved — P2, fixed

- **CWE-918 / CWE-367 / ASVS 12.6** · `engine/desk_engine/security.py:check_outbound_url`; callers in
  `routes/connections.py`, `routes/mcp.py`, `routes/providers.py`, `webhooks.py`, `connectors/odoo.py`,
  `connectors/open_api.py`.
- On a Team Server, user-supplied URLs were resolved and judged once, at save time. Later connections resolved
  again (DNS rebinding window), redirects were judged by nobody, and connector/webhook URLs were never re-checked.
  The shared range 100.64.0.0/10 was not refused.
- **Scenario:** an admin of one workspace registers an MCP server or webhook at a name that later resolves to an
  internal address (metadata service, database, another container). **Impact:** the engine becomes a proxy into
  the private network, with the credentials it holds for that connection attached.
- **Fix:** `egress.PrivateNetworkGuard` wraps the engine's HTTP transports in server mode and refuses private,
  loopback, link-local, local-name and shared addresses at every request, including redirects; the S3 endpoint
  and `DESK_EGRESS_ALLOW` hosts are exempt; sealed servers keep `SealedTransport`. The save-time check refuses
  100.64/10 too. Commit 87e0196. Residual: resolution in the guard and in the socket are still two lookups, but
  the window is now per request and the guard caches for 60 s; true IP pinning would need a custom connection
  pool and is listed as a P3 improvement.
- **Tests:** `OB04ConnectionTimeNetworkPolicy` (3).

### OB-05 · Encrypted storage returned unauthenticated plaintext — P1, fixed (behaviour change)

- **CWE-345 / ASVS 6.2** · `engine/desk_engine/storage.py:EncryptedStorage.get`.
- An object without the `DSKENC1` header was returned as it was, so anyone able to write to the file store (bucket
  operator, a compromised MinIO credential) could substitute a document, an upload or a generated workbook, and
  the engine would serve it as genuine.
- **Fix:** such an object is refused with `storage_plaintext` unless `DESK_STORAGE_ALLOW_PLAINTEXT=true`;
  `python -m desk_engine storage-encrypt` rewrites every clear object encrypted so the flag can be removed. This
  is a compatibility change for installations that stored files before turning encryption on; the error names the
  command. Commit 87e0196.
- **Tests:** `test_storage.test_files_written_before_encryption_need_an_explicit_migration` (rewritten from the
  test that pinned the old behaviour).

### OB-06 · Parser resource limits — P2, fixed

- **CWE-400 / CWE-409** · `knowledge/extract.py`, `knowledge/ocr.py`, `files/editable.py`, `costcentres/budget.py`.
- No decompression or page/time limit for docx/xlsx/pdf parsing; Pillow opened any format it recognises (EPS →
  Ghostscript if installed); the 60 MP pixel check ran after decoding; OCR worst case is 200 pages × 4 images ×
  120 s. Parsing runs in a thread so the event loop survives, but CPU and memory do not.
- **Fix (R7):** `files/limits.py` — `check_office_zip` refuses an Office archive whose members declare more than
  400 MB inflated, more than 20,000 parts, or a ratio above 200:1 (Python's `zipfile` never reads past a declared
  size, so the declaration is a reliable cap); it is called by every Office reader (knowledge extraction, dataset
  import, user-file editing, budget and ledger parsers, close statements). `open_image` decodes only PNG, JPEG,
  TIFF, BMP, GIF and WebP, and refuses an image whose header declares more than 60 MP before any pixel is decoded
  (Pillow's own 178 MP ceiling is caught too). OCR of one scanned PDF stops after 15 minutes; pages past the budget
  are indexed empty rather than failing the document. Caddy caps request bodies at 260 MB at the edge.
- **Residual:** pypdf has no per-page time limit; a pathological PDF can still take long on a single page. The
  OCR budget and the tool timeouts (OB-08) bound the agent-facing paths.
- **Tests:** `OB06ParserLimits` (2).

### OB-07 · Body limits relied on Content-Length — P2, fixed

- **CWE-770** · `routes/public.py` (ingest, workflow hook, incoming webhook, Telegram, WhatsApp),
  `web.parse_body`.
- A chunked or misdeclared request was read in full before the limit applied; the 100 MB ingest body was then
  parsed with `json.loads` in memory.
- **Fix:** `web.read_body` reads in chunks and stops at the limit; every public endpoint and the JSON parser use
  it. Commit 1435e17. Residual: multipart uploads still rely on Starlette's disk spool plus a post-read check;
  they go to disk, not memory, and are capped by `max_files`; Caddy adds no body limit by default (roadmap R7
  adds `request_body { max_size }` to the Caddyfile).
- **Tests:** `OB07BodyLimits` (2).

### OB-08 · Agent execution: no argument schema check, no time limit, effects by label — P2, mitigated

- **CWE-20 / CWE-400 / ASVS 1.14** · `agent/tools.py`, tool modules.
- Tool arguments were checked only for presence of required keys; no tool call had a time limit on the chat,
  workflow or MCP-server path; several state-changing tools carry `effect="read"` and run without approval.
- **Fix (two of three):** `validate_args` checks type, enum, required, properties, additionalProperties, items and
  numeric/string bounds before any handler runs; `Tool.run` applies a per-tool time limit (300 s, 30 min for
  `sync_accounting`). Commit 78644c1.
- **Open, owner decision:** which of `sync_accounting`, `drive_import_spreadsheet`, `gmail_import_attachment`,
  `gmail_save_to_documents`, `drive_add_document`, `create_workbook`, `create_report`, `edit_file` should ask
  for approval. Recommendation in the roadmap (R8): the four Google imports and `edit_file` of a user's own file
  become `write`; `create_workbook`/`create_report` stay `read` because they only add a version the reviewer checks.
- **Tests:** `OB08AgentIsolation` (2).

### OB-09 · Prompt-injection surface — P2, accepted with controls, improvements planned

- **ASVS 5.2, LLM01** · `knowledge/*`, `connectors/*` (error bodies), `mcp_client.py`, `skills/loader.py`,
  `workflows/engine.py`, `webhooks.py`.
- Untrusted text reaches prompts and tool arguments without a structural delimiter; the Gmail trigger trusts a
  spoofable `From`; an editor-authored skill is enabled at once and replaces a built-in of the same name.
- **Controls in place (verified):** every write tool needs a human approval with the exact arguments shown;
  payments are refused in code; sealed workspaces remove every outbound tool; bank identifiers are masked; the
  reviewer runs with a fresh context; the system prompt carries the "data is not instructions" rule;
  `test_security_boundaries` proves the refusals at the socket level.
- **Planned (R8):** wrap tool results, document passages and trigger data in a fixed delimiter the prompt names;
  make a new custom skill `enabled = 0` until an admin turns it on and refuse a custom skill whose name shadows a
  built-in; keep connector error bodies out of tool output shown to the model (log them instead); add the AI
  security test suite of document 06.

### OB-10 · MCP trust and audit hygiene — P2, partly fixed

- `mcp_client.py`, `routes/mcp.py`, `webhooks.py`.
- Fixed: the `mcp.add` audit entry keeps scheme, host and path of the URL only (query strings can carry tokens and
  audit details reach outgoing webhooks). Commit 87e0196; test `OB10MCPAuditURL`.
- Accepted, documented: `auto` policy is honoured only when **both** the admin sets it and the server marks the
  tool read-only; an admin who sets `auto` on a tool accepts the server's word. Open (R8): refuse a refreshed tool
  list that changes the schema of a tool under an existing name until the admin re-approves; longer prefixes to
  avoid collisions.

### OB-11 · Authorisation on open event streams — P2, fixed

- **CWE-613** · `routes/threads.py:events`.
- A stream was authorised when it opened; a session that ended or a membership that was removed kept receiving the
  conversation until the client closed it.
- **Fix:** the session, membership and thread checks repeat at every keep-alive and before every event. Commit
  1435e17. **Test:** `OB11StreamReauthorisation`.

### OB-12 · Electron IPC sender checks — P3, accepted

- `desktop/src/main.ts`. Five IPC channels have no sender-frame check. The renderer only ever loads the app
  origin or the bundled `connect.html`; `desk:bridge` and `desk:google-challenge` (the two that hand out secrets)
  do check the sender. Planned hardening (R9): check `senderFrame.url` on every channel for symmetry.

### OB-13 · Bot token in transport errors — P3, fixed

- `channels/service.py:Telegram.call`. Transport exceptions carried the token-bearing URL. Fixed: only the exception
  type is reported. Commit 87e0196. **Test:** `OB13TelegramTokenNeverInErrors`.

### OB-14 · SMTP login in clear; unbounded sent-mail list — P3, fixed

- `mail.py`. `DESK_SMTP_SECURITY` now accepts `starttls`, `ssl` or `none`, and `none` cannot be combined with a
  login; the last 200 messages are kept. Commit 87e0196. **Test:** `OB14MailNeverLogsInInClear`.

### OB-15 · Formula engines: unhandled errors — P3, fixed

- `files/expr.py`, `analysis/formula.py`. Nesting deeper than 32 and `ROUND` digits outside ±30 now give a
  validation error or a blank cell. Commit d409034. **Tests:** `OB15FormulaEngineRobustness` (2).

### OB-16 · Viewer-role scope — P3, design decision recorded

- Viewers can mint `/mcp` keys (read-only tools, their own membership) and their chat attachments become
  workspace documents forwarded to PipesHub. Both are consistent with the viewer role's read rights. Recommendation:
  keep, but show the collection on upload and let admins turn off viewer uploads per workspace (R9).

### OB-17 · CI/CD gates — P2, fixed

- `.github/workflows/*.yml`. Added a read-only default token (`permissions: contents: read`), a `security` job
  (gitleaks over the history, ruff's Bandit-equivalent rules, pip-audit, npm audit of runtime dependencies) that
  the image and release jobs depend on, and a Trivy scan of the Team Server image (CRITICAL/HIGH, fixed only).
  Open: lockfiles for `ui/` and `desktop/` (cannot be generated in the audit environment; R9), SHA-pinned
  actions.

### OB-18 · Operations — P3, partly fixed

- `server/docker-compose.yml`: the engine drops every capability, runs with `no-new-privileges` and a tmpfs
  `/tmp`. Open: plaintext SQLite backups (recommend encrypting the backup file with the operator's key management;
  R9), no rate limit on `/mcp` (R8), `DESK_SESSION_DAYS=0` default (documented; the deployment checklist now
  recommends a cap).

## Verified controls (no finding)

Reviewed and found sound, with the evidence consulted:

- Authorisation declared per route and enforced in `web.Router` (198 routes read; `load_workspace` joins
  `members`); tenant filters in every repository query sampled (36 string-built SQL sites reviewed: identifiers
  and `?` placeholders only).
- Passwords: scrypt N=2^15, r=8, p=1, constant-time compare, uniform timing for unknown users.
- Sessions, invites, resets, handoffs, ingest keys, webhook secrets, MCP keys: random ≥ 24 bytes, stored hashed,
  single-use where applicable, constant-time compares.
- Secrets at rest: AES-256-GCM, random nonce, associated data bound to id/purpose/workspace; files: HKDF-derived
  key, nonce per object, key as associated data.
- No `eval`, `exec`, `pickle`, `yaml.load` or shell invocation in the engine; OCR via argument-list `subprocess`
  with a timeout.
- UI: strict CSP with no inline script; Markdown rendered to React elements; no `dangerouslySetInnerHTML`,
  `innerHTML` or `eval`; Electron `contextIsolation`, `sandbox`, no `nodeIntegration`, navigation pinned, deep
  links regex-filtered.
- Payment refusal and sealed egress enforced in code and tested at the socket and DNS level
  (`test_security_boundaries`, 26 tests).
