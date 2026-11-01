#!/usr/bin/env bash
# Encrypted, consistent online backup of the Team Server database into ./backups/<UTC timestamp>.desk-backup.
#
# One-time setup:  python -m desk_engine keygen > backup.key   (on the machine that will KEEP the backups, not on
# the server), then set DESK_BACKUP_KEY_FILE in server/.env to a Docker secret or a root-only file that holds it.
# The backup key is not the master key: whoever holds backup files and the backup key can read ledgers and
# conversations but not the stored credentials (those need DESK_MASTER_KEY); keep the two keys in different places.
set -euo pipefail
cd "$(dirname "$0")"
mkdir -p backups
stamp=$(date -u +%Y%m%dT%H%M%SZ)
docker compose exec -T engine python -m desk_engine backup "/var/lib/ofintech-desk/backup-$stamp"
docker compose cp "engine:/var/lib/ofintech-desk/backup-$stamp.desk-backup" "backups/$stamp.desk-backup"
docker compose exec -T engine rm -f "/var/lib/ofintech-desk/backup-$stamp.desk-backup"
sha256sum "backups/$stamp.desk-backup" > "backups/$stamp.desk-backup.sha256"
chmod 600 "backups/$stamp.desk-backup"
echo "backups/$stamp.desk-backup (opens only with the backup key; test it: python -m desk_engine backup-check <file> --key-file backup.key)"
