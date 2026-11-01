# Enterprise readiness report — final gate

OfinTech Desk, `main` on 1 November 2026 (release 1.1.0 `e8de370` plus the audit's remediation commits).
Prepared by the independent review acting as principal engineer and security architect. Evidence for every
statement below is in `SECURITY-ASSURANCE-REPORT.md` (verification records), `THREAT-MODEL.md` and
`OPEN-RISKS.md`; the earlier numbered documents hold the baseline and the design findings.

## Decision

**READY WITH ACCEPTED RISKS** — for deployment to teams whose operator accepts the ten risks in `OPEN-RISKS.md`
(AR-1 to AR-10) and validates the live integrations they will use before go-live.

This is an engineering judgement from the evidence gathered in this audit. It is **not** a certification and
**not** a compliance statement: no PDPL, SAMA CSF, ZATCA or other regulatory conformance is claimed, and no
third party has tested a staged deployment (OP-6). "Production readiness" here means that the review found no
unresolved Critical or High finding, that every closed finding has an executed negative, positive and regression
test, that financial behaviour is proven unchanged, and that the remaining risks are named, owned and dated.

## What the decision rests on

| Requirement of the gate | Outcome | Evidence |
|---|---|---|
| Outstanding decisions resolved | all six | OB-08 split implemented (3fa9da4); OB-05 strict default kept with migration; backups encrypted under a separate key with restore verification (6110d9a, `backup-check`); lockfiles committed by CI (e30b1b1) and `npm ci` everywhere; CI watched on the latest commits; Electron IPC rules extracted and tested (2c7773c) |
| Adversarial testing, all ten categories | executed, 37 engine + 7 desktop cases | assurance report §3; five attacks found during the work, each closed with a test |
| Financial integrity | proven unchanged | golden fixture identical at every commit; 11 independent Decimal oracles; pre-audit data opens without migration; backup round trip keeps every sum; the model never executes code or alters a computed figure |
| Enterprise isolation across layers | verified | API routes (per-route roles + membership join), database rows (workspace filter, scoped lookups), object storage (workspace-keyed paths), retrieval and memory, background jobs (workflow creator re-check), audit, exports, MCP access keys — `test_isolation`, `test_adversarial`, `test_ai_security` |
| Evidence-based closure | every CLOSED record has the eight items | assurance report §2; nothing closed on inspection |
| Regression safety | 269/269; pre-audit suite 180/182 with only the two intended behaviour changes | assurance report §1 |
| CI gates | gitleaks, ruff security rules, pip-audit, npm audit, Trivy, UI and desktop checks green on `main` | `.github/workflows/build.yml` |
| Secrets | none in the repository history; canaries never appear in logs, errors or responses | gitleaks; `test_isolation.LeakageThroughLogsAndErrors` |

## Changes since the previous readiness assessment (Moderate Risk)

The four items that held the earlier rating at Moderate Risk are resolved: the AI security suite exists and
passes (13 cases plus 13 adversarial); CI has produced green results on every commit since `443eca5`; backups are
encrypted and their restoration rehearsed by a command and a test; single-user performance is measured (200,000-row
import 2.7 s, dataset queries ≤ 110 ms, `/health` < 1 ms). Multi-user load remains unmeasured (AR-4).

## Accepted risks (summary — full table with mitigations in `OPEN-RISKS.md`)

| ID | Risk | Owner | Business impact | Review |
|---|---|---|---|---|
| AR-1 | Single-process engine | repository owner | availability only; no data loss | 2027-02-01 |
| AR-2 | Untrusted text not structurally delimited in prompts | repository owner | a misleading answer, never an action | 2026-12-15 |
| AR-3 | Live integrations validated against mocks only | repository owner | an integration may fail on first live use | per go-live |
| AR-4 | No multi-user load measurement, no metrics endpoint | repository owner | slowdowns seen by users first | 2026-12-15 |
| AR-5 | Desktop host trusts its user | repository owner | laptop compromise = local data compromise | by design |
| AR-6 | Viewers' attachments become workspace documents | repository owner | viewer can add to the knowledge base | 2027-02-01 |
| AR-7 | Scanner images and actions pinned by tag | repository owner | CI behaviour, not the product | 2026-12-15 |
| AR-8 | Cheap five-minute login lockout | repository owner | nuisance, no access | 2027-02-01 |
| AR-9 | Session token also in login JSON | repository owner | none for the shipped UI | 2027-02-01 |
| AR-10 | Compliance assessed, not certified | repository owner | regulated customers need their own assessment | per customer |

## Conditions of deployment

1. Follow `docs/DEPLOYMENT.md`: TLS at Caddy, `DESK_MASTER_KEY` and `backup.key` generated and stored apart,
   `DESK_EGRESS_ALLOW` for internal model hosts, `storage-encrypt` run once where files predate encryption.
2. Schedule `server/backup.sh` and a monthly `backup-check` against the latest backup; rehearse a restore to a
   scratch path before relying on it.
3. Validate each live integration the customer will use (AR-3) in a staging workspace with the customer's own
   accounts, and keep MCP tools on *ask* until an admin has reviewed each one.
4. Re-run the full engine suite, the desktop tests and the CI gates on every change; the golden financial fixture
   must stay identical unless a calculation change is intended and documented.
5. Revisit this decision at the earliest review date in `OPEN-RISKS.md` (15 December 2026) or on any High finding.

## What would change the decision

- **To NOT READY:** a new High finding without a fix; a failing golden fixture without a documented calculation
  change; loss of the CI gates; a live integration leaking credentials.
- **To READY (without accepted risks):** OP-1 (delimiting), OP-2 (metrics and a load run), OP-6 (independent
  penetration test) completed, and AR-3 validated for the integrations in use.
