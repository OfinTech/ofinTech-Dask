# Owner Control Center — security review

## Principles

1. **Platform administration is not customer-data access.** No owner route returns a workspace's ledgers, datasets,
   documents, files, conversations or credentials. The dashboard sees accounts, usage counts, states and security
   events. The only bridge is *support access*: the viewer role on one workspace, for 1–72 hours, with a reason,
   audited against that workspace and visible to its own administrators; writes are refused (`support_read_only`).
2. **Deny by default.** Every `/api/owner/*` route names a permission (`platform/owner.py: PERMISSIONS`); an account
   without a platform role gets 403 `owner_required`, a delegated administrator gets 403 on billing credentials, plans,
   security policy and delegation. The desktop app has no owner area at all (404).
3. **No public owner registration.** Platform roles come from `DESK_PLATFORM_OWNERS`, `python -m desk_engine owner
   grant`, or an existing owner's delegation (step-up, optional dual approval). Nothing in the sign-up form or the
   profile API can set one (`test_owner_role_cannot_be_spoofed_or_self_assigned`).
4. **Step-up for every mutation.** All owner mutations are `fresh=True`: the password must have been re-entered within
   `DESK_REAUTH_MINUTES` on this session. Critical changes (billing keys, plan prices/limits, MFA/session policy,
   delegation) can additionally require a **second owner's approval** (`security.dual_approval`); the requester cannot
   approve their own change.
5. **Secrets are write-only.** Stripe keys, the webhook secret, the SMTP password, the Google client secret and the
   notification webhook secret live in the encrypted `secrets` table (AES-GCM under `DESK_MASTER_KEY`). Responses
   carry only `configured` and the last four characters; the audit log records `last4` only
   (`test_secrets_are_write_only_and_settings_are_validated_and_audited`).
6. **Every privileged operation is audited** through the existing immutable audit log (`platform.*`, `plan.*`,
   `subscription.*`, `coupon.*`, `user.suspend`, `workspace.state`, `support.access.*`, `billing.*`, `email.*`,
   `announcement.*`, `sales.lead.*`), with actor, target, decision and result.
7. **Feature flags cannot weaken security.** The settings registry has no key for authentication, tenant isolation,
   audit or egress; `features.flags` only switches optional product features off.
8. **Settings can narrow, never loosen.** Session limits set from the dashboard are clamped to the engine's own
   ranges (idle 30–45 min, lifetime 1–24 h); a value outside is refused with 422.

## Billing

* Stripe is the authority. The browser only starts a Checkout or opens the Portal; local subscription state follows
  Stripe's **signed** webhooks (`Stripe-Signature`, HMAC-SHA256, 5-minute tolerance) or an explicit reconciliation.
* Events are stored by id before processing (duplicates → 200, ignored), processed idempotently, retried on failure,
  parked as dead-letter after five attempts with an owner alert, and ignored when older than the last applied event
  for the same subscription (out-of-order delivery).
* Card data never reaches the server (Stripe-hosted pages). Test keys are refused in live mode and vice versa.
* A failed payment puts the subscription in `past_due` with a grace period; when it runs out the account returns to
  the default plan and workspaces beyond the limit become **read-only — nothing is deleted**.

## Accounts

* Suspension ends every session and blocks sign-in with the reason; reactivation restores.
* A forced password change gates every request except the change itself (`password_reset_required`); a reset link is
  e-mailed, never shown to the administrator when e-mail works.
* Two-factor authentication: TOTP (RFC 6238) from the standard library; enrolment confirmed by a first valid code;
  sign-in answers a correct password with a 5-minute single-use challenge (max 5 attempts) instead of a session; policy
  `none | admins | all` plus a per-account requirement gate everything but enrolment (`mfa_enrollment_required`); the
  owner can reset a lost authenticator (sessions end).
* Account lockout and the failed-sign-in alert threshold are owner-configurable; repeated failures alert the owner and
  warn the person once.
* Google sign-in: Authorization Code + PKCE + state (existing); the owner controls enablement, allowed domains,
  registration policy and whether a *verified* Google address may claim an existing account (`never` forces a password
  sign-in first). Accounts are never merged on an unverified claim.

## Workspaces

* States `read_only | suspended | archived` are enforced in `load_workspace` for every `/api/w/{ws}` route — no path
  around them. Deletion requires `archived` first, is owner-only, step-up protected and audited.
* Limits are enforced inside the creating transaction (concurrency-safe), quotas before storing, members at
  invitation, features and AI allowance per request — never only in the page.

## Analytics and privacy

* No IP addresses, names, e-mails, prompts, answers, documents or figures are stored. Visitor hashes are salted per
  day and cannot be reversed; the referrer keeps only its host; consent is required by default; bots are dropped;
  retention is enforced. Product events carry a name, account id, workspace id and time only.

## Residual risks and limitations (honest list)

| Item | State |
|---|---|
| Live Stripe, SMTP and Google configurations | **Not exercised live** in CI (no credentials). The adapters run against a mock Stripe and a fake mailer through the real code paths; live configuration is the operator's verification step. |
| SSO (SAML/OIDC) | Not implemented; marked *planned* on the pricing page. |
| Provider-native e-mail adapters (SES/SendGrid/Resend API) | Not implemented; they work as SMTP relays. |
| Bounce/complaint ingestion | Manual suppression list only; no provider webhook for bounces. |
| Audit retention | Recorded as policy; deletion stays with the operator's archival job (immutability kept). |
| Over-quota "grace" policy | The quota refuses new uploads at once; the grace variant is a setting reflected in messaging, not a delayed lock-out. |
| Dual approval | Covers the four critical kinds; other owner actions are single-approver with step-up. |
| Malware scanning | Integration point only (uploads keep the existing type/size validation and encryption at rest). |
| Rate limiting | In-process (per engine instance); a multi-instance deployment needs limits at the proxy. |
