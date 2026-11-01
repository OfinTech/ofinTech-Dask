# Owner risk acceptance register

Prepared 1 November 2026 for the product owner's decision before enterprise expansion. The audit prepared this
register; **it has not accepted any risk**. Every row that needs the owner's acceptance is marked
**PENDING OWNER APPROVAL** and stays so until the owner fills in the acceptance block at the end of this
document. Nothing in this file is a signature, an approval or a compliance statement.

Status of each item was re-checked against the code at `b1374ed` and the CI runs named in the evidence column.

## Decision vocabulary

`MITIGATE BEFORE RELEASE` · `ACCEPTANCE REQUESTED` (the audit asks the owner to accept) ·
`ACCEPTED BY OWNER` (only after the owner records it below) · `DEFER WITH CONTROLS` · `CLOSED — VERIFIED`.

## 1. Risk matrix

| ID | Description | Component | Severity | Likelihood | Business impact | Financial / data-security impact | Existing controls | Residual risk | Mitigation recommendation | Owner | Review | Evidence | Proposed decision |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| AR-1 | Single-process engine; no horizontal scaling; a restart drops open streams | Team Server deployment | Low | Medium (restarts happen) | minutes of unavailability per restart | none: runs, approvals and workflow runs are persisted and resume | Docker restart policy, health check, resumable runs | availability only | PostgreSQL/Redis design (doc 07 M8) when a customer needs > 1 replica | repository owner | 2027-02-01 | `docs/DEPLOYMENT.md`; `workflows.resume_interrupted` | **ACCEPTANCE REQUESTED** — PENDING OWNER APPROVAL |
| AR-2 | Untrusted text (documents, e-mails, tool results) not structurally delimited in prompts | agent prompts | Medium | Medium | a misleading answer | **no action**: 26 injection cases show every outbound or state-changing action waits for a person; figures without a source are flagged | approvals, step-up on outbound approvals (new), sealed workspaces, memory proposals, admin-gated skills | confusion, not action | OP-1 delimiting (doc 07 M2) | repository owner | 2026-12-15 | `FINAL-AI-AGENT-SECURITY-ASSURANCE.md` §D; `test_ai_security`, `test_ai_financial_assurance` | **ACCEPTANCE REQUESTED** — PENDING OWNER APPROVAL |
| AR-3 | Live integrations validated against mocks only (OAuth, Google sign-in, SMTP, S3, Odoo, PipesHub, OCR non-English, installers) | connectors | Medium | High (first live use will find something) | an integration fails at go-live | no security control depends on these paths; a live failure is a functional failure | mocks follow documented contracts; the adapter example; the Zoho demo import (31 Oct) | functional surprises | validate each integration in a staging workspace with the customer's own accounts before go-live | repository owner | per go-live | `MULTI-TENANT-INTEGRATION-RESULTS.md` (model simulated) | **ACCEPTANCE REQUESTED** — PENDING OWNER APPROVAL, with the go-live validation as a condition |
| AR-4 | No multi-user load measurement; no metrics endpoint | engine | Low | Medium | slowdowns seen by users first | none | single-user figures (200k-row import 2.7 s, queries ≤ 110 ms); heavy work off the loop | unknown ceiling | OP-2: `/metrics`, alerting, a load run | repository owner | 2026-12-15 | `SECURITY-ASSURANCE-REPORT.md` §5 | **ACCEPTANCE REQUESTED** — PENDING OWNER APPROVAL |
| AR-5 | Desktop host trusts its OS user (master key and token readable by that account) | desktop | Low | Low | laptop compromise = local data compromise, as for any desktop app | local data only | OS keychain where available; the new inactivity lock (screen, not credential) | by design | none planned | repository owner | not planned | `docs/SECURITY.md` "Desktop" | **ACCEPTANCE REQUESTED** — PENDING OWNER APPROVAL |
| AR-6 | Viewers' chat attachments become workspace documents and reach PipesHub | knowledge | Low | Medium | a viewer adds to the shared knowledge base | none (consistent with the viewer's read rights) | collection shown; admins delete; PipesHub scoped | low | per-workspace switch | repository owner | 2027-02-01 | OB-16 | **ACCEPTANCE REQUESTED** — PENDING OWNER APPROVAL |
| AR-7 | Scanner images and actions pinned by tag, not digest | CI | Low | Low | a compromised upstream tag alters CI; the product is unaffected | none directly | read-only token; secrets only in packaging; lockfiles | low | pin by digest (a maintainer with registry access) | repository owner | 2026-12-15 | `build.yml` | **DEFER WITH CONTROLS** — PENDING OWNER APPROVAL |
| AR-8 | Cheap five-minute login lockout of a known e-mail (8 wrong passwords / 5 min) | auth | Low | Medium | nuisance | none (no access gained) | per-IP limit; Google sign-in unaffected | nuisance | progressive delay | repository owner | 2027-02-01 | `routes/auth._rate_limit`; `test_accounts` | **ACCEPTANCE REQUESTED** — PENDING OWNER APPROVAL |
| AR-9 | Session token also returned in login/signup JSON (unused by the shipped UI) | auth | Low | Low | none unless a third-party client stores it badly | none for the shipped UI | cookie path only; CSRF guard; 12 h / 8 h lifetimes now bound the token too | low | remove once no client needs it | repository owner | 2027-02-01 | `routes/auth._start_session` | **ACCEPTANCE REQUESTED** — PENDING OWNER APPROVAL |
| AR-10 | Compliance (PDPL, SAMA CSF) assessed, not certified | governance | — | — | regulated customers need their own assessment | — | gap assessment (doc 10); no claim in product or docs | — | customer-specific assessment; OP-6 penetration test | repository owner | per customer | `10-PRODUCTION-READINESS.md` | **ACCEPTANCE REQUESTED** — PENDING OWNER APPROVAL |
| OP-3 | Audit/data retention: no retention schedule; no `secure_delete` on the desktop database (deleted rows recoverable from free pages) | database | Low | Low | an unbounded audit log; forensic recovery of deleted rows on a lost laptop | the audit log keeps every entry (good for accountability, needs a schedule); deleted financial rows may be recoverable on the desktop file | audit entries immutable and inside encrypted backups; desktop data encrypted at rest when the OS disk is | documented, not implemented | define a retention schedule with the operator; `PRAGMA secure_delete` + periodic `VACUUM` on the desktop | repository owner | 2027-02-01 | `FINAL-AUDIT-LOGGING-VERIFICATION.md` "Retention" | **DEFER WITH CONTROLS** — PENDING OWNER APPROVAL |
| OP-7 | Dependabot alert 147: `minimatch` < 11.0.1 (moderate, ReDoS) in the desktop's **development** tree (electron-builder, `dev: true` throughout) | desktop build tooling | Low | Low (needs a hostile glob at build time on the build machine) | CI/build machine only; not in the shipped app | none for users | outside `--omit=dev --audit-level=high`; Dependabot's own update jobs fail (no reachable fixed transitive) | build-time only | `overrides` entry or electron-builder upgrade, then the `lockfiles` workflow — needs registry access the audit machine lacks | repository owner | 2026-11-15 | `FINAL-CI-VERIFICATION.md` §5 | **DEFER WITH CONTROLS** — PENDING OWNER APPROVAL (not a release blocker: not shipped) |
| OP-8 | Intermittent stall of the engine test suite (test support code, not the product) | CI | — | — | CI time | none | root cause found and fixed (`4068d0d`); faulthandler abort; `settle()` bound | — | close after ten consecutive clean engine-test steps | audit | 2026-11-15 | engine-test step `success` on 9 consecutive runs since `4068d0d` (41277460158 … 41292823606); the tenth is the run on `df5b3ae`/`b1374ed` — see `FINAL-RELEASE-GATE.md` | **CLOSED — VERIFIED** once the tenth run is green (recorded in the release gate) |
| OP-11 | Python dependencies pinned by exact version, not by hash | supply chain | Low | Low | a registry compromise could substitute a package of the same version | integrity of the engine | 13 exact pins; pip-audit in CI; Trivy on the image | low | `pip-compile --generate-hashes` + `--require-hashes` in CI and the image | repository owner | 2026-12-15 | `engine/requirements.txt`; `build.yml` | **DEFER WITH CONTROLS** — PENDING OWNER APPROVAL |
| OP-12 | Session policy behaviours not driven by a real browser | UI | — | — | — | — | — | — | — | audit | — | `OP-12-BROWSER-SESSION-VERIFICATION.md` | see that report: **CLOSED — VERIFIED** for the browser scenarios; the three Electron-only scenarios remain NOT VERIFIED and are carried as OP-14 |
| OP-13 | The audit log lives in the application database; an attacker with the file and the ability to drop triggers can rewrite history | audit | Medium (for regulated deployments) | Low | loss of accountability after a full compromise | forensic integrity | immutable within the engine; inside encrypted backups; every event also leaves through outgoing webhooks | depends on the deployment | ship entries to an external append-only store (SIEM/syslog via the webhooks) for regulated customers | repository owner | 2027-02-01 | `FINAL-AUDIT-LOGGING-VERIFICATION.md` | **DEFER WITH CONTROLS** — PENDING OWNER APPROVAL; **MITIGATE BEFORE RELEASE** for a regulated customer |
| OP-14 (new) | Electron shell behaviours (minimize/restore, OS sleep/resume IPC, lock on `powerMonitor` events) not automated | desktop shell | Low | Low | a missed lock on sleep on some OS | screen exposure on an unattended laptop | page lock after the inactivity window is browser-verified; IPC wiring reviewed | low | an Electron-driven test on a runner with a display (`xvfb-run`) | repository owner | 2026-12-15 | `OP-12-BROWSER-SESSION-VERIFICATION.md` §7 | **DEFER WITH CONTROLS** — PENDING OWNER APPROVAL |
| OP-1, OP-2, OP-4, OP-5, OP-6 | delimiting; metrics/load; master-key rotation; reviewer-manipulation case; independent penetration test | various | Low–Medium | — | see `OPEN-RISKS.md` | — | — | — | as planned | repository owner | 2026-12-15 / 2027-02-01 / before a regulated customer | `OPEN-RISKS.md` | **DEFER WITH CONTROLS** — PENDING OWNER APPROVAL (OP-6 **MITIGATE BEFORE** a regulated customer) |

Totals: 10 accepted-risk candidates (AR-1…AR-10), 11 open items (OP-1…OP-7, OP-11, OP-13, OP-14; OP-8 closing;
OP-12 closed for browsers). **Critical: 0. High: 0.** Items that must be mitigated before a *regulated*
customer: OP-6 and OP-13. Nothing is a blocker for a controlled pilot with an informed owner.

## 2. Owner risk acceptance record

The owner (or a person the owner names as authorised approver) records each decision here. Until a row is filled
in, the corresponding risk is **PENDING OWNER APPROVAL**. The audit will not fill this in.

| Risk ID | Authorised approver (name, role) | Decision (ACCEPTED BY OWNER / MITIGATE / DEFER) | Date | Scope (which deployments, which customers) | Justification | Expiration / review date |
|---|---|---|---|---|---|---|
| AR-1 | | | | | | |
| AR-2 | | | | | | |
| AR-3 | | | | | | |
| AR-4 | | | | | | |
| AR-5 | | | | | | |
| AR-6 | | | | | | |
| AR-7 | | | | | | |
| AR-8 | | | | | | |
| AR-9 | | | | | | |
| AR-10 | | | | | | |
| OP-3 | | | | | | |
| OP-7 | | | | | | |
| OP-11 | | | | | | |
| OP-13 | | | | | | |
| OP-14 | | | | | | |
| OP-1 / OP-2 / OP-4 / OP-5 / OP-6 | | | | | | |

How to record a decision: edit this table in a commit by the approver (or a commit that names the approver and
the date in its message), keep the row's review date at or before the one proposed above unless the
justification says why not, and update the matching row in `OPEN-RISKS.md`. An acceptance expires on its review
date; an expired acceptance is pending again.

## 3. What acceptance does and does not mean

- Accepting AR-1…AR-10 makes the product's **technical** readiness decision (`FINAL-RELEASE-GATE.md`) into a
  **business** decision to deploy with those risks known and owned.
- It does not make any regulatory statement: PDPL, SAMA CSF and similar remain the customer's and the owner's
  assessments (AR-10); the audit documents controls and gaps and claims no certification.
- It does not remove the conditions of deployment in `ENTERPRISE-READINESS-REPORT.md` (TLS, keys apart, backups
  rehearsed, integrations validated per customer).
