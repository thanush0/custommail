#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
BACKUP_DIR=/data/backups/custommail
mkdir -p "$BACKUP_DIR"
chmod 700 "$BACKUP_DIR"
# Keep secrets out of the source archive; preserve .env separately with mode 600.
tar --exclude='./.env' --exclude='./.venv' --exclude='./__pycache__' --exclude='./.pytest_cache' -czf "$BACKUP_DIR/source-$STAMP.tar.gz" .
if [ -f .env ]; then cp -p .env "$BACKUP_DIR/env-$STAMP"; chmod 600 "$BACKUP_DIR/env-$STAMP"; fi
if docker compose ps --services --filter status=running | grep -qx db; then
  docker compose exec -T db sh -lc 'pg_dump -U "$POSTGRES_USER" "$POSTGRES_DB"' > "$BACKUP_DIR/database-$STAMP.sql"
  chmod 600 "$BACKUP_DIR/database-$STAMP.sql"
else
  echo 'Database container is not running; source backup completed, database dump skipped.' >&2
fi
if [ -d /data/custommail/attachments ]; then
  tar -czf "$BACKUP_DIR/attachments-$STAMP.tar.gz" -C /data/custommail attachments
  chmod 600 "$BACKUP_DIR/attachments-$STAMP.tar.gz"
fi
echo "Backup set created under $BACKUP_DIR (timestamp $STAMP)"
