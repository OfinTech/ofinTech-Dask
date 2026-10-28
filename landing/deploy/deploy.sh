#!/bin/sh
# Brings the checkout up to date with the deploy branch and rebuilds only the website container.
# Run by webhook.py; can also be run by hand:  REPO_DIR=/opt/ofintech-site landing/deploy/deploy.sh
#
# REPO_DIR must be a checkout used only for the website: the script resets it to the remote branch, which would
# throw away local edits (for example a Team Server's Caddyfile) in a checkout that also runs the Team Server.
set -eu
: "${REPO_DIR:?set REPO_DIR to the website's own checkout of the repository}"
BRANCH="${DEPLOY_BRANCH:-main}"
SITE_DIR="${SITE_DIR:-landing}"
COMPOSE_FILES="${COMPOSE_FILES:-docker-compose.yml}"

cd "$REPO_DIR"
if [ -f server/.env ]; then
  echo "refusing to deploy: $REPO_DIR has server/.env, so it also runs a Team Server. Use a separate checkout." >&2
  exit 2
fi
git config --global --add safe.directory "$REPO_DIR" 2>/dev/null || true
git fetch --depth 1 origin "$BRANCH"
git reset --hard "origin/$BRANCH"
echo "now at $(git log -1 --format='%h %s')"

cd "$SITE_DIR"
set --
for f in $COMPOSE_FILES; do set -- "$@" -f "$f"; done
docker compose "$@" up -d --build --no-deps landing
docker image prune -f >/dev/null
echo "website rebuilt"
