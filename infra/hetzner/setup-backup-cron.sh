#!/usr/bin/env bash
# One-time setup: install daily Redis backup as a root cron job.
# Run as root on the Hetzner server: bash infra/hetzner/setup-backup-cron.sh

set -euo pipefail

SCRIPT_SRC="$(cd "$(dirname "$0")" && pwd)/redis-backup.sh"
SCRIPT_DEST=/usr/local/bin/statiq-redis-backup
BACKUP_DIR=/opt/statiq/backups/redis

install -m 0755 "$SCRIPT_SRC" "$SCRIPT_DEST"
mkdir -p "$BACKUP_DIR"

CRON_FILE=/etc/cron.d/statiq-redis-backup
cat > "$CRON_FILE" <<EOF
# Statiq Redis daily backup — runs at 03:00 UTC
0 3 * * * root REDIS_CONTAINER=statiq-redis-1 BACKUP_DIR=$BACKUP_DIR $SCRIPT_DEST >> /var/log/statiq-redis-backup.log 2>&1
EOF
chmod 0644 "$CRON_FILE"

echo "Cron job installed: $CRON_FILE"
echo "Backups will run daily at 03:00 UTC → $BACKUP_DIR"
echo "Logs: /var/log/statiq-redis-backup.log"
echo ""
echo "Test now with: $SCRIPT_DEST"
