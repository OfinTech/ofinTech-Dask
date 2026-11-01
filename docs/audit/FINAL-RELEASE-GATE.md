# Final release gate

## Scope

The closing phase of the security programme on OfinTech Desk, in two steps on 1 November 2026:

1. Hardening (`097f9aa` … `3d055c1`): OP-9 approvals, OP-10 audit logging, RBAC across every path, a live
   two-organisation run, server-enforced inactivity sign-out.
2. Enterprise gate (`2189e1e` … `e02220c`): OP-12 closed with real-browser evidence, the session policy reviewed
   and hardened (12 h / 8 h lifetimes, step-up re-authentication, session termination), and the remaining risks
   prepared for the owner's explicit acceptance.

This document is the engineering decision. It is not a certification, not a compliance statement, and not a
business acceptance of risk — that last one is the owner's, in `FINAL-OWNER-RISK-ACCEPTANCE.md`.

## Final commit and CI

| Item | Value |
|---|---|
| **Final code commit** | **`e02220c`** — "Ignore a shared activity timestamp from the future instead of clamping it" |
| Evidence commit by CI | `ff77743` (browser session evidence for `e02220c`; a bot commit, runs no workflow) |
| Reports commit | the documentation-only commit that carries this file (its own run is recorded in the verification summary delivered with it) |
| **CI run on `e02220c`** | **41294821835** — https://github.com/OfinTech/ofinTech-Dask/actions/runs/41294821835 — **success** |
| Jobs | `test` 124813339636 ✔ · `security` 124813339892 ✔ · `team-server-image` 124813898384 ✔ · `desktop (macos-15)` 124813898376 ✔ · `desktop (ubuntu-22.04)` 124813898412 ✔ · `desktop (windows-2022)` 124813898447 ✔ · `release` skipped (tags only) |
| Browser evidence run | `session-evidence` 41294821631 — https://github.com/OfinTech/ofinTech-Dask/actions/runs/41294821631 — success, committed as `ff77743` |

## Verification on `e02220c` (read from run 41294821835's log)

| Mandatory check | Result | Evidence |
|---|---|---|
| Engine regression suite | **PASS** — `Ran 316 tests in 48.274s … OK` (Python 3.12; locally 316 OK on 3.13) | `test` / Engine tests |
| Financial golden fixture | **PASS** — `Golden.test_every_output_matches_the_pre_audit_engine` ok (fixture from `e8de370`, byte-identical) | same |
| Financial Decimal oracles | **PASS** — 11 `Oracles.*` ok | same |
| RBAC tests | **PASS** — `test_rbac` 6 (192-route sweep under every lower role) | same |
| AI agent authorization tests | **PASS** — `test_ai_financial_assurance` 8, `test_ai_security` 13, `test_adversarial` 13, `test_isolation` 6 | same |
| Approval expiration and replay | **PASS** — `test_approvals_and_audit` 10 | same |
| Session inactivity | **PASS** — `test_session_inactivity` 18 (30 and 45 min, controlled clock) | same |
| Session policy (lifetimes, step-up, revocation, background credentials) | **PASS** — `test_session_policy` 5 | same |
| Playwright browser tests (OP-12) | **PASS** — `browser session tests: 22 passed, 0 failed, 4 not verified` (the 4 are Electron-only: OP-14) | `test` / "Session policy in a real browser"; `docs/audit/evidence/session/` |
| Electron session tests | **NOT VERIFIED** for the shell (minimize/restore, OS sleep/resume IPC); the desktop page's lock/resume **PASS** in Chromium; `desktop/test/guards.test.mjs` 7/7 | OP-14 |
| Multi-tenant integration (real process, HTTP) | **PASS** — `41/41 checks passed on e02220c` | `test` / "Two organisations on one real server" |
| Frontend build and type check | **PASS** — `tsc --noEmit`, i18n 1,865/1,865 ×3, `npm test` 6/6, `node build.mjs`, smoke test passed | `test` |
| Security scanning | **PASS** — gitleaks `no leaks found` (history); ruff S `All checks passed!` | `security` |
| Dependency scanning | **PASS** — pip-audit `No known vulnerabilities found`; npm audit `found 0 vulnerabilities` ×2; Trivy on the image green | `security`, `team-server-image` |
| Desktop packaging | **PASS** — three installers | `desktop` ×3 |
| Development-tree npm advisory (Dependabot 147) | **NOT VERIFIED by the gate** (outside `--omit=dev`; not shipped) | OP-7 |
| Hash-pinned Python dependencies | **PARTIAL** (exact pins, no hashes) | OP-11 |

Every count was read from the run's log on the exact commit; no result from an older commit is used as proof.

## What changed in this gate (operators)

- Session lifetime: 12 hours (8 for admins/owners) instead of 30 days; `DESK_SESSION_HOURS`,
  `DESK_ADMIN_SESSION_HOURS` (`DESK_SESSION_DAYS` still read, as hours, with a warning).
- Step-up: high-risk actions (roles, connections, MCP servers and keys, providers, closing a month, deleting data,
  egress/review settings, administrator actions, approving an assistant action that leaves the workspace) ask for
  the password unless it was entered in the last `DESK_REAUTH_MINUTES` (10).
- People can list and end their other sessions; the instance administrator can end anyone's.
- Migration 20 (one nullable column on `sessions`) runs on start.
- The page ignores a future activity timestamp, re-checks the session on load, and signs itself out at once when
  the limit passes offline.

## Acceptance criteria

| Criterion | Status |
|---|---|
| OP-12 closed with reproducible browser evidence | **met** — 22/22 browser scenarios, screenshots and log committed, re-run on every push |
| Inactivity timeout 30 min, 30–45 configurable, warning at 2 min, server-enforced, background traffic ignored | **met** — engine tests, browser S0–S4, S1-45min |
| Absolute lifetime reviewed and decided | **met** — 12 h / 8 h, `FINAL-SESSION-SECURITY-POLICY.md` |
| Revocation on logout, expiry, disable, removal, lost permission, admin termination, credential change | **met** — `test_session_policy`, `test_rbac` |
| Token/cookie security reviewed; no insecure storage introduced | **met** |
| Step-up for high-risk financial and security actions, on top of RBAC and approvals | **met** |
| Compatibility (web, desktop, agent, jobs, integrations, data) | **met** — 316 tests, live run, browser run, installers |
| Remaining risks prepared for owner acceptance, none accepted by the audit | **met** — `FINAL-OWNER-RISK-ACCEPTANCE.md`; every AR/OP **PENDING OWNER APPROVAL** |
| Final CI green on the exact release commit | **met** — run 41294821835 on `e02220c` |

## Risk governance snapshot

Reviewed: 10 accepted-risk candidates (AR-1…AR-10) and 12 open items. Critical: 0. High: 0. Closed in this
gate: OP-8 (ten consecutive clean engine-test steps), OP-12 (browser), AI-05/AI-06 (earlier). Requiring
mitigation before a *regulated* customer: OP-6 (independent penetration test), OP-13 (external audit-log
shipping). Awaiting the owner: all AR rows and the DEFER rows (OP-3, OP-7, OP-11, OP-14, OP-1/2/4/5/6).
Formally accepted by the owner: **none** — the register is empty until the owner fills it in.

## FINAL DECISION

**APPROVED WITH EXPLICIT LIMITATIONS**

Technical readiness: every critical security control is verified with executed evidence on the exact release
commit — in-process, over real HTTP against a real server, and in a real browser — and no critical or high
finding is open. Two findings surfaced by the browser evidence (a future activity timestamp, offline sign-out)
were fixed and re-verified before this decision.

The limitations are explicit and are what keep this from "approved for controlled enterprise pilot" as a broad
statement: (1) **business risk acceptance is pending** — the owner has not yet recorded acceptance of AR-1…AR-10
or the deferred items, and the audit does not accept risks on the owner's behalf; (2) the Electron shell's own
sleep/resume behaviour is not automated (OP-14); (3) live third-party integrations and a real model remain
outside the evidence (AR-3, AR-2); (4) a regulated customer needs OP-6 and OP-13 first; (5) no regulatory
compliance is claimed (AR-10). Once the owner completes the acceptance register, the technical evidence here
supports a controlled enterprise pilot with those risks owned; it does not support unqualified broad deployment
until the deferred items are closed.
