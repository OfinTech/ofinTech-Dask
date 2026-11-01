# Embedded Agent Gateway + AG-UI adapter — architecture

Status: MVP design and implementation on branch `feature/embedded-agent-gateway`.
Baseline audited: `origin/main` at commit `eada27077f436685f1df4878b5ec2165d2669c93` (2026-11-01, "Enterprise
gate reports: OP-12 browser verification, session security policy, owner risk acceptance register, release gate").
Engine version at baseline: 1.1.0; last database migration at baseline: 20.

This document records what was found in the repository before any code was written, the design that follows from
it, and the parts of the existing security architecture that the gateway reuses unchanged.

## 1. Repository audit (findings, not assumptions)

### 1.1 Engine and HTTP layer

| Area | Finding (file) |
|---|---|
| Framework | Starlette ASGI app with a single SQLite database (WAL, foreign keys on) — `engine/desk_engine/app.py`, `db.py`. One engine process per deployment (accepted risk AR-1 in `docs/audit/OPEN-RISKS.md`). |
| Routing | `web.Router.route(method, path, *, auth, role, body, fresh)` wraps every handler: generates a request id (`audit.current_request_id`), authenticates, loads the workspace and role when the path has `{ws}`, enforces step-up re-authentication (`fresh`), parses a Pydantic body (2 MB cap), maps `AppError` to JSON and audits every 403 (`_audit_denied`). |
| Authentication | `web.authenticate()`: desktop mode compares a bearer with `settings.local_token`; server mode accepts a bearer or the `desk_session` cookie, looks up `sessions` by `token_hash(token)` (SHA-256), enforces idle and absolute lifetimes, and runs the CSRF origin check for cookie writes. |
| Authorization | `web.load_workspace(ctx, ws_id, role)` joins `workspaces` and `members`; a non-member gets 404 plus an `access.denied` audit entry. Roles are `viewer < editor < admin < owner` (`web.ROLES`, `role_at_least`). |
| Machine credentials already in the code base | `mcp_keys` (`routes/mcp.py`, `mcp_server.py`): a per-user, per-workspace key `odk_…` stored as a SHA-256 hash, shown once, resolved to a `ToolContext` with `source="mcp"`; per-key and per-IP rate limits in `mcp_server.RateLimited`; refused on sealed workspaces (`_not_sealed`). `ingest_keys` (webhook ingestion) follow the same hash-only pattern. |
| Security headers | `app.SecurityHeaders`: CSP `default-src 'self'`, `frame-ancestors 'none'`, `X-Frame-Options: DENY`, HSTS when the public URL is https. The Desk UI itself is therefore not embeddable in an iframe — by design; the embedded SDK must talk to an API, not frame the UI. |
| Secrets | `security.SecretStore` (AES-256-GCM with the master key, AAD bound to purpose and workspace); `token_hash`, `new_token` (`secrets.token_urlsafe`). |
| Audit log | `Engine.audit()` → `audit_log` (append-only: migration 19 adds `BEFORE UPDATE/DELETE` triggers that abort). `audit.sanitize()` drops secret-like keys, strips URL queries, masks bank identifiers, caps strings at 500 characters. Entries carry `event_id`, `request_id`, `resource_type`, `decision`, `approval_id`, `result`. |
| Usage metering | `Engine.record_usage(ws_id, provider_id, model, usage, source)` → `usage` table (tokens per model call). No per-integration dimension exists at baseline. |
| Egress | `egress.py`: `workspace_sealed()`, `tool_sends_out()`, `is_payment_tool()` (payment tools cannot be registered at all), `SealedTransport`, `PrivateNetworkGuard`, `mask_bank_identifiers()`. |

### 1.2 Agent runtime

| Area | Finding (file) |
|---|---|
| Entry point | `agent.harness.Agent.start_turn(workspace, user_id, thread_id, text, *, parent, source, attachments) -> run_id`. Refuses with `Conflict(thread_busy)` while a run on the thread is running or waiting for approval. Inserts the user `messages` row and an `agent_runs` row (`status`, `state` JSON with `step`, `pending`, `final_text`, `source`, `extra_instructions`). |
| Loop | `Agent._run`: builds `ToolContext(engine, workspace, user_id, thread_id, run_id, source)`, takes `tools = engine.tools.for_context(ctx)` (availability, `min_role` from the `members` table, payment-tool refusal, sealed-workspace filtering), selects tool groups, streams the model, executes `read` tools directly and turns `write` tools (or MCP `ask` policy) into approvals; `MAX_STEPS = 16`. |
| Events | Published on `engine.events` (channel = thread id) — `{"type": "message"}`, `assistant_start` (`message_id`, `model`), `delta` (`message_id`, `text`), `tool_start` (`call.id/name/arguments`), `approval`, `run` (`status`, `final_text`, `usage`). `EventHub` keeps a replay buffer per channel (5 000 events), bounded subscriber queues (10 000), and drops slow consumers. `routes/threads.py` serves these as SSE and re-authenticates on every event (`still_allowed()`). |
| Approvals | `approvals.Approvals.request/decide/claim/finish`: expiry (4 h outbound / 24 h internal), SHA-256 digest of workspace + requester + tool + canonical arguments, atomic `approved → executing` claim, failure codes `expired/replayed/changed/permission/missing`; a decision needs the editor role (`_requester_may`, `routes/approvals.py` with `require_fresh`). `Agent.decide()` resumes the run once nothing is pending. |
| Tool output | `ToolOutput(text, blocks, data)`; `blocks` are typed (`kpis`, `table`, `link`, `sources`, `text`) and rendered by the Desk UI's `components/blocks.tsx`; the model's text is never rendered as HTML (`agent/tools.py` module docstring, `docs/SECURITY.md`). `for_model()` masks bank identifiers. |
| Tool inventory | 53 static tools at baseline; 41 are `effect=read`, 12 are `write`. Read tools that still leave the network: the Google tools (`gmail_*`, `drive_*`) — `tool_sends_out()` is true for them. Read tools that need editor: `prepare_month_end_close`, `sync_accounting`, `create_dashboard`, `create_workbook`, `create_report`, `open_file_for_editing`. |
| Gap | There is **no way to restrict one run to a subset of tools**. `for_context()` filters by role, sealing and availability only; a viewer in a workspace with Google connected is offered `gmail_search`. The gateway needs a per-run tool scope. |

### 1.3 Existing tests the gateway must not weaken

`engine/tests`: `test_rbac`, `test_isolation`, `test_approvals_and_audit`, `test_security_boundaries`,
`test_session_*`, `test_tokens`, `test_team_server`, `test_ai_security`, `test_financial_regression` with
`golden_financial.py` fixtures (never regenerated), `test_mcp` (the closest analogue to this feature). Test harness:
`tests/helpers.py` (`Harness`, `FakeLLM` scripted OpenAI-compatible endpoint, `Router` mock transport). Run with
`python -m pytest engine/tests`.

### 1.4 UI

`ui/` is React 19 built with esbuild; `components/blocks.tsx` renders the typed blocks; i18n in `ui/src/i18n`
(English and Arabic, RTL). The embedded SDK is a separate package (`sdk/embedded-agent`) and does not import from
`ui/`, so the Desk UI build is untouched.

## 2. Design

### 2.1 Trust model

```
 Partner backend ──(integration key, server-to-server)──▶ POST /api/embedded/sessions ──▶ short-lived session token
        │                                                                                   │
        ▼                                                                                   ▼
 Partner web app ──(session token in Authorization header)──▶ POST /api/embedded/agents/{agent_id}/run ──▶ AG-UI SSE
```

* **Integration** — registered by a workspace **admin** inside OfinTech Desk (`/api/w/{ws}/embedded/integrations`,
  step-up re-authentication required, refused on sealed workspaces exactly like MCP keys). Registration creates a
  dedicated **service user** (`users` row, no password, `is_local = 0`) that is a **viewer** member of that one
  workspace. The integration key (`oei_…`, 256-bit, SHA-256 hash stored, shown once) is for the partner's backend
  only.
* **Tenant binding is server-side.** The workspace comes from the integration row; the acting Desk identity is the
  service user; the role comes from the `members` table (read by `ToolContext.role` at every tool call). Nothing
  in the request body — `threadId`, `forwardedProps`, `context`, `state`, custom headers — can select a workspace,
  a user or a role. A client-supplied `tenant`/`workspace`/`role` field is ignored and audited once per run as
  `embedded.input.ignored_fields`.
* **Session token** (`oes_…`) — issued to the partner backend for one end user (`subject`, an opaque string the
  partner chooses; only its SHA-256 is stored). Default lifetime 15 minutes (1–60 allowed), hash stored in
  `embedded_sessions`, revocable, bound to the integration. The browser never sees the integration key.
* **Origin allow-list** — every browser request must carry an `Origin` that is on the integration's list (exact
  scheme + host [+ port]); CORS headers are emitted only for that origin. Server-to-server calls carry no `Origin`
  and are accepted for the session-issuing endpoint only.
* **Deny by default** — an unknown token, an unknown agent id, a revoked session, a suspended integration, a
  missing capability, an origin not on the list, a sealed workspace, a disabled service user → 401/403 with an
  `embedded.denied` audit entry; no partial run is started.

### 2.2 Components (new code, all under `engine/desk_engine/embedded/`)

| Module | Responsibility |
|---|---|
| `model.py` | Table access for `embedded_integrations`, `embedded_sessions`, `embedded_threads`, `embedded_usage`; key and token issuance (`oei_`/`oes_` prefixes), hashing, expiry. |
| `auth.py` | `resolve_session(engine, request) -> EmbeddedPrincipal` (integration + session + workspace + service user), origin check, CORS headers, token-bucket rate limiting per integration and per session, `embedded.denied` auditing. |
| `agents.py` | The agent catalogue: `finance-copilot` (Ask Your Finance + Financial Intelligence + read-only reconciliation queries). Each agent maps to a **tool scope** — the read-only, non-egress tool set — and to the capabilities an integration must hold. |
| `scope.py` | `ToolScope` (allowed categories, `effect == "read"`, `tool_sends_out() is False`, explicit deny list) and `apply(tools, scope)`; the harness applies it when `state["tool_scope"]` is present. |
| `agui.py` | The AG-UI protocol adapter: parses `RunAgentInput`, translates Desk events into AG-UI events, encodes SSE, maps `waiting_approval` to `RUN_FINISHED` with `outcome.type = "interrupt"`, maps `resume` items to the approvals engine. |
| `gateway.py` | Orchestration of one run: entitlement check, thread lookup/creation, `Agent.start_turn(..., source="embedded")`, event subscription with bounded buffering, cancellation on disconnect, usage record. |
| `metering.py` | `embedded_usage` rows per run; `Entitlements` interface with the default `LimitEntitlements` (reads the integration's `limits` JSON: runs per minute, runs per day, input characters, tokens per day). No billing. |
| `routes.py` | Public endpoints (`/api/embedded/...`, own authentication) and admin endpoints (`/api/w/{ws}/embedded/...`, standard `Router`). |
| `components.py` | The generative-UI component registry: versioned JSON schemas for the ten components, validation of tool blocks into component payloads, ar/en labels. |

Changes to existing files are limited to: `db.py` (migration 21), `agent/harness.py` (apply `state["tool_scope"]`
and accept `source="embedded"`), `routes/__init__.py` (mount the new routers), `app.py` (construct
`engine.embedded`), docs and OpenAPI.

### 2.3 Request flow for `POST /api/embedded/agents/{agent_id}/run`

1. `resolve_session` → principal, or 401/403 + audit. Rate limits (integration, session) → 429.
2. Parse `RunAgentInput` (Pydantic, 256 KB cap, ≤ 64 messages, last user message ≤ 8 000 characters). Unknown
   `agent_id` → 404. The agent's required capability not granted to the integration → 403.
3. `Entitlements.check(principal, agent)` → 403 `entitlement_exceeded` when a daily/monthly limit is reached.
4. Map `(integration, subject_hash, client threadId)` → Desk thread (`embedded_threads`); create it on first use
   with `visibility = 'private'`, `created_by = service user`, `channel = 'embedded:<integration id>'`.
5. `Agent.start_turn(workspace, service_user_id, thread_id, text, source="embedded")` with
   `state.tool_scope = agent.scope` and `state.extra_instructions = agent.instructions` (plain-text financial-only
   guardrail; the private model configured for the workspace is used — there is no fallback to any external
   provider that the workspace has not configured, and the gateway never carries provider credentials).
6. Subscribe to the thread channel **before** the run starts (the channel replay buffer covers the gap), stream
   AG-UI events, stop at the terminal `run` event. On client disconnect the run is cancelled the same way
   `POST /threads/{tid}/stop` does.
7. Write one `embedded_usage` row (status, tokens, tool calls, duration) and audit `embedded.run.finish`.

### 2.4 Data model (migration 21)

```
embedded_integrations(id, workspace_id, name, status, service_user_id, key_hash UNIQUE, key_prefix,
                      allowed_origins JSON, capabilities JSON, limits JSON, created_by, created_at,
                      last_used_at, rotated_at, revoked_at)
embedded_sessions(id, integration_id, token_hash UNIQUE, subject_hash, subject_label, locale,
                  created_at, expires_at, revoked_at, last_used_at)
embedded_threads(integration_id, subject_hash, client_thread_id, thread_id, created_at,
                 PRIMARY KEY (integration_id, subject_hash, client_thread_id))
embedded_usage(id, integration_id, session_id, run_id, subject_hash, agent_id, status,
               input_tokens, output_tokens, tool_calls, duration_ms, created_at)
```

No raw subject identifiers, no raw tokens, no end-user personal data beyond an optional 80-character display
label the partner chooses to pass.

### 2.5 Generative UI

The model never emits UI. Tools return typed `blocks`; the adapter validates each block against the component
registry and emits it as an AG-UI `CUSTOM` event named `ofintech.component` with `{version: 1, component, props}`.
The SDK renders only components that exist in its own registry; unknown components fall back to a plain text
card. No HTML, no script, no URLs other than Desk-owned `FinancialSourceReference` links, which the SDK renders
as text unless the host app provides an `onOpenSource` handler.

### 2.6 What this MVP does not do (stated plainly)

* No write operations through the gateway: the tool scope offers read tools only, so no approval is ever
  requested by an embedded run. The interrupt mapping is implemented and tested so that a future write-capable
  agent has a protocol path, but **approvals are decided inside OfinTech Desk by an editor**, never from the
  partner app.
* No client-defined tools (`RunAgentInput.tools` is accepted and ignored; the model never sees them).
* No billing; the entitlement interface is a hook.
* No claim of production readiness, PDPL/SAMA/ZATCA compliance or multi-replica operation. See
  `SECURITY-THREAT-MODEL.md` §residual risks and `IMPLEMENTATION-PLAN.md` §verification for what is and is not
  proven by tests.
