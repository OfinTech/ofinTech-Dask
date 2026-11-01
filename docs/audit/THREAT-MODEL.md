# Threat model and attack surface

STRIDE over the trust boundaries of `01-ARCHITECTURE-BASELINE.md` §7, with the attack surface listed by entry
point. Each threat names the control that answers it and the test that proves the control, or the finding that
tracks the gap. Findings (OB-nn) are in document 02; database items (DB-nn) in document 04.

## 1. Actors

| Actor | Access | Goal |
|---|---|---|
| A1 Anonymous on the internet | Team Server public routes, website forms | account takeover, data access, denial of service |
| A2 Member of a workspace (viewer/editor/admin) | authenticated API, agent | reach another workspace, escalate role, exfiltrate through the agent, poison shared context |
| A3 Author of content the system reads | documents, e-mails, webhook bodies, accounting records, MCP tool descriptions and results | make the agent act, exfiltrate, corrupt figures, run code on an analyst's computer |
| A4 Third-party service | LLM provider, accounting API, Google, MCP server, PipesHub, S3 operator | read what it is sent, return hostile data, substitute stored content |
| A5 Operator / host administrator | server, volumes, backups, environment | outside the model for data at rest except where encryption applies; relevant for backups and key handling |
| A6 Process on the desktop host | same OS account | outside the model (the desktop trusts its user); documented, not defended |

## 2. Attack surface by entry point

| Entry point | Auth | Limits | Notes |
|---|---|---|---|
| `POST /api/auth/{signup,login,forgot,reset}`, `/auth/form/*` | none | per-e-mail and per-IP rate limits (in memory), password policy, exact Origin for the website forms | first account becomes instance admin; sign-up otherwise by invitation |
| `GET /oauth/callback` | single-use `state` (15 min), PKCE | — | OB-01 fixed: provider hosts matched exactly |
| `GET /api/auth/desktop-handoff` | PKCE-bound one-time code, 120 s | — | sets the cookie for the desktop app |
| `POST /api/ingest/v1/accounting/{kind}` | `odk_` key (hashed) | 60/min per key, 100 MB (streamed, OB-07) | push connector; writes a dataset only |
| `POST /api/hooks/workflows/{wid}`, `/api/hooks/in/{hid}` | bearer hash or HMAC-SHA256, constant time | 256 KB streamed, 30–60/min | starts the linked workflow as its creator |
| `/hooks/telegram/{cid}`, `/hooks/whatsapp/{cid}` | secret header / HMAC with app secret | 1 MB streamed (OB-07) | messages routed to the agent as the linked member |
| `/mcp` | per-user access key (hashed) | 50 k chars per result; 120/min per key, 60/min wrong keys per address (OB-18 fixed) | read-only tools only |
| 198 `/api/*` routes | session cookie (+ exact Origin on writes) or desktop bearer | 2 MB JSON (streamed); uploads per route | roles declared per route |
| SSE `/api/w/{ws}/threads/{tid}/events` | as above, re-checked every 15 s (OB-11 fixed) | queue 10,000 events | — |
| Static UI | none | — | CSP `script-src 'self'`, no inline script |
| Electron IPC (7 channels) | sender-frame check on every channel (`desktop/src/guards.ts`, tested under Node) | — | OB-12 fixed |
| `ofintech-desk://` deep links | regex-filtered route and parameters | — | sets `location.hash` only |

## 3. STRIDE by boundary

### Boundary 0 → 1: internet to engine

| Threat | Control | Evidence |
|---|---|---|
| **S**poofing a user | scrypt passwords, hashed random session tokens, idle timeout, OIDC with verified e-mail and PKCE, single-use invitations/resets | `test_accounts`, `test_team_server` |
| Spoofing the website (CSRF) | `SameSite=Strict` cookie + whole-origin match | `OB02OriginCheck` |
| Spoofing a webhook sender | per-hook secret/HMAC, constant time | `test_webhooks`, `test_channels` |
| **T**ampering with requests | pydantic bodies, identifier checks, parameterised SQL | `test_data` (SQL injection), `test_audit_regressions.OB08` |
| **R**epudiation | `audit_log` for sign-in, approvals, write tools, connections, ingest, webhooks | append-only by code; forwarded to outgoing webhooks |
| **I**nformation disclosure | generic 500s, no access log, secrets never returned, `SAFE_CONFIG_KEYS` on connection listings | reviewed in 02 |
| **D**enial of service | body limits independent of headers (OB-07), parser limits (OB-06), rate limits on auth/hooks/`/mcp`, Caddy 260 MB cap; **residual:** in-memory limiters reset on restart, single process (AR-1) | — |
| **E**levation | roles declared per route, `instance_admin` checks inside the few non-workspace routes, approvals need editor | `test_team_server` |

### Boundary 1 ↔ 2: engine and untrusted content (the AI pipeline)

| Threat | Control | Evidence |
|---|---|---|
| Instructions in documents, e-mails, webhook bodies, MCP output make the agent **act** | every write tool pauses for a human with the exact arguments (now including imports from Gmail/Drive and edits of a person's file, OB-08); payments refused in code; sealed workspaces remove outbound tools; skills written by editors gated by admins (OB-09); an MCP tool that changed under its name asks again (OB-10) | `test_ai_security` cases 1–4, 9; `test_security_boundaries`; `OB08`, `OB10MCPToolReapproval` |
| Instructions make the agent **read** more than needed | reads are bounded to the member's own workspace; nothing leaves without a write tool | case 6 |
| Memory poisoning | model proposes, person approves; secrets refused | case 2, `test_memory` |
| Fabricated or mis-sourced figures | calculators and query compiler in code, figure check on answers, evidence-linked cells marked `unverified`, independent reviewer | cases 10–12, `test_files` |
| Leaking identifiers to the model/provider | IBAN and card masking on every model call (OB-19 fixed for engine ids) | case 14, `Masking` |
| Resource abuse through tools | `MAX_STEPS = 16`, per-tool timeouts, schema validation | case 13, `OB08` |
| **Residual:** untrusted text is not structurally delimited in prompts; a model may still be *confused* by it even though it cannot *act* without a person | documented; R8 part 2 | — |

### Boundary 1 → 3: engine to outside services

| Threat | Control | Evidence |
|---|---|---|
| SSRF via user URLs | https-only, no userinfo, private/CGNAT ranges refused at save and at **every request** (OB-04), sealed transport on sealed servers | `OB04`, `SealedServer` |
| Credential exposure to a look-alike host | exact provider host lists (OB-01); fixed hosts for Google/QuickBooks/Xero; keys only in headers | `OB01` |
| Hostile responses | pydantic validation of connector payloads, size caps, error bodies truncated | case 9 |
| Storage substitution | AES-GCM with the key as AAD; plaintext refused unless migrating (OB-05) | `test_storage` |
| Data sent to a cloud model | the customer's choice; sealed mode restricts to local models; masking | `SealedWorkspace` |

### Boundary 4: desktop host

| Threat | Control | Evidence |
|---|---|---|
| Web page reaching the local engine (DNS rebinding) | `Host` must be loopback; per-launch bearer token | `test_api_desktop` |
| Renderer escaping the sandbox | `contextIsolation`, `sandbox`, no `nodeIntegration`, no webview, navigation pinned, permissions denied | `main.ts` review |
| Hostile deep link | regex on route and parameters; only `location.hash` is set | `main.ts` review |
| Master key at rest | OS `safeStorage`; Linux without a keyring warns and falls back to Electron's key (documented) | `main.ts` |
| Another process of the same user | **not defended** (environment of the child process, decryptable key file): the desktop trusts its user | documented |

## 4. Abuse cases worth rehearsing

1. *Compromised accounting system.* Returns a journal whose descriptions carry formulas and instructions. Outcome
   today: text stays text in every export (OB-03); instructions reach the model as data; any send waits for a
   person.
2. *Malicious workspace admin.* Registers an MCP server at an internal name and a webhook at the metadata
   address. Outcome: refused at save and at connection (OB-04); on a sealed server refused by the transport.
3. *Stolen backup.* Nothing is readable: backups are sealed under a backup key kept apart from the files and from
   the master key; a tampered file does not open (`test_backup`).
4. *Removed employee with an open tab, a running workflow and an MCP key.* The stream closes within 15 s; the
   session is deleted on the next request (OB-11); the workflow run stops before its next step; the MCP key stops
   resolving (`test_isolation`).
5. *Editor who writes a "skill".* It stays off until an admin reads and enables it (OB-09).
6. *Bucket operator swaps a stored workbook.* Refused: the object does not authenticate (OB-05).

## 5. Open items from this model

Structural delimiting of untrusted text (AR-2), the single-process limit (AR-1), live-integration validation
(AR-3) and the other accepted risks are in `OPEN-RISKS.md` with owners and review dates. A further abuse case
added by the gate: *a colleague's actions seen by a viewer* — the oversight desk that landed on 1 Nov reads the
audit log and is admin-only (NEW-01).
