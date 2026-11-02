# Security model

## Threats considered

- A model that is wrong, manipulated by content it reads (prompt injection in e-mails or documents), or simply
  verbose.
- Another member of the same Team Server trying to reach a workspace they do not belong to.
- A web page trying to reach the local desktop engine (DNS rebinding, CSRF).
- Stolen disk or backup without the master key.
- Account takeover through password reset, sign-in with another identity provider or a guessed webhook address.
- Malicious files: spreadsheets, Word documents, scanned images, skill archives.
- User-supplied URLs used to reach internal services (SSRF).

## Controls

**Agent actions.**
- Tools declare their effect. Read tools run directly. Write tools pause the run and create an approval that shows
  the exact arguments. Write tools include: sending or drafting e-mail, messaging colleagues, running workflows,
  importing e-mail attachments or Drive files into the workspace, editing a person's own file, and MCP tools set to
  *ask*. Refreshing datasets from a read-only accounting connection and creating a new evidence-linked workbook or
  report (a new version the reviewer checks) run without approval.
- Approving needs the editor role or higher. Decisions are audited.
- Tools carry the role their API route needs. A viewer's assistant is not offered — and, if a model names them
  anyway, is refused — the tools that create or refresh workspace records without an approval: dashboards,
  generated workbooks and reports, editable copies of documents, month-end close preparation, accounting refreshes
  and file edits. The role is read from the membership at the time of each call, never from the model.
- MCP tools may run unattended only when the administrator allows it **and** the server marks the tool read-only.
- The agent has no shell, file-system or SQL access.
- Content the agent reads (e-mails, documents, MCP output) cannot approve anything. At worst it can make the agent
  *propose* an action, which a person then sees.

**Figures.**
- Arithmetic runs in code (calculator tools, query compiler).
- After each answer, numbers that appear in no tool result or user message are listed under the answer so reviewers
  can check them.

**Tenancy.**
- Every workspace-owned row carries `workspace_id`, and every query filters by it.
- Membership and role are checked on each request. Private conversations are visible only to their author.
- Tests cover cross-workspace access.

**Secrets.**
- AES-256-GCM using a 32-byte master key. A random nonce is used per value, and associated data binds each value to
  its id, purpose and workspace.
- Desktop: the master key is generated on first start and kept encrypted by the operating system (`safeStorage`). On
  Linux without a keyring the app warns and falls back to Electron's built-in key.
- Team Server: `DESK_MASTER_KEY` comes from the environment and is never written to the database. Back it up
  separately from data backups.
- Secrets are never returned by the API.

**Desktop engine.**
- Binds to 127.0.0.1 only and requires a per-launch 256-bit token.
- Requests with a Host other than `localhost`/`127.0.0.1` are refused, which blocks DNS rebinding.
- Stdio MCP servers run with a minimal environment.

**Team Server.**
- scrypt password hashing (N=2^15) and a minimum password policy.
- Opaque session tokens, stored hashed. Cookies are HttpOnly, Secure and SameSite=Strict, and cookie-authenticated
  writes must come from the server's own origin.
- **Sessions.** A session ends after `DESK_SESSION_IDLE_MINUTES` of inactivity — 30 by default; an operator may
  set 30 to 45, and a value outside that range is pulled back into it (never switched off on a server) — and the
  row is deleted rather than merely refused, so the old cookie or token can do nothing afterwards and a ping
  cannot revive it. Activity is a request that changes something, which includes the page's heartbeat
  (`POST /api/session/ping`, sent at most once a minute and only after the person has used the page). **Reading
  never counts**: the pages that poll, the event stream carrying an assistant's answer, `GET /api/session/status`
  and a tab refreshing itself cannot hold a session open with nobody there, and nothing the page sends carries a
  timestamp the engine would believe. Two minutes before the end the page shows *"Your session will expire in
  2 minutes due to inactivity"* (also in Arabic, Spanish and French) with *Stay signed in* — one authenticated
  POST that the engine refuses if the session has already ended — and *Log out now*. When the end comes the page
  signs out through `POST /api/auth/logout?reason=idle`, clears what it kept, tells every other tab through
  `localStorage`, and shows the sign-in screen with the reason. Open tabs share one activity clock, so working in
  one keeps the others signed in; after a sleep, a reconnection or a long time in the background the page asks
  `GET /api/session/status` how long is really left, and that answer can only shorten what the page believes.
  Beside the idle window every session has an absolute lifetime, counted from sign-in whatever the person does:
  `DESK_SESSION_HOURS` (12 by default, 1–24 on a server) and, for anyone who is admin or owner of any workspace or
  the instance administrator, `DESK_ADMIN_SESSION_HOURS` (8 by default) — judged from the current memberships at
  every request, so a promotion shortens the sitting session. (`DESK_SESSION_DAYS` from earlier releases is still
  read, as days × 24, with a warning.) All knobs refuse a negative or non-numeric value at start-up.
- **Step-up for high-risk actions.** Changing a member's role or removing a member, connecting or disconnecting an
  accounting system, a channel or an OAuth app, adding or changing an MCP server, creating an MCP access key, adding
  or removing a model provider, closing or deleting a month-end close, deleting a dataset, changing the workspace's
  egress policy or review settings, deleting a workspace, an administrator's reset link or session termination, and
  **approving an assistant action that leaves the workspace** all need the person to have entered their password
  within the last `DESK_REAUTH_MINUTES` (10) on this session — at sign-in or through `POST /api/auth/reauth`, which
  is rate-limited like a sign-in and audited either way. The page asks for the password in place and sends the
  request once more; a Google-only account signs in again instead. Step-up is on top of the role checks and the
  approvals, never instead of them. The desktop app, which has no password, does not ask.
- **Revocation.** A session ends on logout (that device), on the idle or lifetime limit, when the account is
  disabled (next request), on a password change or reset (every session of that person), when the person ends
  another of their own sessions (`GET/DELETE /api/auth/sessions`, `POST /api/auth/sign-out-everywhere`) and when
  the instance administrator terminates an account's sessions (`POST /api/admin/users/{id}/sign-out`, after
  confirming their password). Removal from a workspace or a lost role takes effect on the next request without
  ending the session, and expires that person's pending approvals. Expired, revoked and failed sign-ins are audit
  events (`session.expired`, `session.revoked`, `session.logout`, `user.login.failed`, `user.reauth.failed`) with
  the user and the reason, never the password or the token.
- **Background credentials are not sessions.** MCP access keys, a push connection's ingest key and a workflow's
  webhook secret each open only their own endpoint and never a session; a session cookie opens none of them.
- **Desktop.** The desktop app has no accounts: the engine runs for this computer's user under a per-launch token,
  so there is no session to end. After the same inactivity window the page locks instead — every page is unmounted,
  nothing financial stays on screen, and a deliberate click brings the app back — and it locks at once when the
  computer is locked or put to sleep (the shell tells it). This protects an unattended, unlocked computer from a
  passer-by; it cannot protect against someone who already holds the account (see "Threats considered").
- Only sessions work this way. The MCP keys, a push connection's ingest key and a workflow's webhook secret are
  not sessions: they do not expire and are revoked by rotating them.
- Rate limits on sign-up and sign-in. Sign-up is by single-use invitation, except for the first account.
- Behind Caddy with automatic HTTPS and HSTS.

**Web UI.**
- CSP: `default-src 'self'` and `script-src 'self'`, with no inline scripts. Also `frame-ancestors 'none'`,
  `X-Frame-Options: DENY`, `nosniff` and `no-referrer`.
- Model output is parsed from Markdown into React elements. There is no `dangerouslySetInnerHTML`, and links open
  externally only for http(s).
- E-mail HTML is converted to text.

**Electron.**
- `contextIsolation`, `sandbox`, no `nodeIntegration`, no `webview`.
- Navigation is limited to the app's origin. New windows and external links go to the system browser (http/https
  only). Permission requests are denied.
- The preload bridge exposes only the token, the version, and an `openExternal` call.

**Data import.**
- Size limits and read-only parsing (`openpyxl` in data-only mode). Formulas are not evaluated.
- Column names never become SQL identifiers.
- Skill archives: path traversal is refused, only text files are kept, and nothing is executed.
- CSV exports prefix cells starting with `= + - @`.

**Documents.**
- Original files are stored with owner-only permissions and are downloaded as attachments, never rendered by the
  app. Searches and passages are filtered by workspace.
- Connector tokens are stored encrypted, like other credentials. Use a token with only the scopes listed in
  docs/KNOWLEDGE.md and a bounded expiry.
- Chat attachments go through the same checks as uploads: allowed types, size limit, encrypted storage, and
  workspace checks on the document ids sent with a message.

**Files and storage.**
- Stored files (documents, imports, generated workbooks and reports) are encrypted by the engine with AES-256-GCM
  before they are written, locally or to S3. The key is derived from the master key, and the object key is the
  associated data, so the storage operator cannot read them and a copied object does not decrypt.
- The S3 client signs requests with Signature V4 and checks the result against AWS's published examples. The MinIO
  overlay gives the engine a bucket-scoped user; the root credentials are used only by the one-shot init container.
- Formulas in files come from the engine's own parser and compiler, never from model text. Text that starts with
  `=` is written as text.
- Links inside files open the app at a route (`ofintech-desk://open/…` on the desktop, the server URL otherwise). The
  desktop app accepts only route characters and a few known parameters from such links.
- The reviewer runs with read-only tools and without the drafting conversation, so content in the conversation
  cannot steer it.

**Connector keys.**
- Odoo and adapter keys are stored as secrets. Ingest keys for push connections are shown once, stored as hashes,
  limited to 60 requests a minute and 100 MB per request, and can be rotated, which revokes the old key. Every push
  is audited.
- Connector addresses follow the outbound-request rules below.

**Outbound requests.**
- On the Team Server, user-supplied URLs (custom model endpoints, MCP servers, connectors, webhooks) must be HTTPS and
  must not resolve to private, loopback, link-local, shared (100.64/10) or reserved addresses. URLs with embedded
  credentials are refused.
- The same judgement is repeated when each request is made (`egress.PrivateNetworkGuard` wraps the engine's HTTP
  transport in server mode), so a name that later resolves to an internal address, or a redirect to one, is refused at
  the connection. The configured S3 endpoint and hosts listed in `DESK_EGRESS_ALLOW` are exempt, because they are the
  operator's own services. On a sealed server the guard is replaced by the sealed transport, which allows only the
  private network.

**Webhooks.**
- Telegram: a secret token header. WhatsApp: an HMAC-SHA256 signature with the app secret.
- OAuth `state` is single-use and expires after 15 minutes. PKCE is used where the provider supports it.
- Connections → Webhooks (WEBHOOKS.md): secrets of 30 random bytes shown once, stored encrypted and compared in
  constant time (bearer or HMAC-SHA256 of the body for incoming; every outgoing delivery signed with HMAC-SHA256);
  256 KB and 60 deliveries a minute per incoming webhook, 64 KB per outgoing payload with credential-like keys
  removed; outgoing addresses follow the outbound-request rules and redirects are not followed; the last 500
  deliveries are kept; every change is audited and events about webhooks are never sent through them.

**Accounts.**
- Passwords: scrypt; at least 12 characters with mixed case and a digit. Changing or resetting a password ends every
  other session.
- Sign-in and sign-up forms on the website (`DESK_SITE_URL`): accepted only from the website's or the app's origin
  (`Origin`/`Referer` checked), the same rate limits and password policy as the app, no e-mail enumeration (an
  existing address on an invitation-only server gets the same "by invitation" answer), and short error codes in the
  redirect rather than messages that could be spoofed.
- Password reset: the same answer whether or not the address has an account; at most 3 requests per address per 15
  minutes and 20 per IP; a random single-use token stored only as a hash, valid for one hour.
- Google sign-in: OIDC authorisation code flow with PKCE and a single-use `state` (15 minutes); only verified e-mail
  addresses; optional domain allow-list (`DESK_GOOGLE_ALLOWED_DOMAINS`) applied to every Google sign-in; never
  creates an account on an invitation-only server without an invitation.

**Memory.**
- Nothing proposed by the model is used before a person saves it; workspace memory needs the editor role.
- Secrets (passwords, keys, tokens, private keys, IBANs, Luhn-valid card numbers, account numbers) are refused.
- Memory is separated by workspace, and personal memory by person; every change is audited. Memory is labelled in
  the prompt as possibly out of date, and answers show which entries they used.

**The user's own files.**
- Accepted: `.xlsx` and `.docx` up to 25 MB; macro-enabled, old binary and encrypted files are refused rather than
  silently changed. Version 1 is the untouched original; every edit makes a new version.
- Formulas written by the model are checked: they may only refer to the same workbook; links to other files, URLs,
  DDE (`|`) and functions such as `WEBSERVICE`, `FILTERXML`, `HYPERLINK`, `CALL`, `REGISTER`, `RTD`, `INFO` and
  `CELL` are refused.
- The model cannot read or write anything but the file it was given, through the edit operations listed in
  FILES.md; there is no general file or shell access.

**Workflow webhooks.**
- One secret per workflow, shown once, stored as a SHA-256 hash and compared in constant time; a new secret
  replaces the old one at once.
- 256 KB per event, 30 events a minute per workflow; refused while the workflow is off. The event becomes data in
  `{{trigger.…}}` lookups only (no expression evaluation). It reaches the model only where a step's instruction
  includes it, and is treated like any untrusted document: actions that leave the app still need an approval.

**Audit.**
- Logged: sign-ins, membership and role changes, connections, approvals, tool executions, imports, deletions, file
  uploads and edits, memory changes, webhook secret changes, and MCP-server calls.

## Not done yet

- SSO/SAML and TOTP two-factor sign-in on the Team Server.
- Per-workspace encryption keys and key rotation tooling.
- Admin-configurable data retention.
- Code signing and notarisation are configured in CI, but need the company's certificates.
- An external penetration test.

What the assistant can never do (payments, data leaving the organization), and the tests that prove it: [DATA_BOUNDARIES.md](DATA_BOUNDARIES.md).
