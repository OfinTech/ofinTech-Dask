# 05 — API security review

The engine's HTTP API: 198 authenticated routes in `engine/desk_engine/routes/*.py` through `web.Router`, 10
public routes in `routes/public.py`, the MCP endpoint, and the static UI. Reference: OWASP API Security Top 10
(2023). Findings are tracked under their OB/DB ids; this document records what was checked and what holds.

## 1. Request handling model

- One decorator per route declares method, path, `auth`, `role` and `body` model (`web.Router.route`). The
  decorator authenticates, loads the workspace from the `{ws}` path parameter **joined with membership**, checks
  the role, validates the body with pydantic (JSON, 2 MB, now streamed), and maps `AppError` subclasses to JSON
  with stable `error` codes. Anything else is a generic 500 with the detail in the log.
- Responses are JSON or `Response(204)`; file downloads are `attachment` with sanitised names; the UI is served
  with a path-containment check.
- No CORS middleware: the API is same-origin only, which is correct for a first-party SPA; the website forms are
  the one cross-origin writer and are checked by exact origin.

## 2. OWASP API Top 10 walkthrough

| # | Risk | Status | Evidence |
|---|---|---|---|
| API1 | Broken object level authorisation | **holds** | every object is fetched with `workspace_id` (datasets through `store.get_dataset`, documents, files, threads with private-visibility check, approvals with `workspace_id`); `test_team_server`, `test_ai_security` case 6 |
| API2 | Broken authentication | **holds** | scrypt, hashed tokens, idle/absolute limits, OIDC with PKCE and verified e-mail, single-use links, exact origin (OB-02), handoff bound by PKCE |
| API3 | Broken object property level authorisation | **holds** | pydantic bodies with explicit fields; connection listings filtered to `SAFE_CONFIG_KEYS`; secrets never serialised; `public_user` projection |
| API4 | Unrestricted resource consumption | **improved** | body limits streamed (OB-07), parser limits (OB-06), tool timeouts (OB-08), Caddy cap; open: `/mcp` rate limit, in-memory limiters |
| API5 | Broken function level authorisation | **holds** | roles per route; `instance_admin` for OAuth app credentials and reset links; approvals need editor; skills enabling needs admin (now also for editor-written skills) |
| API6 | Unrestricted access to sensitive business flows | **holds** | write tools need approval; payments impossible; workflows run as their creator; sign-up by invitation |
| API7 | SSRF | **fixed** | save-time and request-time checks (OB-04); sealed transport |
| API8 | Security misconfiguration | **improved** | strict CSP, `nosniff`, `DENY`, HSTS; `DESK_DEV` refused in server mode; https required; SMTP modes validated (OB-14); containers hardened (R6); open: SHA-pinned actions, lockfiles |
| API9 | Improper inventory management | **holds** | one version of the API, every route in one registry; OpenAPI published only for the Open Accounting Connector; no debug or admin routes |
| API10 | Unsafe consumption of APIs | **improved** | connector payloads validated by pydantic with size caps; exact provider hosts (OB-01); error bodies truncated; MCP results capped; open: MCP tool-list re-approval |

## 3. Per-area notes

**Authentication routes.** Rate limits are keyed by e-mail (8/5 min) and client IP (30/5 min) in process
memory; behind the shipped Caddy the IP is the real one (`forwarded_allow_ips="*"` is correct only because port
8700 is not published). Anyone who exposes the engine directly must drop that setting; `docs/DEPLOYMENT.md`
says so. The login limit per e-mail allows a cheap lockout of a known address (8 wrong passwords); acceptable for
a team product, noted for R9 (progressive delay instead of a hard refusal).

**Session token in the JSON body.** `/api/auth/login` and `/signup` return `token` alongside setting the
cookie. The web UI never uses it (cookie only), the desktop app in team mode uses the cookie too. Removing it
would be cleaner (R9, after checking no third-party client relies on it).

**Workspace routes.** `PATCH /api/w/{ws}` (admin) cannot change `egress_policy` except by the owner
(`test_security_boundaries.test_the_owner_can_reopen`). `DELETE /api/w/{ws}` is owner-only and cascades.

**Uploads.** Four upload routes (`documents`, `datasets/imports`, `files/upload`, `cost-centres/ledger/upload`,
`close/.../statement`, `skills/import`) check the header, then the real size, then type (extension or zip content),
then the new archive limits. `max_files` is set on every `request.form` call. Chat attachments by viewers go to a
workspace collection (OB-16, design note).

**Downloads.** Always `attachment`, `application/octet-stream` or the file's own type, names sanitised to
`[A-Za-z0-9 ._-]`. Generated workbooks are served from encrypted storage after decryption in the engine.

**Public routes.** Each one authenticates by a different mechanism and none shares state with sessions; all now
stream bodies with a cap. The `/api/connector-spec/openapi.json` route is unauthenticated by design (it is the
public contract for adapters) and reveals only the public URL.

**MCP endpoint.** Bearer key, read-only tools, workspace-scoped, result cap; the one open item is the missing
rate limit (OB-18).

**Error responses.** `AppError.to_dict()` returns `error`, `message`, optional `details` (pydantic field errors).
Upstream errors are truncated to 200–300 characters and prefixed; stack traces never leave the process.

**Headers.** Verified on a running instance in tests: `content-security-policy`, `x-content-type-options`,
`x-frame-options`, `referrer-policy`, `cross-origin-opener-policy`, `permissions-policy`, HSTS on https.
Not set: `cross-origin-resource-policy` (low value for a same-origin API; R9 nicety).

## 4. Request validation inventory

| Mechanism | Where |
|---|---|
| pydantic models with `Field` bounds and `Literal` enums | every `body=` route; workflow definitions; file specs; connector payloads |
| Regex-constrained path/query values | invite tokens (hashed lookup), `return_to`, dataset ids, upload ids, document ids (`[A-Za-z0-9_]`), deep-link parameters |
| Identifier allow-lists | `db._check_identifier`, `store.table_name`, `COLUMN` keys `c\d+`, cost-centre `EDITABLE` fields |
| Streamed size limits | `web.read_body` on JSON and all public routes |
| Tool-argument schemas | `agent/tools.validate_args` |

## 5. Recommendations (into the roadmap)

1. Rate-limit `/mcp` per key (reuse the in-memory limiter) — R8.
2. Drop the session token from the login/signup JSON once no client needs it — R9.
3. Progressive delay on per-e-mail login failures instead of a hard 8/5 min — R9.
4. `cross-origin-resource-policy: same-origin` header — R9.
5. When a second engine process is ever introduced, move rate limiters to shared storage (document 07).
