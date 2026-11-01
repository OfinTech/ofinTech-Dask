# Open risks and accepted risks

State on 1 November 2026 after the final gate. Every item names an owner, the business impact if it materialises,
the mitigation in place, and the review date. "Accepted" means the owner carries the risk knowingly for the period
named; it is not closed. Findings that are closed with evidence are in `SECURITY-ASSURANCE-REPORT.md`.

## Accepted risks

Owner sign-off: **PENDING OWNER APPROVAL** for every row. The review dates and mitigations were set by the audit;
the repository holds no written acceptance by the owner. The decision-ready package, with the full matrix and the
acceptance record the owner fills in, is `FINAL-OWNER-RISK-ACCEPTANCE.md`; nothing below is accepted until a row of
that record is completed by the owner.

| ID | Risk | Owner | Business impact | Mitigation in place | Review |
|---|---|---|---|---|---|
| AR-1 | **Single-process engine.** One engine container per deployment; a restart drops open streams and marks running agent runs as interrupted; no horizontal scaling. | repository owner | availability only; no data loss (runs and approvals are persisted and resume; workflow runs in `running` resume, agent runs are marked and can be re-asked) | Docker restart policy; health check; documented trigger for the PostgreSQL/Redis design (document 07 §9 M8) | 2027-02-01 or on the first customer needing > 1 replica |
| AR-2 | **Untrusted text is not structurally delimited in prompts** (OB-09 part 2). A model may be confused by instructions inside documents, e-mails or webhook bodies. | repository owner | a wrong or misleading answer; **not** an action: every outbound or state-changing action needs a person's approval, payments cannot exist, sealed workspaces remove outbound tools, memory needs approval, skills need an admin | `test_ai_security` (13 cases) proves no action results; figure check flags unsourced numbers; reviewer isolated | 2026-12-15 (R8 part 2 planned) |
| AR-3 | **Live integrations validated only against mocks** (OAuth providers, Google sign-in, SMTP, MinIO/S3, Odoo, PipesHub, OCR with non-English data, packaged installers). | repository owner | an integration may fail on first live use; no security control depends on these paths working | mocks follow the providers' documented contracts; the adapter example and the demo company exercised the Zoho import live on 31 Oct (project notes) | before each customer go-live, per integration used |
| AR-4 | **No multi-user load measurement; no metrics endpoint.** | repository owner | slowdowns under load would be seen by users before operators | single-user figures measured (200k-row import 2.7 s, queries ≤ 110 ms); heavy work off the event loop; logs | 2026-12-15 (R9 `/metrics` and a load run) |
| AR-5 | **Desktop host trusts its user.** Another process of the same OS account can read the engine's environment (master key, token) and decrypt `master.key`. | repository owner | compromise of the laptop is compromise of its local data, as for any desktop application | OS keychain where available; documented in `docs/SECURITY.md` | not planned to change (by design) |
| AR-6 | **Viewers' chat attachments become workspace documents and reach PipesHub** (OB-16). | repository owner | a viewer can add to the shared knowledge base; consistent with the viewer's read rights | collection shown on upload; admins can delete; PipesHub scoped per workspace | 2027-02-01 (per-workspace switch under consideration) |
| AR-7 | **Pinned scanner images and actions by tag, not digest** (Trivy `latest`, `actions/*@v7`). | repository owner | a compromised upstream tag could alter CI behaviour; the product itself is unaffected | read-only workflow token; secrets reach only the desktop packaging job; lockfiles pin all npm dependencies | 2026-12-15 (pin by digest once a maintainer can resolve them) |
| AR-8 | **Login rate limit allows a cheap lockout** of a known e-mail address (8 wrong passwords in 5 minutes). | repository owner | a nuisance for the targeted user for five minutes; no access gained | per-IP limit too; Google sign-in unaffected | 2027-02-01 (progressive delay planned) |
| AR-9 | **Session token also returned in the login/signup JSON** (unused by the shipped UI). | repository owner | none unless a third-party client stores it insecurely | cookie remains the only path the UI uses; CSRF guard on cookie writes; bearer path cannot be forged cross-site | 2027-02-01 (remove once no client needs it) |
| AR-10 | **Compliance (PDPL, SAMA CSF) is assessed, not certified.** | repository owner | regulated customers need their own assessment | gap assessment in `10-PRODUCTION-READINESS.md`; no claim made in product or docs | per customer |

## Open items (not accepted; work planned)

| ID | Item | Plan | Target |
|---|---|---|---|
| OP-1 | Structural delimiting of untrusted text; connector error bodies out of model-visible output | R8 part 2 (document 07 M2) | 2026-12-15 |
| OP-2 | `/metrics`, alerting, a multi-user load run | R9 | 2026-12-15 |
| OP-3 | Retention policy and `secure_delete` on the desktop database (DB-06) | R9 | 2027-02-01 |
| OP-4 | Key rotation command for the master key | document 07 M6 | 2027-02-01 |
| OP-5 | Reviewer-manipulation case for the AI suite; a larger malicious-document corpus | R8 | 2026-12-15 |
| OP-6 | Independent penetration test of a staged deployment | engage a third party | before the first regulated customer |
| OP-14 | Electron shell behaviours of the session policy (minimize/restore, OS sleep/resume IPC, lock on `powerMonitor` events) are implemented and reviewed but not driven by Electron automation (the browser-level lock and resume are verified) | an Electron-driven test on a runner with a display (`xvfb-run`) | 2026-12-15 |
| OP-13 | The audit log lives in the application database; an attacker with the file and the ability to drop triggers can rewrite history | ship entries to an external append-only store through the outgoing webhooks (they already carry every event) for regulated deployments | 2027-02-01 |
| OP-11 | Python dependencies pinned by version, not hash (SC-02) | `pip-compile --generate-hashes`; `pip install --require-hashes` in CI and the image | 2026-12-15 |
| OP-7 | Dependabot alert 147 (moderate): `minimatch` 3.x/5.x in the desktop's **development** tree (electron-builder tooling); not shipped in the app and outside the CI gate (`--omit=dev --audit-level=high`) | add an `overrides` entry or bump electron-builder, regenerate the lockfile with the `lockfiles` workflow | 2026-11-15 |

## Closed since the previous report

**OP-12 — browser automation of the session policy (CLOSED — VERIFIED, `0cc17c9`/`95ca47d`).** `e2e/session_browser.py`
drives Chromium (Playwright) against real engine processes with the built UI, both clocks controlled: automatic
sign-out at 30 and at 45 minutes, the warning at 28:00 in English and Arabic (RTL) with a live countdown and
keyboard operation, Stay signed in validated by the engine and refused for a dead or revoked session, background
polling/streams/reads not extending, two tabs signing out together and not reviving, offline past the limit and
reconnection, sleep/wake, and the desktop lock and resume. 21 passed, 0 failed; evidence with screenshots in
`docs/audit/evidence/session/` and the step in CI. The Electron-only scenarios move to OP-14.
Report: `OP-12-BROWSER-SESSION-VERIFICATION.md`.

**OP-8 — engine test-suite stall (CLOSED — VERIFIED).** Ten consecutive clean engine-test steps since the fix
(`4068d0d` → `df5b3ae`, runs 41277460158 … 41293129846) — see `FINAL-RELEASE-GATE.md`.

**Session policy (new controls, `03bdb3b`).** 12-hour lifetime (8 for administrators), step-up re-authentication for
high-risk actions, own-session management and administrator termination. `FINAL-SESSION-SECURITY-POLICY.md`.

**OP-9 — approval expiry and replay protection (CLOSED, `1f1a8de`).** Approvals carry an expiry (4 h outbound /
24 h internal) and a digest of workspace, requester, tool and canonical arguments; a decision refuses expired or
decided rows and a requester who lost the role; execution claims the row with one atomic update that re-checks
everything, so one approval executes once and concurrent claims cannot both win; membership loss, demotion and
thread stop revoke. Ten mandatory cases in `tests/test_approvals_and_audit.py`, all passing; AI-05 closed with it.
Evidence: `FINAL-AI-APPROVAL-SECURITY.md`.

**OP-10 — audit logging (CLOSED, `1f1a8de`).** One audit service with event id, correlation id, resource,
decision, approval reference and result on every entry; sanitised details; immutable rows (triggers); every tool
call, refusal, 403, cross-tenant attempt, failed sign-in, logout and expired session recorded; admin-only reading.
Tested for successful, failed, denied, expired and replayed actions and for secrets. AI-06 closed with it.
Evidence: `FINAL-AUDIT-LOGGING-VERIFICATION.md`.

**Session inactivity (new control, `9e47e90`).** 30-minute server-enforced sign-out (30–45 configurable), warning
two minutes before, stay-signed-in that cannot revive a dead session, multi-tab sign-out, resync after sleep,
30-day lifetime, desktop lock. Evidence: `SESSION-SECURITY-VERIFICATION.md`.

**RBAC sweep (`fe24400`)** and the **live two-tenant run (`3d055c1`)**: `FINAL-ACCESS-CONTROL-VERIFICATION.md`,
`MULTI-TENANT-INTEGRATION-RESULTS.md`.


FIN-01 (viewer's assistant could create dashboards, generated files, editable copies, close runs and refresh
accounting datasets without approval; tools now carry the role of their route, `097f9aa`) — evidence in
`FINAL-AI-AGENT-SECURITY-ASSURANCE.md`.

OB-08 (effects decided and implemented), OB-10 (MCP tool re-approval), OB-12 (automated desktop tests), DB-01
(encrypted backups), lockfiles (OB-17), NEW-01/02 (oversight role, scoped lookups in the pages added on 1 Nov),
WH-01 (webhook payload cleaner), workflow creator re-check — evidence in `SECURITY-ASSURANCE-REPORT.md`.
