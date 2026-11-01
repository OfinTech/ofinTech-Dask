# Final audit logging verification (OP-10)

## Scope

Every security-sensitive operation of the engine — API routes, AI agent tools, approvals, sessions, background
workflow runs — must leave an entry that an administrator can read and nobody can alter, with enough fields to
reconstruct who did what, where, under which authorization, with which outcome, and never with a secret in it.

## Architecture reviewed

One writer, `Engine.audit()` (`app.py`), used by every route (`Ctx.audit`), the router itself (refusals), the
agent harness, the approvals service, the workflow engine, the session code and the knowledge/files services. The
`audit_log` table (`db.py`), read by `GET /api/w/{ws}/audit` (admin). Outgoing webhooks receive the same
(sanitised) events.

## Changes implemented (`1f1a8de`)

- **Schema (migration 19)**: `event_id`, `request_id`, `resource_type`, `decision`, `approval_id`, `result` added
  to `audit_log`; index on `event_id`; **triggers** `audit_log_no_update` and `audit_log_no_delete` abort any
  UPDATE or DELETE (`RAISE(ABORT)`), so the engine's own connection cannot alter history; there is no route that
  tries.
- **`desk_engine/audit.py`**: `new_event_id()` (`ev_` + 96 random bits), `new_request_id()`, a `ContextVar`
  carrying the correlation id of the current API request (set by the router, returned as `x-request-id`) or
  agent/workflow run; `sanitize()` drops credential-shaped keys (`secret`, `token`, `password`, `api_key`,
  `authorization`, `cookie`, `private`, `session`, `bearer`, `signature`, …), strips query strings and fragments
  from URLs, masks bank identifiers, caps strings at 500 characters and collections at 40 items; `error_summary()`
  keeps the first line of an error only.
- **Events added**: `tool.run` for **every** tool call (read tools included) with `effect`, `min_role`, the
  decision (`allowed` / `approved` + approval id) and the outcome; `tool.denied` (role refusal; unknown or
  unoffered tool); `access.denied` for every 403 (route, needed role, reason) and for cross-tenant attempts
  (recorded **against the targeted tenant** with `not_a_member`); `user.login.failed`; `session.logout` (with the
  page's reason, `user` or `idle`); `session.expired` (`idle` or `lifetime`); the whole approval lifecycle
  (`approval.requested/approved/rejected/expired/replayed/changed/permission/missing/denied`); `member.role`,
  `member.remove` already existed and now also expire the member's approvals (audited `approval.expired`).
- **Access**: unchanged — admin or owner of the workspace; viewers and editors get 403 (itself audited).
- **Rendering**: the API returns JSON; the Audit and Oversight pages render fields as text (React escapes); a
  stored `<script>` is data, not markup (tested).
- **Retention and access controls (documented in `desk_engine/audit.py` and `docs/SECURITY.md`)**: entries live
  as long as the database; the engine never prunes them; they are inside every encrypted backup; reading needs the
  workspace's admin role; there is no API to modify or delete them; an operator with database access who wants
  to prune must do so outside the engine with the triggers dropped, which is itself visible in the schema.

### Required fields → columns

| Required | Column / source |
|---|---|
| Event ID | `event_id` (unpredictable, unique) |
| Timestamp (UTC) | `created_at` (epoch seconds, UTC) |
| Request / correlation ID | `request_id` (`req_…` for an API request, `run_…` for an agent run, `wr_…` for a workflow run) |
| User ID | `user_id` |
| Tenant ID | `workspace_id` |
| Tool / action | `action` (dotted name) and `target` (tool name, route, approval, member, document, …) |
| Resource type and id | `resource_type` (`tool`, `approval`, `route`, `workspace`, `session`, …) and `target` |
| Authorization decision | `decision` (`allowed`, `denied`, `needs_approval`, `approved`, `rejected`, `expired`, `replayed`) |
| Approval reference | `approval_id` |
| Execution result | `result` (`ok`, `error`, `refused`, `pending`, `signed_out`, …) |
| Sanitised error details | `detail.error` (first line, sanitised) |

## Test scenarios and actual results

`tests/test_approvals_and_audit.py::AuditLog.test_events_fields_and_secrets` (passing locally and in CI) drives,
on one Team Server: a successful approved write, a viewer's refused tool call and an unknown tool, a replayed
decision, an expired approval, a route denial (viewer → `POST /close`), two reads of the audit log refused
(viewer, editor), a cross-tenant request, a failed login and a logout; then asserts:

| Check | Result |
|---|---|
| Events present: `approval.requested`, `approval.approved`, `tool.run`, `tool.denied`, `approval.replayed`, `approval.expired`, `access.denied`, `user.login.failed`, `session.logout`, `user.signup`, `workspace.create` | PASS |
| Every row has a unique `ev_` id of 27 chars and a float UTC timestamp | PASS |
| Correlation ids: `req_` on API-driven rows, `run_` on tool rows | PASS |
| The viewer's `tool.denied` rows carry user, tenant, `resource_type=tool`, `decision=denied`, `result=refused` | PASS |
| The executed write carries user, tenant, `decision=approved`, the approval id and `result=ok` | PASS |
| The route denial names the route and the role needed; never the body | PASS |
| The cross-tenant attempt is recorded against the targeted tenant with `not_a_member` and the attacker's id | PASS |
| `session.logout` and `user.login.failed` attributed, with decision | PASS |
| No password, wrong password, API key, cookie name or `Bearer ` anywhere in the log | PASS |
| `sanitize`: `api_key` and `Authorization` dropped, URL query stripped, IBAN masked, 2,000-char text capped | PASS |
| UPDATE and DELETE on `audit_log` raise `IntegrityError`; the row is unchanged | PASS |
| Viewer and editor get 403 on the log; the admin reads it as JSON; a stored `<script>` comes back as data | PASS |

Also: `test_rbac.test_every_role_gated_route_refuses_every_lower_role` asserts one `access.denied` row per refused
route call (≥ 120); `test_session_inactivity` asserts `session.expired` with `reason=idle|lifetime` and
`session.logout` with `reason=user|idle`; `test_ai_financial_assurance` asserts `tool.run` rows only for what ran
and never for a blocked write; the live run reads event ids back through the admin API for denials, refusals,
approvals, role changes and workflow completion (`MULTI-TENANT-INTEGRATION-RESULTS.md`).

## Evidence references

Commit `1f1a8de`; migration 19 in `engine/desk_engine/db.py`; `engine/desk_engine/audit.py`; CI run 41284426822
and the final run in `FINAL-RELEASE-GATE.md`.

## Remaining risks

- The log is in the same SQLite database as the data it describes: an attacker with the database file and the
  ability to drop triggers can rewrite it. Shipping entries to an external, append-only store (syslog/SIEM via
  the outgoing webhooks, which already carry every event) is the recommended next step for regulated
  deployments; not implemented here.
- Retention is "forever, with the database": a legal retention schedule is the operator's to define.
- Volume: every tool call is now a row (≈ one per agent step); acceptable for team sizes, to be watched under
  load (AR-4).

## Final commit

`3d055c1`.
