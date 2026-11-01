# Owner Control Center — integration guide

The Owner Control Center was implemented **directly inside the OfinTech Desk repository**
(https://github.com/OfinTech/ofinTech-Dask, branch `main`). The ZIP `OfinTech-Desk-Owner-Control-Center.zip` is a
source archive of the repository at the commit named in `docs/owner/TEST-RESULTS.md`, so the feature is already
integrated: build and run the repository as usual (`docs/DEPLOYMENT.md`).

## Files added or changed for this feature

**Engine (Python)**

| Path | Change |
|---|---|
| `engine/desk_engine/platform/*.py` | new package: owner, settings, plans, stripe, billing, coupons, email, notifications, analytics, sales, storage, mfa, support |
| `engine/desk_engine/routes/owner.py`, `routes/platform.py` | new route modules, registered in `routes/__init__.py` |
| `engine/desk_engine/db.py` | migration 21 (additive tables and columns) |
| `engine/desk_engine/config.py` | `hosted`, `platform_owners` (`DESK_HOSTED`, `DESK_PLATFORM_OWNERS`) |
| `engine/desk_engine/web.py` | `account_gates()` (maintenance, MFA enrolment, forced reset, read-only account); support access and workspace state in `load_workspace`; `public_user` fields |
| `engine/desk_engine/app.py` | bootstrap (seed plans, provision owners, session overrides), `_platform_tick` scheduler, audit → analytics mirror |
| `engine/desk_engine/routes/auth.py` | registration policy, lockout policy, failed-login alerts, MFA challenge/verify/enrol, e-mail verification, Google policy, welcome/verification mail, forced reset clearing |
| `engine/desk_engine/routes/public.py` | Google sign-in → MFA step redirect |
| `engine/desk_engine/routes/workspaces.py` | transactional workspace limit, member limit, first-workspace alert |
| `engine/desk_engine/routes/{threads,close,analysis,workflows,mcp,connections}.py` | feature entitlement gates |
| `engine/desk_engine/knowledge/base.py`, `files/service.py` | storage quota checks |
| `engine/desk_engine/__main__.py` | `owner grant|revoke|list` |
| `engine/tests/{owner_base,stripe_mock,test_owner_access,test_owner_billing,test_owner_comms}.py`, `test_backup.py` | tests |

**UI (React/TypeScript)**

| Path | Change |
|---|---|
| `ui/src/owner/*.tsx` | the dashboard: shell, Overview, Users, Workspaces, Plans/Subscriptions, Billing/Coupons, Growth (Analytics/Leads), Comms (Notifications/Email), Security (Security/Integrations/Storage/Audit/Settings), shared widgets |
| `ui/src/pages/Pricing.tsx`, `pages/AccountSecurity.tsx` | pricing/Contact Sales/Plan tab; MFA, forced password change, notifications, e-mail verification |
| `ui/src/main.tsx`, `lib/app.tsx`, `pages/Auth.tsx`, `pages/Settings.tsx`, `styles.css` | routing (`#/owner`, `#/pricing`, `#/verify-email`), gates, MFA sign-in step, Plan tab, account sections |
| `ui/src/i18n/{ar,es,fr}.ts` | 618 new strings each |

**Other**: `e2e/owner_browser.py`, `.github/workflows/build.yml` (browser step), `server/.env.example`,
`landing/site/assets/analytics.js` + `index.html`, `docs/owner/*`, `CHANGELOG.md`, `README.md`, `docs/TESTING.md`.

## Applying to a fork or an older checkout

```bash
git fetch origin && git merge <commit>        # or cherry-pick the single commit
cd engine && pip install -r requirements.txt && python -m unittest discover -s tests -t .
cd ../ui && npm ci && npx tsc --noEmit && npm run i18n:check && npm test && node build.mjs
```

No new runtime dependencies were added to the engine or the UI. The migration applies on first start.

## Configuration summary

See `docs/owner/DEPLOYMENT.md`: `DESK_HOSTED`, `DESK_PLATFORM_OWNERS`, then Stripe / Google / SMTP / analytics /
security in the dashboard. Nothing is live until configured; tests run against mocks.
