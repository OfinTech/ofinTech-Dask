# Owner Control Center — architecture

The Owner Control Center is the platform-administration layer of OfinTech Desk: a protected area at `#/owner` for
the product owner and delegated administrators, backed by `/api/owner/*`. It extends the existing engine
(Starlette + SQLite, one code base for the desktop app and the Team Server) rather than adding a service, and it
reuses the existing authentication, step-up re-authentication, audit log, secrets store, mailer, storage abstraction
and React design system.

## Editions

| Edition | `DESK_HOSTED` | Plans and quotas | Billing | Owner Center |
|---|---|---|---|---|
| Open-source self-hosted Team Server | unset | none — every entitlement unlimited | off | available to the operator (users, security, e-mail, audit) |
| Hosted Free / Pro | `true` | enforced on the server | Stripe | full |
| Enterprise | `true` | owner-assigned entitlements, contract billing | owner-assigned subscription | full |
| Desktop app | — | none | none | none (404) |

The self-hosted edition keeps every right the Apache-2.0 licence grants: nothing in this layer limits it.

## Package layout

```
engine/desk_engine/platform/        the Owner Control Center's services
  owner.py          platform roles (owner, admin), deny-by-default permissions, bootstrap, dual approval
  settings.py       validated settings registry; encrypted secrets (write-only); runtime session overrides
  plans.py          plans, subscriptions (local lifecycle), entitlements, transactional enforcement
  stripe.py         Stripe REST client (no SDK), idempotency keys, webhook signature verification
  billing.py        checkout, portal, idempotent webhook processing, reconciliation, invoices, MRR
  coupons.py        coupons mirrored to Stripe (coupon + promotion code), server-side validation
  email.py          SMTP settings, EN/AR templates, durable queue with retries, suppression
  notifications.py  announcements, platform notifications, preferences, owner alerts, webhook channel
  analytics.py      privacy-preserving web + product events, KPI and series computation, CSV export
  sales.py          Contact Sales leads, notes, follow-ups
  storage.py        per-workspace usage, quota enforcement
  mfa.py            TOTP (RFC 6238) enrolment, challenge, policy gate
  support.py        workspace state (read-only / suspended / archived) and time-limited support access
engine/desk_engine/routes/owner.py      /api/owner/*  (every mutation: fresh=True)
engine/desk_engine/routes/platform.py   /api/plans, /api/me/plan, /api/billing/*, /api/sales/contact,
                                        /api/analytics/collect, /api/me/notifications*
ui/src/owner/                           the dashboard (16 sections) — OwnerApp.tsx and one file per area
ui/src/pages/Pricing.tsx                pricing page, Contact Sales form, Settings → Plan
ui/src/pages/AccountSecurity.tsx        MFA enrolment, forced password change, notification preferences
e2e/owner_browser.py                    Playwright run against a real hosted engine
```

## Data model (migration 21, additive)

`platform_admins`, `platform_settings`, `plans`, `subscriptions`, `entitlement_overrides`, `billing_customers`,
`stripe_events`, `invoices`, `coupons`, `coupon_redemptions`, `email_templates`, `email_jobs`, `email_suppressions`,
`announcements`, `announcement_deliveries`, `notification_preferences`, `user_notifications`, `sales_leads`,
`lead_notes`, `analytics_events`, `workspace_state`, `support_access`, `owner_change_requests`,
`email_verifications`, `mfa_challenges`; new nullable columns on `users` (`email_verified_at`, `signup_method`,
`suspended_reason`, `must_reset_password`, `restrictions`, `mfa_secret_id`, `mfa_enabled_at`, `last_login_at`) and
`sessions.mfa_at`. Nothing existing is altered or dropped; rollback is a restore of the pre-migration backup
(`python -m desk_engine backup`) — the migration itself has no destructive step to undo.

## Request flow and enforcement points

```
browser ──► web.Router ──► authenticate() ──► account_gates()  (maintenance, MFA enrolment, forced reset, read-only)
                              │
                              ├─ /api/owner/*  ──► owner.require(ctx, permission)  (deny by default) ──► service ──► audit
                              │
                              └─ /api/w/{ws}/* ──► load_workspace()  (membership | support grant → viewer)
                                                     └─ support.enforce()  (read_only / suspended / archived)
                                                          └─ route handler ──► plans.require_feature() / check_quota() / limits
```

* Workspace limit: `plans.check_workspace_limit` runs **inside** the `BEGIN IMMEDIATE` transaction that inserts the
  workspace, so concurrent requests cannot both pass (tested with six parallel threads).
* Storage quota: `storage.check_quota` before any document or file is stored.
* Feature entitlements: `plans.require_feature` on the assistant (`ask_finance`), close preparation
  (`autonomous_reconciliation`), forecasts (`financial_intelligence`), workflows, MCP keys/servers (`api_access`),
  ERP connections; plus the global feature flags (`features.flags`).
* AI allowance: `plans.check_ai_allowance` on each assistant message against the owning account's monthly tokens.

## Analytics pipeline

* Website: `POST /api/analytics/collect` (consent required by default) → visitor hash `sha256(daily salt|addr|UA)[:24]`,
  device/browser class, referrer host, source, country from the proxy header (optional), language. No IPs stored.
* Product: `Engine.audit()` mirrors selected actions to `analytics_events(kind=platform)` with the event name, account,
  workspace and time only (`analytics.AUDIT_TO_EVENT`).
* Metrics are computed on read from these tables plus `users`, `sessions`, `workspaces`, `subscriptions`, `invoices`;
  every metric's definition is in `analytics.METRIC_DEFINITIONS` and exposed at `/api/owner/analytics/definitions`.

## Background work (`Engine._platform_tick`)

Every few seconds the scheduler delivers queued e-mail (exponential back-off, parked after `email.max_attempts`),
sends due announcements, runs the subscription lifecycle (`plans.tick`), retries failed Stripe events (dead-letter
after 5), reminds sales follow-ups and purges analytics past the retention window.

## Conflicts with the requested design, and what was done

| Requested | Existing behaviour | Resolution |
|---|---|---|
| Owner provisioning only through a secure bootstrap | The first account on a fresh Team Server is the *instance administrator* | Kept for compatibility; `DESK_PLATFORM_OWNERS` or `owner grant` makes ownership explicit, and when the list is set the instance administrator no longer implies ownership. The Security Center warns when the list is not set. |
| Separate settings store | `settings` table already holds OAuth app credentials | New `platform_settings` registry; Google credentials keep using `connectors.oauth` so existing connections keep working. |
| Email service with provider adapters | A single env-configured SMTP `Mailer` | The dashboard's SMTP settings take precedence over the environment; the queue wraps the same `Mailer`. SES/SendGrid/Resend are reachable as SMTP relays; native API adapters are not implemented. |
| Audit retention configuration | Audit rows are immutable (DB triggers) | The retention setting is recorded and shown; deletion is left to the operator's archival job, so the immutability guarantee is kept. |
| SSO/SAML/OIDC for Enterprise | Not present | Listed as **planned**, never advertised as operational. |
