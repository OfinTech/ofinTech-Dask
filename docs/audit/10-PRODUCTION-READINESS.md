# 10 — Production-readiness checklist

Living checklist, updated with the roadmap. ✅ met with evidence · 🟡 partly met or met with a documented condition
· ❌ not met · ⬜ not yet assessed. The final rating at the bottom follows the engagement's scale (Critical, High
Risk, Moderate Risk, Low Risk, Production Ready) and is given only with evidence.

## Security

| Item | State | Evidence / condition |
|---|---|---|
| Authentication (passwords, OIDC, sessions, resets, invitations) | ✅ | scrypt, PKCE, hashed single-use tokens, idle timeout; `test_accounts`, `test_team_server` |
| Authorisation enforced server-side on every protected operation | ✅ | declarative roles on 198 routes; isolation tests; OB-11 fixed |
| Tenant isolation | ✅ | `workspace_id` on every owned table and query; `SecretStore` binds workspace; `test_team_server`, `test_knowledge` (PipesHub) |
| Encryption at rest (secrets, files) | ✅ | AES-256-GCM with AAD; OB-05 fixed (no unauthenticated plaintext) |
| Encryption in transit | ✅ (server) | Caddy TLS + HSTS; https required for public URL and user URLs; SMTP `none` cannot carry a login |
| Secret management | ✅ | master key from OS store / env / Docker secret; never in DB; audit URLs stripped (OB-10) |
| Outbound network policy | ✅ | save-time check + `PrivateNetworkGuard` at every request (OB-04); sealed mode |
| CSRF / origin | ✅ | SameSite=Strict + exact origin (OB-02) |
| Input limits | ✅ | body limits independent of headers (OB-07); Office zip and image limits before parsing, OCR budget, Caddy edge cap (OB-06) |
| Export safety | ✅ | all CSV/XLSX writers neutralise formula prefixes (OB-03) |
| Audit logging | ✅ | `audit_log` for sign-in, approvals, write tools, connections, webhooks, ingest; immutable append (no update path in code) |
| Vulnerability management | 🟡 | CI gates green on `main` (gitleaks, ruff S, pip-audit, npm audit); Trivy from fa391df; lockfiles missing |
| Incident response | 🟡 | SECURITY.md reporting process; no runbook for key rotation or session mass-revocation yet |

## AI and agent

| Item | State | Evidence / condition |
|---|---|---|
| Write actions need human approval with exact arguments | ✅ | `harness._request_approval`; `test_more`, `test_workflows` |
| Payments impossible | ✅ | refused at registration, listing and call; `test_security_boundaries` |
| Tool argument validation | ✅ | `validate_args` (OB-08) |
| Tool and run time limits | ✅ | per-tool timeout; `MAX_STEPS = 16` |
| Deterministic financial calculation | ✅ | formula languages parsed and evaluated in code, no `eval`; Excel rounding; figure check on answers |
| Source attribution | ✅ | every cell in generated files carries a source; unverified figures listed |
| Prompt-injection isolation | 🟡 | controls in place and now tested end-to-end (approval, sealing, masking, reviewer isolation, skills gated by admins); structural delimiting of untrusted text still planned (OB-09) |
| AI security test suite (doc 06) | ✅ | 13 tests over the 15 cases, scripted model, mocked services |
| MCP trust | 🟡 | `auto` requires admin + server read-only flag; tool-list change re-approval pending (R8) |

## Data and database

| Item | State | Evidence / condition |
|---|---|---|
| Migrations, integrity constraints, FK on | ✅ | 18 ordered migrations, `foreign_keys=ON`, WAL |
| Concurrency | 🟡 | single process, in-process write lock; heavy dataset work off the loop (DB-02); hourly WAL checkpoint (DB-03) |
| Backups | 🟡 | consistent online copy; **not encrypted** (decision 4 in the roadmap) |
| Data retention / deletion | 🟡 | delete chat and workspace exist; no retention policy or secure-delete for storage objects |

## Reliability and operations

| Item | State | Evidence / condition |
|---|---|---|
| Health check | ✅ | `/health`, Docker HEALTHCHECK |
| Restart recovery | 🟡 | interrupted agent runs marked; workflow runs in `waiting` not resumed (baseline §9) |
| Container privileges | ✅ | non-root, `cap_drop: ALL`, `no-new-privileges`, tmpfs `/tmp` (R6) |
| Network exposure | ✅ | engine not published; only Caddy 80/443; `/mcp` rate-limited (OB-18) |
| Configuration guards | ✅ | `DESK_DEV` refused, https required, key mandatory; SMTP modes validated |
| Monitoring / alerting | ❌ | logs only; no metrics endpoint or alert rules |
| Rollback procedure | 🟡 | image tags; migrations are forward-only (documented) |
| Horizontal scaling | ❌ by design | one engine process (event hub, rate limits, tasks in memory) |

## Performance

| Item | State |
|---|---|
| 200,000-row CSV ledger (9.4 MB): upload + preview | ✅ 2.1 s (audit machine, single process) |
| Commit of those 200,000 rows into a dataset table | ✅ 2.7 s; database 11.4 MB afterwards |
| Grouped sum over 200,000 rows (by month / by party / total) | ✅ 110 ms / 74 ms / 13 ms |
| `/health` round trip after the import | ✅ < 1 ms |
| Dataset parsing, commit and queries off the event loop | ✅ `asyncio.to_thread` (DB-02); other requests are not stalled by them |
| Concurrent users, agent runs in parallel, memory ceiling | ⬜ not measured (needs a multi-client load tool; R9) |

## Compliance (gap assessment, not a certification)

| Framework | State | Notes |
|---|---|---|
| Saudi PDPL | ⬜ | personal data of users and of counterparties in ledgers; needs a record of processing, retention rules and a deletion path per data subject; data residency depends on the LLM provider the customer chooses (sealed mode keeps data inside) |
| SAMA CSF (if a regulated entity deploys) | ⬜ | relevant controls: access management (met), logging (met), encryption (met), vulnerability management (in progress), third-party/cloud (depends on providers), incident response (partial) |
| ZATCA e-invoicing | n/a | the product makes no e-invoicing claim and writes nothing to accounting systems |

## Rating

**READY WITH ACCEPTED RISKS** (1 November 2026, final gate; previously Moderate Risk after R5/R6). All P1 and P2
findings are closed with executed negative, positive and regression tests; no P0 was found; the AI security suite,
CI gates, encrypted backups and single-user performance measurements that held the earlier rating are in place.
The accepted risks (AR-1..AR-10) and open items (OP-1..OP-6) are in `OPEN-RISKS.md`; the decision, its conditions
and what would change it are in `ENTERPRISE-READINESS-REPORT.md`. The rating is a judgement from the evidence
above, not a numerical score and not a certification.
