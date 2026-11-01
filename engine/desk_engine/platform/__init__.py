"""The Owner Control Center: platform administration for the people who run an OfinTech Desk installation.

Platform administration is separate from workspace roles. A platform owner manages accounts, plans, billing,
integrations and policies; they do not thereby read any workspace's ledgers, files, conversations or credentials.
The only route into a workspace's data is a time-limited, audited support grant (``support_access``), and even that
gives the viewer role, never a write.

Modules:

* ``owner``          — who is a platform owner or delegated administrator, and the deny-by-default permission check
* ``settings``       — validated platform settings (general, integrations, policies, feature flags) with audit
* ``plans``          — subscription plans, entitlements and their server-side enforcement (hosted edition only)
* ``stripe``         — a small Stripe REST client and webhook signature verification
* ``billing``        — checkout, customer portal, idempotent webhook processing, reconciliation, invoices
* ``coupons``        — discount coupons mirrored to Stripe and validated here
* ``email``          — e-mail settings, bilingual templates, a durable queue with retries
* ``notifications``  — announcements, platform-level notifications, preferences, owner alerts
* ``analytics``      — privacy-preserving website and product analytics
* ``sales``          — Contact Sales leads
* ``storage``        — per-workspace storage usage and quota enforcement
* ``mfa``            — TOTP two-factor authentication (RFC 6238) without new dependencies
* ``support``        — workspace state (suspended, archived, read-only) and support access
"""
