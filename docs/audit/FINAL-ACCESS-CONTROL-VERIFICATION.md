# Final access-control verification (RBAC)

## Scope

Prove that authorization is enforced server-side on every path that can read or change protected data, using
the existing roles (`viewer < editor < admin < owner`, `web.ROLES`) and membership model, without trusting the
page, the model, an MCP server's hints or a tenant id supplied by the caller. Verified on `3d055c1`.

## Architecture reviewed, path by path

| Path | Where authorization happens | Verified by |
|---|---|---|
| REST API (192 routes with `{ws}`: 93 viewer, 55 editor, 43 admin, 1 owner; 20 account/session routes) | `web.Router`: `authenticate` → `load_workspace` (membership **join**, 404 for non-members, the role from the row) → `ctx.require(role)` — before the body is parsed or the handler runs | `test_rbac.test_every_role_gated_route_refuses_every_lower_role`: every editor/admin/owner route called as every lower role → 403, ≥ 120 calls, state fingerprint unchanged, one `access.denied` each |
| Streaming (`GET …/threads/{tid}/events`) | same route guard; private threads 404 for other users; `still_allowed()` re-checks session, membership and thread on every keep-alive | `OB11StreamReauthorisation`, `test_rbac.test_resource_ids…` (viewer → 404 on a private thread and its stream), `test_session_inactivity.test_ai_streaming…` |
| AI agent tools | `ToolContext.role` read from `members` at each call; `Tool.min_role` enforced when tools are offered **and** in `Tool.run`; write tools need an editor's approval; payments refused; sealed workspaces drop outbound tools | `test_ai_financial_assurance` (viewer, cross-tenant, injection), `test_rbac.test_viewer_cannot_modify…` |
| MCP integrations | servers managed by admins; tools `ask` by default; `auto` only with an admin-approved fingerprint of a read-only tool; `readOnlyHint` never sufficient; the MCP server exposing engine tools to clients runs as the key's user with that user's role | `OB10MCPToolReapproval`, `test_ai_financial_assurance.test_mcp_tools_ask_by_default…`, `test_mcp` |
| Background workers (workflow runs, document indexing, file review) | runs carry `workspace_id`; the creator must still be an active member **with at least the editor role** before every step and before an approved step (`_may_act`, tightened in `1f1a8de`); indexing/review run under the owning user's id | `test_rbac.test_background_job_stops…`, `test_isolation.test_mcp_key_background_jobs_and_audit`, live run step 9 |
| File uploads and downloads | editor to upload/import; viewer to read; object keys `documents/<ws>/<doc>`; every lookup by `workspace_id` | sweep; `test_isolation.test_database_rows_and_objects`; live run steps 3/4 |
| Financial reports and exports | viewer of **that** workspace; `export.csv` scoped; close/journal/pack generation editor | sweep; `test_isolation`; live run |
| Database operations | every store query carries `workspace_id`; dataset tables reachable only through scoped lookups; `fingerprint()` before/after in every denied case | `test_ai_financial_assurance`, `test_rbac`, live run |
| Document storage and retrieval | scoped `knowledge.get/search`; PipesHub per workspace | `test_isolation.test_retrieval_memory_and_conversations`, AI tests |
| AI memory and vector search | scoped `memories`; proposals only from the model | same |
| Administrative operations | admin: members, invites, connections, MCP servers, audit, oversight, file settings; owner: granting/removing owner, egress policy | `test_rbac.test_editor_cannot_do_admin_or_owner_actions` (admin cannot grant owner) |
| Electron IPC / desktop ↔ engine | `desktop/src/guards.ts` (sender origin, setup pages, external links, deep links, handoff codes); the engine's per-launch local token | `desktop/test/guards.test.mjs` (7) |

## Required controls — status

| Control | Status | Where |
|---|---|---|
| Authentication on protected operations | enforced | `authenticate` (401), sessions deleted on expiry |
| Role and resource-level authorization | enforced | route roles + scoped lookups + `min_role` |
| Tenant isolation | enforced | membership join; `workspace_id` everywhere; cross-tenant attempts audited |
| Permissions validated at execution time | enforced | role from `members` per call; approvals re-check at claim; workflows re-check per step |
| No reliance on frontend visibility | enforced | the rail hides pages, the server refuses regardless (sweep) |
| User-provided tenant ids not trusted | enforced | the id in the path is only ever joined against the caller's memberships |
| AI permission claims not trusted | enforced | nothing the model sends reaches a role or approval decision |
| MCP `readOnlyHint` not sole control | enforced | fingerprint + admin + policy |
| Deny by default | enforced | unknown tools → error; non-member → 404; missing role → 403 |
| Background jobs keep tenant context | enforced | run rows carry the tenant; creator re-check |
| Revoked permissions take effect on the next operation | enforced | `test_rbac.test_revocation_takes_effect…`; live run step 7 |

## Financial controls — a viewer cannot modify authoritative records

| Path | Result | Evidence |
|---|---|---|
| Direct API | 403 on close, imports, dataset rename/delete, dashboards, files, workflows, connection sync, document delete | `test_rbac.test_viewer_cannot_modify_financial_records_by_any_path`; live run step 6 |
| AI agent tools | editor tools (`sync_accounting`, `create_workbook`, `create_report`, `create_dashboard`, `open_file_for_editing`, `prepare_month_end_close`, `edit_file`) not offered and refused if named; reads allowed | same; `test_ai_financial_assurance` |
| MCP tools | a viewer cannot add servers (admin); any MCP write needs an editor's approval | sweep; assurance tests |
| Background jobs | a viewer cannot start a workflow (403); a creator demoted to viewer stops a run | `test_rbac.test_background_job_stops…` |
| File editing | `edit_file` is `write` **and** editor; `POST /files/{id}/edit` editor | sweep |
| Indirect workflows | a viewer's write request waits for an editor; the viewer cannot approve it (403) | live run step 6; `test_approvals_and_audit` |

Classification of `sync_accounting`, `create_workbook`, `create_report`, `prepare_month_end_close` by actual side
effect: all four create or replace **workspace records** (synced datasets, generated files, close runs with
proposals) without touching ledger rows, journal entries or decisions; they are therefore editor-only and run
without an approval for editors and above. This matches their API routes. See
`FINAL-AI-AGENT-SECURITY-ASSURANCE.md` §A for the full handler-by-handler table.

## Mandatory tests — results

| Test | Where | Result |
|---|---|---|
| Viewer attempting editor actions | `test_rbac` (sweep + specific), live run 6 | PASS |
| Editor attempting owner/admin actions | `test_rbac.test_editor_cannot_do_admin_or_owner_actions` | PASS |
| Another tenant's resources | `test_rbac.test_resource_ids…`, `test_isolation`, live run 3/4/5 | PASS |
| Unauthorized direct API requests | sweep (≥ 120 refusals) | PASS |
| Unauthorized AI tool calls | `test_ai_financial_assurance`, `test_rbac` | PASS |
| Unauthorized background job execution | `test_rbac.test_background_job_stops…` | PASS |
| Permission revocation during an active session | `test_rbac.test_revocation_takes_effect…`, live run 7 | PASS |
| Resource ID manipulation | `test_rbac.test_resource_ids…`, AI assurance (8 tools) | PASS |
| Cross-tenant document and memory retrieval | `test_isolation`, AI assurance, live run 5 | PASS |
| Authoritative data before/after every denied modification | `fingerprint()` in all of the above | unchanged in every case |

## Changes implemented in this phase

- `workflows/engine.py::_may_act` requires the editor role, not only membership (`1f1a8de`).
- Member removal/demotion expires the member's pending and approved requests (`1f1a8de`).
- Every 403 and every cross-tenant attempt is audited by the router (`1f1a8de`).
- `tests/test_rbac.py` (6 tests, `fe24400`).

## Remaining risks

- Owners and admins of a workspace are trusted in full (by design; AR-5 for the desktop).
- The sweep uses an empty JSON body; it proves the role gate fires before any handler, not each handler's own
  per-object checks, which the specific tests and the isolation suite cover for the data-bearing routes.
- Electron dispatch is tested through its decision functions, not end to end (OB-12 residual).

## Final commit

`3d055c1`.
