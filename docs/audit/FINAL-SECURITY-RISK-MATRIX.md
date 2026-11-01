# Final security risk matrix

Independent re-verification on `main` at `097f9aa`, 1 November 2026. Every row was checked against the source
at that commit and the executed tests (CI run 41277460158 for the code before the one new fix; local and CI runs
for `097f9aa` as recorded in `FINAL-CI-VERIFICATION.md`). Finding ids from the earlier audit documents are kept
(OB, DB, WH, WF, NEW, AR, OP); the ids FIN-, SC- and AI- are new to this verification.

Statuses: OPEN · IN PROGRESS · FIXED — NOT VERIFIED · SECURITY VERIFIED · FUNCTIONALLY VERIFIED · REGRESSION
VERIFIED · CLOSED (all three kinds of evidence executed) · ACCEPTED RISK.

**On approvals.** The accepted risks below carry an owner (the repository owner) and a review date proposed by
the audit. No written sign-off from the owner exists in the repository or in the project record; until the
owner records one, every ACCEPTED RISK row is "accepted by the audit on the owner's behalf, approval pending".
This report does not invent that approval.

## 1. Matrix

Severity is the worst plausible outcome before mitigation; exploitability is after the controls now in place.

| Risk ID | Severity | Exploitability | Business impact | Mitigation | Test evidence | Residual risk | Status |
|---|---|---|---|---|---|---|---|
| **FIN-01** Viewer's assistant could create dashboards, generated files, editable copies, close runs and refresh accounting datasets without any approval (tools labelled `read` whose routes need `editor`) | Medium (integrity of workspace records; a viewer could prepare a close run and refresh synced ledgers) | was: any viewer with chat; now: none found | spurious artefacts; a refreshed synced dataset (idempotent, from the authoritative system); no ledger row or decision changed | `Tool.min_role` read from `members` at each call; enforced in `ToolRegistry.for_context` and `Tool.run` (`agent/tools.py`); seven tools marked editor | `test_ai_financial_assurance.Tenants.test_viewer_cannot_create_workspace_records_through_read_tools` (negative, fingerprint of 6 tables unchanged), `…test_viewer_requested_write_needs_an_editor…` (positive: viewer's write request waits for an editor), full suite 277 OK | an owner-role user is still trusted for everything (by design) | **CLOSED** (`097f9aa`) |
| **OB-01** OAuth callback trusted prefix-matched Zoho host | High (token + client secret to attacker) | needs a live unused OAuth state; none | credential theft | exact host lists, `connectors/flows.py` | `OB01ZohoCallback` ×2; `test_more.ZohoFlow` | none known | CLOSED |
| **OB-02** CSRF origin by prefix | Medium | none (whole-origin equality + SameSite=Strict) | forged state-changing requests | `web.check_origin` (`web.py:276`) | `OB02OriginCheck`; `test_adversarial.AuthenticationAndSessions.test_cookie_writes_need_the_origin…` | none known | CLOSED |
| **OB-03** Formula injection in exports | High (code execution in a reviewer's spreadsheet client) | none found incl. invisible prefixes | financial-report integrity, client compromise | `files/cells.py` (`csv_safe`, `looks_like_formula`), used by every writer | `OB03FormulaInjection` (7 prefixes × CSV/XLSX), `test_adversarial.FilesAndTraversal.test_invisible_characters…`, oracle `test_csv_export_keeps_numbers_and_neutralises_only_text` | clients ignoring the `'` convention show an apostrophe | CLOSED |
| **OB-04** Outbound policy only at save time (rebinding, CGNAT, redirects) | High (SSRF to metadata/internal services) | requires DNS control and a second lookup within 60 s | internal-service exposure | `egress.PrivateNetworkGuard` (`egress.py:177`) at every connection; CGNAT refused | `OB04ConnectionTimeNetworkPolicy` (12), `test_adversarial.NetworkPolicyEvasion` (3 methods, 28 notations, mocked DNS, redirect) | two lookups (guard + socket) — IP pinning planned | CLOSED |
| **OB-05** Encrypted storage returned unauthenticated plaintext | High (store tampering) | none (refused unless operator flag) | serving forged documents | `storage.EncryptedStorage.get` (`storage.py:338`) refuses objects without the header; `storage-encrypt` migrates | `test_storage.test_files_written_before_encryption_need_an_explicit_migration` (negative + positive + idempotent rerun); pre-audit data dir opens; backup round trip | operators must run the migration once (documented) | CLOSED (behaviour change, re-validated: strict default confirmed in code) |
| **OB-06** Parser resource exhaustion | Medium (DoS) | bounded | availability | `files/limits.py` (`check_office_zip`, `open_image`), OCR budget, Caddy cap | `OB06ParserLimits` (6 readers, EPS, 100/400 MP headers) | pypdf per-page time unbounded (tool timeout bounds the agent path) | CLOSED |
| **OB-07** Body limits trusted Content-Length | Medium (DoS) | bounded | availability | `web.read_body` (`web.py:163`) | `OB07BodyLimits` | multipart spools to disk before its check (Caddy caps) | CLOSED |
| **OB-08** Tool args unchecked, no timeouts, effects by label | Medium | none found | unauthorised writes via mislabelled tools | `validate_args` (`agent/tools.py:80`), per-tool `timeout`; Gmail/Drive imports and `edit_file` are `write`; **re-validated here by reading every handler (AI report §A)** | `OB08AgentIsolation`, `test_user_files` (edit waits), `test_ai_financial_assurance` (every listed tool exercised) | `sync_accounting`, `create_workbook`, `create_report`, `create_dashboard`, `prepare_month_end_close` remain unattended for editors+ (they write derived records, never authoritative ones) — see AI report §E | CLOSED |
| **OB-09** Prompt-injection surface; editor skills enabled at once | Medium | containment proven; confusion possible | misleading answer; never an unapproved action | skills gated by admin; approvals; sealed workspaces; memory proposals | `OB09SkillsNeedAnAdmin`; `test_ai_security` 13; `test_ai_financial_assurance` injection ×2 (PDF, CSV, MCP result, e-mail, ERP response) | untrusted text not structurally delimited (AR-2) | gate part CLOSED; remainder ACCEPTED RISK (AR-2) |
| **OB-10** MCP: raw URL audited; `auto` trusted the server once set | Medium | none found | unattended execution of a changed tool | `tool_fingerprint` / `tool_may_run_unattended` (`mcp_client.py:48,59`), `approved_fingerprint` stamped by PATCH | `OB10MCPAuditURL`, `OB10MCPToolReapproval`; `test_ai_financial_assurance.Tenants.test_mcp_tools_ask_by_default…` (readOnlyHint alone ≠ unattended; hidden side effect waits; destructive never unattended) | an admin who approves a hostile read-only tool is trusted (design) | CLOSED (re-validated) |
| **OB-11** Streams never re-authorised | Medium | ≤ 15 s window | ex-member sees events | `still_allowed` (`routes/threads.py:170`) | `OB11StreamReauthorisation` ×2 | keep-alive delay | CLOSED |
| **OB-12** Electron IPC sender checks | Low→Medium | needs a foreign frame in the app window | local actions from a hostile page | `desktop/src/guards.ts` (`trustedSender`, `isAppUrl`, `isOpenableExternally`, `isAcceptableServerUrl`, `routeFromDeepLink`, `handoffCode`) | `desktop/test/guards.test.mjs` 7/7 in CI | decision functions tested, not Electron's dispatch (manual check per OS advisable) | CLOSED |
| **OB-13/14/15** Telegram token in errors; SMTP in clear; formula parser crashes | Low | none | leakage / crash | typed errors; SMTP modes; parser bounds | `OB13`, `OB14`, `OB15`, golden expressions | — | CLOSED |
| **OB-16 / AR-6** Viewers' chat attachments become workspace documents | Low | by design | viewer adds to the knowledge base | collection shown; admins delete | — | accepted | ACCEPTED RISK (approval pending) |
| **OB-17** No CI gates; no lockfiles | Medium (supply chain) | — | undetected vulnerable dependency or secret | `security` job, Trivy, lockfiles + `npm ci` | CI run 41277460158 (gitleaks, ruff S, pip-audit, npm audit, Trivy all green) | tag-pinned scanners (AR-7); dev tree outside the gate (SC-01) | CLOSED |
| **OB-18** `/mcp` unrated; container privileges | Low | — | DoS; container escape surface | limiter; `cap_drop`, `no-new-privileges`, tmpfs | `OB18MCPEndpointRateLimit` | — | CLOSED |
| **OB-19** Masker ate engine ids | Low | — | broken links in answers | lookbehind fix | `OB19` | — | CLOSED |
| **DB-01** Plaintext backups | High (every ledger and conversation in one file) | theft of the file yields nothing without the backup key | confidentiality | `backup.py` (`seal`/`open_sealed`, AES-256-GCM, separate key, `backup-check`) | `test_backup` ×5 (theft, tampering ×3 + truncation + foreign file, overwrite refusal, round trip with sums, CLI) | key management is the operator's | CLOSED (re-validated: key separation and restore verification confirmed) |
| **DB-02/03** Loop stalls; WAL growth | Low | — | availability | off-loop dataset work; hourly checkpoint | benchmark (200k rows 2.7 s), golden | — | CLOSED |
| **DB-06 / OP-3** Retention / `secure_delete` on the desktop database | Low | needs the laptop | deleted rows recoverable from free pages | — | — | open | OPEN (OP-3) |
| **WH-01** Credentials under unlisted keys / URL queries in webhook payloads | Medium | none found | token leakage to the model and storage | `webhooks._clean` (`webhooks.py:65`) | `test_adversarial.WebhookLeakage` ×2, `test_webhooks` | key-fragment list is a heuristic | CLOSED |
| **WF-01** Workflow runs acted with a departed creator's rights | Medium | none found | background job with stale authority | `_may_act` (`workflows/engine.py:259`) before each advance and approved step | `test_isolation.test_mcp_key_background_jobs_and_audit` | a run already inside a tool call finishes it | CLOSED |
| **NEW-01/02** Oversight desk visible to viewers; two unscoped lookups | Low | none found | colleagues' activity to viewers | admin-only route and rail; scoped queries | `test_adversarial.test_role_escalation_attempts` (viewer 403), `test_overview` 10 | — | CLOSED |
| **AI-01** Cross-tenant reads through agent tools with manipulated ids (datasets, documents, search, memory) | High if present | none found | tenant data disclosure | every store query carries `workspace_id`; `find_dataset`, `knowledge.get`, `memory.list` scoped | `test_ai_financial_assurance.Tenants.test_tenant_b_agent_cannot_reach_tenant_a_data_with_manipulated_ids` (8 tools, no Alpha value in any result, Alpha fingerprint unchanged), `test_isolation.TwoTenants` ×3 | none known | SECURITY VERIFIED → CLOSED (positive: Beta's own data returned; regression: suite) |
| **AI-02** Financial writes through the agent (ledger rows, journal entries, invoices, payments, reconciliation, close decisions) | Critical if present | no tool exists; unknown tools answered with an error; payments refused at registration and at call | authoritative data integrity | no write tool over ledgers/closes; `is_payment_tool` (`egress.py:57`) at registry and call; close `prepare` keeps decisions (`close/service.py:_merge`) and refuses closed months; approvals bind frozen arguments | `…test_no_tool_can_change_ledger_rows_or_posted_decisions` (fingerprint of ledger rows and sums unchanged; rejected close item stays rejected), `…test_approved_write_runs_with_frozen_arguments…`, `test_security_boundaries` (payments) | — | CLOSED |
| **AI-03** Approvals: replay, viewer decision, stale approvals | Medium | second decision refused; viewer 403 | unauthorised action | `UPDATE … WHERE status='pending'`; `role_at_least(role,'editor')` in `Agent.decide`; `pending` cleared after resolution | `…test_approved_write…` (viewer 403, second decision 400/409), `test_isolation.ApprovalRace` | **pending approvals never expire** (arguments are frozen, so a late approval runs exactly what was shown) — AI-05 | CLOSED for replay/role; expiry OPEN (AI-05) |
| **AI-04** Injection via PDF, CSV/XLSX, e-mail, MCP tool result, accounting API response, user text, error pages | High if it led to action | none led to an action in 20 scripted attempts | exfiltration, unauthorised writes | server-side approvals independent of the model; unknown/unoffered tools refused; sealed workspaces; the 200-char cap on connector account names | `test_ai_financial_assurance` injection ×2, `test_ai_security` cases 1–4, 9 | a confused answer (AR-2) | SECURITY VERIFIED → CLOSED |
| **AI-05** No expiry on pending approvals | Low | needs an editor to approve later | an action approved long after it was proposed | arguments frozen and shown; thread stop expires them | `routes/threads.py:stop` sets `expired` | open | OPEN (proposed: expire after 7 days; recorded as OP-9) |
| **AI-06** Read-tool executions are not audited (only write/policy tools) | Low | — | weaker forensic trail for reads | thread messages persist every call and result | — | open | OPEN (proposed: audit `tool.run` for editor-gated read tools too; OP-10) |
| **SC-01** Dependabot alert 147: `minimatch` < 11.0.1 in the desktop development tree | Moderate (per advisory); not shipped | needs a hostile glob pattern at build time | CI/build-machine only | outside the app; `--omit=dev` gate | — | open | OPEN (OP-7) |
| **SC-02** Python dependencies pinned by version, not hash | Low | registry compromise | supply chain | `requirements.txt` exact pins + pip-audit | CI | open | OPEN (proposed `pip-compile --generate-hashes`; OP-11) |
| **AR-1** Single-process engine | Low | — | availability only | restart policy | — | accepted | ACCEPTED RISK (approval pending) |
| **AR-2** Untrusted text not structurally delimited | Medium | confusion possible, action impossible | misleading answer | approvals, figure check | 26 injection cases | accepted to 2026-12-15 | ACCEPTED RISK (approval pending) |
| **AR-3** Live integrations validated against mocks only | Medium | — | first-use failure | documented contracts | mocks | accepted per go-live | ACCEPTED RISK (approval pending) |
| **AR-4** No load measurement / metrics | Low | — | slowdowns | single-user figures | benchmark | accepted to 2026-12-15 | ACCEPTED RISK (approval pending) |
| **AR-5** Desktop host trusts its user | Low | needs the laptop | local data | OS keychain | — | by design | ACCEPTED RISK (approval pending) |
| **AR-7** Scanner images and actions by tag | Low | upstream tag compromise | CI behaviour | read-only token | — | to 2026-12-15 | ACCEPTED RISK (approval pending) |
| **AR-8** Cheap login lockout | Low | trivial | nuisance | per-IP limit, Google sign-in | `test_accounts` | to 2027-02-01 | ACCEPTED RISK (approval pending) |
| **AR-9** Session token also in login JSON | Low | needs a third-party client | — | cookie path only in the UI | — | to 2027-02-01 | ACCEPTED RISK (approval pending) |
| **AR-10** Compliance assessed, not certified | — | — | regulated customers need their own assessment | gap assessment | — | per customer | ACCEPTED RISK (approval pending) |
| **OP-8** Engine test-suite stall | — (CI only) | — | CI time | `wait_idle` rewritten; `settle()` bound; faulthandler abort | runs since `4068d0d` complete in ~40 s | close after ten clean runs | IN PROGRESS |

## 2. Re-validation notes (the items the brief singled out)

- **OB-05 and backward compatibility.** `EncryptedStorage.get` refuses an object without the `MAGIC` header
  unless `allow_plaintext` is set; `storage-encrypt` rewrites once and is idempotent (second run rewrites 0). A
  pre-audit data directory opens unchanged (`user_version` 18, no migration added by any audit commit). Encrypted
  objects written before the audit open identically. Verified by reading the code and by `test_storage`.
- **OB-08 classification by actual side effects.** Every handler in `desk_engine/agent/*_tools.py` was read
  (inventory in `FINAL-AI-AGENT-SECURITY-ASSURANCE.md` §A). Four tools labelled `read` create or replace
  workspace records: `create_dashboard` (dashboards), `create_workbook`/`create_report` (files + versions),
  `open_file_for_editing` (a file copy; the original document untouched), `prepare_month_end_close` (close run +
  proposed items; decisions preserved), `sync_accounting` (replaces the synced datasets from the accounting
  system; never writes to it). None touches ledger rows, journal entries, invoices, payments or close decisions.
  They are now editor-only (FIN-01). The Google imports and `edit_file` are `write` as the earlier audit decided.
- **OB-10 fingerprint re-approval.** `tool_fingerprint` hashes name, description, input schema and the read-only
  flag; `tools_for` downgrades `auto` to `ask` unless `approved_fingerprint` matches; the PATCH route stamps the
  fingerprint only for tools set to `auto`. A tool whose annotation claims read-only but writes (fake server
  variant 3, `account_balance`) waits for an approval by default, and runs only after an editor approves it.
- **Encrypted backup and recovery.** `backup.py`: `DSKBK1` magic, 12-byte nonce, AES-256-GCM under a key that is
  not the master key (`decode_key`/`read_key_file`); `restore_backup` opens, verifies with `PRAGMA integrity_check`
  on a temp copy, and only then moves into place, refusing to overwrite unless asked and dropping stale `-wal`/
  `-shm`. Five tests incl. the CLI round trip.
- **Dependabot 147.** Read from the Dependabot job definition in run 41274498358: advisories for `minimatch`,
  the one without a reachable fix affecting `< 11.0.1`; present at 3.1.5 / 5.1.9 / 9.0.9 under electron-builder's
  tree, `dev: true` throughout `desktop/package-lock.json`. Not in the installed application.
- **Cross-tenant isolation.** Route layer (`load_workspace` membership join → 404 for foreign workspaces),
  store layer (`workspace_id` in every lookup), agent layer (AI-01), background jobs (WF-01), MCP keys and
  exports (`test_isolation`). No gap found.
- **Authentication and authorisation.** Declarative per-route roles (`web.Router`), `role_at_least`, approvals
  need editor, oversight needs admin, agent tools now carry the route's role (FIN-01). Session attacks:
  `test_adversarial.AuthenticationAndSessions` (tampered, revoked, idle, query-string tokens; forged origins).
- **Electron IPC.** `guards.ts` decision functions tested (7); dispatch not automated (OB-12 residual).
- **Financial exports.** OB-03 incl. invisible prefixes; numbers stay numbers (oracle).
- **Webhooks and connectors.** WH-01; OB-01; OB-04; connector responses validated against the Open Accounting
  Connector schema (account names ≤ 200 chars, which also bounded the injection payload in AI-04).
- **Secrets, logs, exports.** gitleaks over the history; `test_isolation.LeakageThroughLogsAndErrors` (canaries
  never in logs or responses); `OB13`; `WH-01`; audit entries keep URLs without query strings.
- **Supply chain.** Lockfiles + `npm ci`; pip-audit; Trivy; SC-01 and SC-02 open; AR-7.

## 3. Totals

| | Count |
|---|---|
| Risks reviewed | 42 rows (28 findings, 10 accepted risks, 4 open items incl. OP-8) |
| Critical remaining | 0 |
| High remaining | 0 (every High-rated finding is CLOSED with executed evidence) |
| Closed with security + functional + regression evidence | 24 (OB-01…08, 10…15, 17…19, DB-01/02/03, WH-01, WF-01, NEW-01/02, FIN-01, AI-01/02/04) |
| Accepted risks | 10 (AR-1…AR-10) — owner sign-off **pending**, none invented |
| Open | 6 (DB-06/OP-3, AI-05/OP-9, AI-06/OP-10, SC-01/OP-7, SC-02/OP-11) + OP-8 in progress |
| New this verification | FIN-01 (fixed), AI-05, AI-06, SC-02 (open, low) |
