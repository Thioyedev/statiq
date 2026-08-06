#!/usr/bin/env bash
# Daily Redis backup — copy RDB snapshot to /opt/statiq/backups/redis/
# Install the cron job with: bash infra/hetzner/setup-backup-cron.sh

set -euo pipefail

CONTAINER=${REDIS_CONTAINER:-statiq-redis-1}
BACKUP_DIR=${BACKUP_DIR:-/opt/statiq/backups/redis}
KEEP_DAYS=${KEEP_DAYS:-7}

mkdir -p "$BACKUP_DIR"

# Trigger background save and wait for it to finish
docker exec "$CONTAINER" redis-cli BGSAVE
PREV=$(docker exec "$CONTAINER" redis-cli LASTSAVE)
for i in $(seq 1 30); do
  sleep 1
  NOW=$(docker exec "$CONTAINER" redis-cli LASTSAVE)
  [ "$NOW" != "$PREV" ] && break
  [ "$i" -eq 30 ] && echo "WARNING: BGSAVE may not have completed" && break
done

DEST="$BACKUP_DIR/dump-$(date +%Y%m%d-%H%M%S).rdb"
docker cp "$CONTAINER:/data/dump.rdb" "$DEST"
echo "Backup saved: $DEST"

find "$BACKUP_DIR" -name "dump-*.rdb" -mtime +"$KEEP_DAYS" -delete
echo "Pruned backups older than ${KEEP_DAYS} days"
