# Owner Control Center — deployment and configuration

The Owner Control Center ships inside the existing engine and UI; there is nothing extra to deploy. This page covers
what is new. The general Team Server setup is in `docs/DEPLOYMENT.md`.

## 1. Database migration

Migration 21 runs automatically at start (`PRAGMA user_version` 20 → 21). It only adds tables and nullable columns.
Before upgrading a production server take an encrypted backup:

```bash
python -m desk_engine backup /backups/pre-owner-center --key-file /secure/backup.key
```

Rollback = stop the engine, restore that backup (`python -m desk_engine restore … --overwrite`), start the previous
release. No data written by the previous release is changed by the migration.

## 2. Environment

```ini
DESK_HOSTED=false            # true only on the hosted SaaS service: plans, quotas and billing apply
DESK_PLATFORM_OWNERS=        # owner@example.com,second@example.com — provisioned at start-up / sign-up
```

Everything else (Stripe, SMTP, Google OAuth, analytics, storage policy, security policy, branding) is configured in
the dashboard and stored encrypted under `DESK_MASTER_KEY`. External integrations are **off** until configured.

## 3. Secure owner bootstrap

1. Set `DESK_PLATFORM_OWNERS` to the owner's address(es) and start the engine.
2. The owner signs up (by invitation or, on a fresh server, as the first account). The role is applied at sign-up or,
   for an existing account, at the next start. Alternatively on the server:
   ```bash
   python -m desk_engine owner grant owner@example.com          # or --role admin
   python -m desk_engine owner list
   python -m desk_engine owner revoke someone@example.com
   ```
3. Open `https://<server>/#/owner`. Turn on **Security Center → Dual approval** once a second owner exists.

There is no public way to obtain a platform role. If `DESK_PLATFORM_OWNERS` is not set, the first account on the
server (the instance administrator) acts as owner for compatibility; the Security Center shows a warning.

## 4. Stripe

1. Billing → mode **test**, paste the publishable key, the secret key (`sk_test_…`) and the webhook signing secret;
   **Test connection**.
2. Plans & Features → open Pro (and any paid plan) → **Sync to Stripe** (creates product and prices).
3. In the Stripe dashboard add a webhook endpoint for the URL shown on the Billing page with the events listed there.
4. Switch to **live** with live keys only when the test flow has been exercised end to end; a mismatch between mode
   and key type is refused.

## 5. Google OAuth

Integrations → Google: client ID and secret from Google Cloud (OAuth client, web application) with the redirect URI
shown on the page (`<public URL>/oauth/callback`). Choose the registration policy, allowed domains and whether a
verified Google address may claim an existing account.

## 6. E-mail

Email Settings → SMTP host, port, security, username, password, sender. **Send test**. Templates (EN/AR) are
editable; the queue retries with back-off and parks failures for review. For Amazon SES, SendGrid or Resend use their
SMTP relay credentials.

## 7. Analytics

Integrations → Analytics: collection on/off, consent requirement (default on), country from the proxy header
(`CF-IPCountry` or `X-Country`; set it at your reverse proxy if wanted), retention. On the public website include
`landing/site/assets/analytics.js` with `data-engine="https://<server>"`; it shows a consent banner and sends events
only after "Allow". Document the policy at `/api/analytics/policy`.

## 8. Storage

The provider stays an environment setting (`DESK_STORAGE=local|s3`, `DESK_S3_*`, encryption on). Quotas come from
plans; the over-quota policy, retention after deletion and the maximum file size are in **Storage**.

## 9. Backups and recovery

Everything the Owner Control Center stores lives in the same SQLite database and secrets table as the rest of the
engine, so the existing `backup.sh` / `python -m desk_engine backup` cover it. Stripe remains the source of truth for
payments: after a restore, **Reconcile** any subscription that changed in the meantime.

## 10. Verification after deployment

* `GET /health` → ok; `GET /api/session` shows `hosted` and `platform`.
* Sign in as the owner → `#/owner` opens; an ordinary account gets 403 on `/api/owner/overview`.
* Hosted: a Free account's second `POST /api/workspaces` answers 403 `workspace_limit`.
* Billing → Test connection ok; a Stripe test-mode checkout provisions Pro after the webhook arrives.
* Email → Send test arrives.
