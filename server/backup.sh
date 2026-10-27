#!/usr/bin/env bash
# Consistent online backup of the Team Server database into ./backups/<UTC timestamp>.sqlite3
set -euo pipefail
cd "$(dirname "$0")"
mkdir -p backups
stamp=$(date -u +%Y%m%dT%H%M%SZ)
docker compose exec -T engine python -m desk_engine backup "/var/lib/ofintech-desk/backup-$stamp.sqlite3"
docker compose cp "engine:/var/lib/ofintech-desk/backup-$stamp.sqlite3" "backups/$stamp.sqlite3"
docker compose exec -T engine rm -f "/var/lib/ofintech-desk/backup-$stamp.sqlite3"
sha256sum "backups/$stamp.sqlite3" > "backups/$stamp.sqlite3.sha256"
echo "backups/$stamp.sqlite3 (keep DESK_MASTER_KEY with it, stored separately)"
