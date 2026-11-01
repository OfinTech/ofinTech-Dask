# Owner Control Center — API

All owner routes need a signed-in platform owner or administrator; the permission each needs is in
`engine/desk_engine/platform/owner.py`. Every mutation requires recent authentication (`fresh=True` → 403
`reauth_required` until `POST /api/auth/reauth`). Pagination: `limit`, `offset`. Date ranges: `period=today|yesterday|
7d|30d|90d` or `from`/`to` (Unix seconds).

## Overview and health
| Method | Path | Permission |
|---|---|---|
| GET | `/api/owner/overview?period=` | dashboard.read |
| GET | `/api/owner/health` | dashboard.read |

## Users
| GET | `/api/owner/users?q=&status=&plan=&mfa=&verified=` | users.read |
| GET | `/api/owner/users/{uid}` | users.read |
| POST | `/api/owner/users/{uid}/suspend` `{reason}` · `/reactivate` · `/sign-out` · `/require-password-reset` · `/reset-mfa` | users.manage |
| PUT | `/api/owner/users/{uid}/restrictions` `{mfa_required?, read_only?, no_new_workspaces?}` | users.manage |
| PUT | `/api/owner/users/{uid}/entitlements` `{overrides, note?, expires_at?}` | subscriptions.manage |
| POST | `/api/owner/users/{uid}/subscription` `{plan_id, interval?, months?, note?}` | subscriptions.manage |

## Workspaces
| GET | `/api/owner/workspaces?q=&state=` · `/api/owner/workspaces/{wid}` | workspaces.read |
| POST | `/api/owner/workspaces/{wid}/state` `{status: active|read_only|suspended|archived, reason?}` | workspaces.manage |
| PATCH / DELETE | `/api/owner/workspaces/{wid}/members/{uid}` | workspaces.manage |
| DELETE | `/api/owner/workspaces/{wid}` (archived first) | workspaces.delete |
| POST | `/api/owner/workspaces/{wid}/support-access` `{reason, hours}` · GET `/api/owner/support-access` · DELETE `/api/owner/support-access/{gid}` | support.access |

## Subscriptions, plans, billing, coupons
| GET | `/api/owner/subscriptions?status=&plan=&q=` · `/api/owner/invoices` | subscriptions.read / billing.read |
| POST | `/api/owner/subscriptions/{sid}/cancel[?now=1]` · `/change` `{plan_id}` · `/reconcile` | subscriptions.manage |
| GET / POST | `/api/owner/plans` · PATCH `/api/owner/plans/{pid}` · POST `/api/owner/plans/{pid}/sync-stripe` | plans.read / plans.manage |
| GET / PUT | `/api/owner/billing` `{enabled, mode, publishable_key, secret_key, webhook_secret, currencies, portal_enabled, tax_enabled, grace_days, allow_promotion_codes}` | billing.read / billing.configure |
| POST | `/api/owner/billing/test` · GET `/api/owner/billing/events` · POST `/api/owner/billing/events/{eid}/retry` | billing.configure |
| GET / POST | `/api/owner/coupons` · PATCH `/api/owner/coupons/{cid}` · POST `/sync-stripe` · GET `/redemptions` | coupons.read / coupons.manage |

## Sales, analytics, notifications, e-mail
| GET | `/api/owner/leads?status=&q=&assigned_to=` · `/api/owner/leads/{lid}` | sales.read |
| PATCH | `/api/owner/leads/{lid}` `{status, assigned_to, follow_up_at, converted_user_id, clear_*}` · POST `/notes` | sales.manage |
| GET | `/api/owner/analytics/kpis` · `/api/owner/analytics/series/{metric}?bucket=day|week|month` · `/api/owner/analytics/definitions` | analytics.read |
| GET | `/api/owner/export/{kpis|users|subscriptions|leads|coupons|traffic|features|revenue|…}.csv` | analytics.export |
| GET / POST | `/api/owner/announcements` · POST `/preview` · `/{aid}/send` · `/{aid}/cancel` · GET `/{aid}/deliveries` | notifications.* |
| GET / PUT | `/api/owner/alerts` `{rules, webhook_url, webhook_secret}` | notifications.* |
| GET / PUT | `/api/owner/email` · POST `/api/owner/email/test` · templates `PUT/DELETE /api/owner/email/templates/{key}/{lang}` · `GET …/preview` · jobs `/api/owner/email/jobs[/{jid}/retry]` · suppressions | email.* |

## Security, integrations, storage, audit, settings
| GET / PUT | `/api/owner/security` `{mfa_policy, dual_approval, lockout_threshold, lockout_minutes, alert_failed_logins, session_idle_minutes, session_hours, admin_session_hours, audit_retention_days, api_key_max_age_days, oauth_providers, registration_*}` | security.read / security.configure |
| GET | `/api/owner/sessions` | security.read |
| POST / DELETE | `/api/owner/admins` `{email, role}` · `/api/owner/admins/{uid}` | admins.manage |
| GET / POST | `/api/owner/changes` · `/api/owner/changes/{cid}/decide` `{approve}` | security.read / admins.manage |
| GET / PUT | `/api/owner/integrations` · `/api/owner/integrations/google` · `/api/owner/integrations/analytics` | integrations.* |
| GET / PUT | `/api/owner/storage` | storage.* |
| GET | `/api/owner/audit?action=&user_id=&workspace_id=&decision=&result=&request_id=&from=&to=` | audit.read |
| GET / PUT | `/api/owner/settings` `{values: {key: value}}` (general and feature groups) | settings.* |

## Public and user-facing platform routes
| GET | `/api/plans` | public: active public plans, Enterprise capability states |
| GET | `/api/me/plan` · `/api/me/entitlements` | signed in |
| POST | `/api/billing/checkout` · `/portal` · `/cancel` (fresh) · `/change` (fresh) · `/coupon-check` | signed in |
| POST | `/api/billing/webhook` | Stripe (signature) |
| POST | `/api/sales/contact` | public, rate-limited, honeypot field `website` |
| POST | `/api/analytics/collect` · GET `/api/analytics/policy` | public, consent-gated |
| GET / POST | `/api/me/notifications` · `/read` · GET/PUT `/api/me/notification-preferences` | signed in |
| POST | `/api/auth/mfa/begin` · `/confirm` · `/disable` (signed in) · `/verify` (challenge) | |
| GET / POST | `/api/auth/verify-email/{token}` · `/api/auth/verify-email/resend` | |

Error codes introduced: `owner_required`, `reauth_required`, `workspace_limit`, `member_limit`, `storage_quota`,
`feature_not_in_plan`, `feature_disabled`, `ai_allowance`, `workspace_read_only|suspended|archived`,
`support_read_only`, `account_read_only`, `account_restricted`, `password_reset_required`,
`mfa_enrollment_required`, `mfa_required`, `bad_code`, `mfa_expired`, `maintenance` (503), `billing_off`,
`billing_unconfigured`, `stripe_mode_mismatch`, `plan_not_synced`, `already_subscribed`, `coupon_*`, `bad_signature`,
`second_owner`, `immutable`, `archive_first`, `domain_not_allowed`.
