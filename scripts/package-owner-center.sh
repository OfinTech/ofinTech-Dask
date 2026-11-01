#!/usr/bin/env bash
# Builds OfinTech-Desk-Owner-Control-Center.zip: the complete repository source at the current commit (engine, UI,
# desktop, server, docs, tests, migrations, configuration templates), with the Owner Control Center docs at the top
# level. Only tracked files are included, so no secrets, data, caches or build artefacts can slip in; the archive is
# verified with `unzip -t` and its commit is written into the manifest.
set -euo pipefail
cd "$(dirname "$0")/.."
OUT="$(realpath -m "${1:-OfinTech-Desk-Owner-Control-Center.zip}")"
NAME="OfinTech-Desk-Owner-Control-Center"
SHA="$(git rev-parse HEAD)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
git archive --format=tar --prefix="$NAME/" HEAD | tar -x -C "$TMP"
# a top-level README for the archive and the integration documents beside the source
cp docs/owner/INTEGRATION-GUIDE.md "$TMP/$NAME/INTEGRATION-GUIDE.md"
cat > "$TMP/$NAME/OWNER-CONTROL-CENTER.md" <<EOF
# OfinTech Desk — Owner Control Center (source archive)

Repository: https://github.com/OfinTech/ofinTech-Dask
Commit: $SHA
Built: $(date -u +%Y-%m-%dT%H:%M:%SZ)

This archive is the complete source of OfinTech Desk at the commit above, which includes the Owner Control Center:

- engine/desk_engine/platform/        the platform services (owner authz, settings, plans, Stripe, billing, coupons, e-mail,
                                      notifications, analytics, sales, storage, MFA, support access)
- engine/desk_engine/routes/owner.py  /api/owner/*;  routes/platform.py  public and user-facing platform routes
- engine/desk_engine/db.py            migration 21 (additive)
- engine/tests/test_owner_*.py        37 engine tests;  tests/stripe_mock.py  the Stripe sandbox
- ui/src/owner/                       the dashboard;  ui/src/pages/Pricing.tsx, AccountSecurity.tsx
- e2e/owner_browser.py                Playwright run against a real hosted engine
- docs/owner/                         ARCHITECTURE, SECURITY, BILLING, API, DEPLOYMENT, INTEGRATION-GUIDE, TEST-RESULTS
- server/.env.example                 configuration template (no secrets)

Install and build: see INTEGRATION-GUIDE.md and docs/DEPLOYMENT.md. Nothing in this archive contains keys, tokens,
production data or customer data; external integrations are off until configured in the dashboard.
EOF
rm -f "$OUT"
(cd "$TMP" && zip -qr -X "$OUT" "$NAME")
unzip -tq "$OUT"
echo "wrote $OUT ($(du -h "$OUT" | cut -f1)) from $SHA"
