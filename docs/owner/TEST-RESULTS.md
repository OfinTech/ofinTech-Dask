# Owner Control Center — test results

Every figure below was read from an executed run; nothing is estimated. The reference commit is the one the ZIP was
built from.

| Item | Value |
|---|---|
| Final commit | `8c126b4` — "Owner browser test: anchor on the rail's visible labels, not the hidden mobile bar" |
| Feature commits | `eacd9b4` (implementation), `0d536cc`, `7a54f77`, `8c126b4` (CI fixes: gitleaks allow-list for the Stripe sandbox fixtures, browser-test selectors) |
| CI run on `8c126b4` | **41335992591** — https://github.com/OfinTech/ofinTech-Dask/actions/runs/41335992591 — **success** (jobs `test`, `security`, `team-server-image`, `desktop` ×3 all green; `release` skipped: tags only) |
| Local verification | full engine suite and the UI build also run from the extracted ZIP (353 OK; UI built; i18n 2483/2483 ×3) |

## Automated tests (CI, Python 3.12, Ubuntu)

| Suite | Result |
|---|---|
| Engine unit and API tests (`python -m unittest discover`) | **353 passed, 0 failed** (316 pre-existing + 37 Owner Control Center) |
| ├ `test_owner_access` (authorization, spoofing, delegation, step-up, dual approval, secrets, content boundary, support access, suspension, forced reset, maintenance, workspace states, TOTP) | 16 passed |
| ├ `test_owner_billing` (plan limits incl. 6 concurrent requests, downgrade → read-only, quotas, features, self-hosted, plan builder, Stripe checkout/webhooks/signature/dedupe/replay/order/failed payment/grace/reconcile/dead-letter/trial/mode mismatch) | 12 passed |
| ├ `test_owner_comms` (coupons, e-mail settings/templates/queue/retry/suppression, announcements, owner alerts, Contact Sales, analytics collection/metrics/exports/retention, Google policy) | 9 passed |
| UI type check (`tsc --noEmit`), translations (`i18n:check` 2483/2483 for ar, es, fr), `npm test` (6), production build | passed |
| Multi-tenant integration on a real server (HTTP) | 41/41 checks |
| Browser smoke test | passed |
| Session policy in a real browser (OP-12) | 22 passed, 0 failed, 4 not verified (Electron-only, unchanged) |
| **Owner Control Center in a real browser** (`e2e/owner_browser.py`, Chromium, hosted engine) | **12 passed, 0 failed** |
| Desktop type check and guard tests | 7 passed; installers built on macOS, Windows, Ubuntu |
| gitleaks (history and tree) | no leaks found |
| ruff security rules | all checks passed |
| pip-audit | no known vulnerabilities |
| npm audit (runtime) | 0 vulnerabilities ×2 |
| Trivy (Team Server image) | passed |

### Browser scenarios (O-1 … O-12)

| Id | Scenario | Result |
|---|---|---|
| O-1 | Owner (from `DESK_PLATFORM_OWNERS`) signs up and reaches `#/owner`; 22 KPI tiles render | PASS |
| O-2 | All 16 sections render without an error | PASS |
| O-3 | Plan editor: Pro workspace limit changed and saved (API confirms) | PASS |
| O-4 | Stripe secret saved; only `…ABCD` shown, full key absent from the page | PASS |
| O-5 | Coupon created from the dialog and listed | PASS |
| O-6 | SMTP settings saved; the password never appears again | PASS |
| O-7 | Announcement sent now; delivered in-app | PASS |
| O-8 | Ordinary account refused the owner area (page and API 403) | PASS |
| O-9 | Free plan: first workspace 200, second 403 `workspace_limit` | PASS |
| O-10 | New user appears in the directory | PASS |
| O-11 | Arabic interface, `dir=rtl` | PASS |
| O-12 | 390 px viewport without horizontal overflow | PASS |

Screenshots are uploaded by CI as the `e2e-evidence` artifact (`owner/*.png`, `owner/evidence.json`).

## Acceptance criteria

| Criterion | Evidence |
|---|---|
| Authorized owners can access the dashboard | O-1; `test_only_platform_administrators_reach_the_owner_api` |
| Unauthorized users cannot access owner APIs | O-8; access tests (403 `owner_required`, audited) |
| New users appear in the administration interface | O-10 |
| Free plan enforces one hosted workspace | O-9; `test_free_plan_allows_one_hosted_workspace_and_pro_two`, `test_limit_holds_under_concurrent_requests` |
| Pro plan enforces two hosted workspaces | same test (third refused with limit 2) |
| Paid file storage quotas are enforced | `test_storage_quota_member_limit_and_feature_entitlements` (403 `storage_quota`) |
| Plans managed from the dashboard | O-3; `test_plan_builder_validation_and_visibility` |
| Stripe sandbox billing works | `Stripe.*` tests through the real client against `tests/stripe_mock.py` (checkout → webhook → Pro; failed payment → grace; reconcile) |
| Google OAuth configured securely | `GooglePolicy` test; secret write-only |
| Notifications created and managed | O-7; `Announcements` tests |
| Email providers and templates configurable | O-6; `Email` test (EN/AR templates, queue, retries, suppression) |
| Coupons created and redeemed | O-5; `Coupons` tests (redemption recorded from the subscription discount) |
| Analytics displayed from real collected events | `Analytics` tests (consent, visitor hash, KPIs, series, CSV); O-1 overview |
| Sales inquiries managed | `Sales` test (submit → inbox → status/assignment/notes/reminder/CSV) |
| Security actions audited | access tests check `audit_log` rows for refusals, grants, secrets, changes |
| Arabic and English interfaces work | O-11; i18n 2483/2483 |
| Existing financial workflows remain functional | the 316 pre-existing tests incl. the golden financial fixture and Decimal oracles pass unchanged; multi-tenant 41/41; OP-12 22/22 |
| Required tests pass | run 41335992591 |
| ZIP contains the actual source | `scripts/package-owner-center.sh` → `git archive` of `8c126b4` (535 files, 4.3 MB), verified with `unzip -t`, engine suite and UI build re-run from the extracted copy |

## Not verified (honest list)

* Live Stripe, live SMTP and live Google OAuth: no credentials in CI; the adapters are exercised against a mock Stripe
  and a fake mailer through the real code paths. Operator verification steps are in `DEPLOYMENT.md`.
* Electron desktop scenarios of the session policy (4, pre-existing OP-14) — unchanged by this work.
* SSO (SAML/OIDC): not implemented, shown as *planned*.
