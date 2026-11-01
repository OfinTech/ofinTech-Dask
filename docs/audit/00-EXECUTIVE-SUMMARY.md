# OfinTech Desk — engineering and security audit: executive summary

Audit of release 1.1.0 (`e8de370`, 1 November 2026) by an independent review acting as principal engineer, with
remediation carried out on `main` the same day. This page is the current state; the numbered documents hold the
evidence. **Rating: Moderate Risk** (scale: Critical · High Risk · Moderate Risk · Low Risk · Production Ready;
the rating is a judgement from the evidence in document 10, not a numerical score).

## 1. Architecture

A single Python engine (Starlette) serves two deployment modes, a desktop app with the engine on loopback and a
Team Server behind Caddy. Authorisation is declarative per route and enforced server-side; the agent loop is the
product's own, with human approval for every write; financial calculation is deterministic code; secrets and files
are encrypted with AES-256-GCM bound to their context. The design is sound and should be evolved, not replaced
(document 07). Its structural limits are a single process (no horizontal scaling), SQLite shared by application
and dataset tables, and prompt-injection defence that is textual rather than structural.

## 2. Security posture

No P0 was found. Three P1 findings (an OAuth callback that trusted a prefix-matched host, formula injection in
the close exports, unauthenticated plaintext accepted from the file store) and nine P2 findings were identified;
**all P1 and all but one P2 are fixed** with regression tests, the remaining P2 (OB-09) is mitigated and planned.
Verified controls that held under review: tenancy filters, parameterised SQL, scrypt, hashed single-use tokens,
constant-time comparisons, CSP without inline script, Electron hardening, payment refusal and sealed egress
enforced in code.

## 3. Findings (document 02)

| ID | Severity | Title | Status |
|---|---|---|---|
| OB-01 | P1 | OAuth callback trusted a prefix-matched Zoho host | fixed 769e263 |
| OB-03 | P1 | Formula injection in the close journal and pack | fixed d409034 |
| OB-05 | P1 | Encrypted storage returned unauthenticated plaintext | fixed 87e0196 (behaviour change) |
| OB-02 | P2 | CSRF origin compared by prefix | fixed 769e263 |
| OB-04 | P2 | Outbound policy only at save time (rebinding, CGNAT) | fixed 87e0196 |
| OB-06 | P2 | Parser resource limits (zip bombs, image formats, OCR time) | fixed 4bc1d39 |
| OB-07 | P2 | Body limits relied on Content-Length | fixed 1435e17 |
| OB-08 | P2 | Tool args unchecked, no timeouts, effects by label | 2 of 3 fixed 78644c1; owner decision |
| OB-09 | P2 | Prompt-injection surface | mitigated e705701 (skills gated); delimiting planned |
| OB-10 | P2 | MCP trust and audit hygiene | partly fixed 87e0196 |
| OB-11 | P2 | Open event streams never re-authorised | fixed 1435e17 |
| OB-17 | P2 | No CI security gates | fixed 40db1e1 (green on main) |
| OB-12..16, 18, 19 | P3 | IPC senders, Telegram token, SMTP, parsers, viewer scope, ops, masker | fixed except backups/retention |
| DB-01..06 | P2/P3 | Plaintext backups, loop stalls, checkpoint, retention | DB-02/03 fixed c9138a0, 76496d2; DB-01 owner decision |

## 4. Database and storage (document 04)

SQLite is the right choice for the desktop and for team-sized servers; heavy dataset work now runs off the event
loop and the WAL is checkpointed hourly. PostgreSQL becomes necessary only with multiple engine replicas; the
access layer is ready for it. Backups are consistent but plaintext — the one open P2 in this area.

## 5. AI and agent (document 06)

Thirteen tests drive the real agent loop through the fifteen required cases with a scripted model and mocked
services: injected instructions produce approvals, not actions; payments cannot exist; another workspace's ids
yield nothing; figures without a source are flagged; identifiers are masked; loops end. Added this audit: argument
schema validation, per-tool time limits, admin gating of editor-written skills.

## 6. Code quality and performance

~49,500 lines, no `eval`/`exec`/shell, consistent conventions, 182 → 221 tests. Measured: a 200,000-row ledger
imports in 2.7 s and aggregates in ≤ 110 ms; `/health` stays under a millisecond. Not yet measured: multi-user
load and memory ceiling.

## 7. Changes implemented

16 commits on `main` (5d2baf0 … c547ecf), 39 tests added and 1 rewritten, 10 audit documents, CI gates
(gitleaks, ruff security rules, pip-audit, npm audit, Trivy, read-only token), container hardening, two dependency
security updates. Full record: document 08.

## 8. Remaining risks and decisions

- Owner decisions: OB-08 approval policy for state-changing "read" tools; acceptance of the OB-05 behaviour
  change; lockfiles for `ui/` and `desktop/`; backup encryption (DB-01).
- Planned: structural delimiting of untrusted text (R8 part 2), MCP tool-list re-approval, `/metrics` and
  alerting, retention/secure delete, SHA-pinned actions, multi-user load test.
- Compliance: PDPL and SAMA CSF gap assessments are outlined in document 10 and not claimed as met.

## 9. Production-readiness decision

**Not yet Production Ready; Moderate Risk.** The product can be deployed for a team that accepts plaintext
backups on an encrypted volume and single-process scale, with the deployment checklist in `docs/DEPLOYMENT.md`.
The path to *Low Risk* is the four owner decisions plus R8 part 2 and monitoring; *Production Ready* additionally
needs a multi-user load test and an independent penetration test of a staged deployment.
