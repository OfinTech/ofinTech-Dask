# Owner Control Center — billing, plans and coupons

## Plans

Three plans are seeded on first start and edited from **Plans & Features** (no code change):

| Code | Default | Workspaces | Storage | Members | Trial | Price |
|---|---|---|---|---|---|---|
| `free` | yes | 1 | 250 MB | 5 | — | 0 |
| `pro` | | 2 | 10 GB (`storage_quota_mb = 10240`, an example value) | 25 | 14 days | 29.00 / 290.00 USD |
| `enterprise` | | custom | custom | custom | — | Contact Sales |

Fields: code, name, description, monthly/annual price (minor units), currency, workspace limit, storage quota,
member limit, feature entitlements, AI token allowance, trial days, billing interval, visibility, Contact Sales
mode, availability (`draft | active | archived`), default flag, position, Stripe product/price ids.

Entitlements are resolved per **owning account** (the `owner` member of a workspace): active subscription → plan;
none → the default plan; owner **overrides** (`PUT /api/owner/users/{id}/entitlements`) on top. Platform
administrators are unlimited; the self-hosted edition is unlimited.

## Plan changes

* Upgrade: applied at once (Stripe: price change with prorations).
* Downgrade: scheduled for the period end (Stripe: price change without prorations at the customer's request through
  the API; owner-assigned: `scheduled_plan_id` applied by the scheduler).
* Cancellation: at period end by default; `?now=1` for the owner.
* Trial conversion: Stripe moves `trialing → active`; a local trial that ends without payment is cancelled by the
  scheduler.
* Payment failure: `past_due` + grace (`billing.grace_days`, default 7); a paid invoice restores `active`.
* **Over-quota policy**: workspaces beyond the new limit become `read_only` (reason `over_plan_limit`), oldest kept
  writable; restored automatically when the limit allows again. Files are never deleted by a plan change; a full
  storage quota refuses new uploads only.

## Stripe

Owner settings (Billing): enabled, test/live, publishable key, secret key (write-only), webhook signing secret
(write-only), currencies, customer portal, Stripe Tax, promotion codes, grace days. **Test connection** calls
`GET /v1/balance`. Plans are pushed with **Sync to Stripe** (product + monthly/annual prices with idempotency keys).

Register the webhook endpoint shown on the Billing page (`/api/billing/webhook`) for:
`checkout.session.completed`, `customer.subscription.created|updated|deleted|trial_will_end`, `invoice.paid`,
`invoice.payment_succeeded`, `invoice.payment_failed`, `charge.refunded`.

Processing guarantees: signature verified (5-minute tolerance), event id persisted first (duplicates and replays
answered 200 and ignored), per-event try/except with retry by the scheduler and dead-letter after 5 attempts
(owner alert), out-of-order events ignored by `created` timestamp, no provisioning from `incomplete` subscriptions,
one Stripe subscription per account (an owner-assigned one gives way). **Reconcile** pulls the subscription from
Stripe and applies it regardless of what the webhooks delivered.

Customer side: `POST /api/billing/checkout` (plan, interval, optional coupon) → Stripe Checkout URL;
`POST /api/billing/portal` → Customer Portal; `POST /api/billing/cancel` and `/change` (step-up); `GET /api/me/plan`
shows plan, entitlements, usage against the AI allowance, invoices.

## Coupons

Created in **Coupons**, mirrored to Stripe as a coupon plus a promotion code (same code). As in Stripe, the discount
(percentage or fixed amount + currency), duration (`once | repeating N months | forever`) and currency are immutable
after creation; expiry, plan restrictions, per-customer limit, first-time rule, redemption limit and active state can
change. Validation (`POST /api/billing/coupon-check`) runs on the server: unknown, inactive, expired, exhausted,
wrong plan, wrong currency, below minimum, already used, not first-time. At checkout the promotion code is passed as a
Stripe `discount`, which excludes free-form codes (no stacking). Redemptions are recorded from the subscription's
discount on provisioning; disabling a coupon deactivates its promotion code in Stripe.

## Sandbox / tests

`engine/tests/stripe_mock.py` implements the Stripe endpoints the service uses and signs webhook events exactly as
Stripe does; `tests/test_owner_billing.py` and `tests/test_owner_comms.py` run the real client and webhook code
against it. No live Stripe account is used anywhere in the repository.
