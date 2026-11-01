# Open risks and accepted risks

State on 1 November 2026 after the final gate. Every item names an owner, the business impact if it materialises,
the mitigation in place, and the review date. "Accepted" means the owner carries the risk knowingly for the period
named; it is not closed. Findings that are closed with evidence are in `SECURITY-ASSURANCE-REPORT.md`.

## Accepted risks

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
| OP-8 | **Intermittent stall of the engine test suite** (not of the product). Seen once locally and in three CI runs on identical code (68770e0, f2ebc46, c06346c). The CI abort traceback on c06346c shows the event-loop thread *executing* `Knowledge.wait_idle` after twenty minutes — the `while tasks: await gather(tasks)` loop spinning on a finished task whose done-callback had not yet removed it. Fixed by waiting only on pending tasks and dropping finished ones (`Knowledge.wait_idle`, `FileService.wait_idle`); the tests now wait through `Harness.settle()`, which turns any stall into a failure naming the stuck tasks after 60 s. Not reproducible locally (25 runs of the indexing tests, 7 full-suite runs). | watch CI: close after ten consecutive clean `test` jobs; reopen on any `settle()` failure | 2026-11-15 |
| OP-7 | Dependabot alert 147 (moderate): `minimatch` 3.x/5.x in the desktop's **development** tree (electron-builder tooling); not shipped in the app and outside the CI gate (`--omit=dev --audit-level=high`) | add an `overrides` entry or bump electron-builder, regenerate the lockfile with the `lockfiles` workflow | 2026-11-15 |

## Closed since the previous report

OB-08 (effects decided and implemented), OB-10 (MCP tool re-approval), OB-12 (automated desktop tests), DB-01
(encrypted backups), lockfiles (OB-17), NEW-01/02 (oversight role, scoped lookups in the pages added on 1 Nov),
WH-01 (webhook payload cleaner), workflow creator re-check — evidence in `SECURITY-ASSURANCE-REPORT.md`.
