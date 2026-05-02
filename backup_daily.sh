#!/bin/bash
set -euo pipefail

# Daily backup for Algovibe persistent data.
# - Creates a consistent SQLite snapshot using sqlite3 .backup
# - Archives DB snapshot + env + selected JSON/state files
# - Deletes backups older than retention days

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
DATA_DIR="$ROOT_DIR/data"
BACKUP_DIR="$DATA_DIR/backups"
TMP_DIR="$BACKUP_DIR/.tmp"
DB_PATH="$DATA_DIR/algotrade.db"
RETENTION_DAYS="${BACKUP_RETENTION_DAYS:-14}"
STAMP="$(date +%F_%H%M%S)"
RUN_DIR="$TMP_DIR/$STAMP"
ARCHIVE_PATH="$BACKUP_DIR/algo_backup_${STAMP}.tar.gz"

mkdir -p "$BACKUP_DIR" "$RUN_DIR"

if [[ ! -f "$DB_PATH" ]]; then
  echo "[backup] ERROR: DB not found at $DB_PATH"
  exit 1
fi

if ! command -v sqlite3 >/dev/null 2>&1; then
  echo "[backup] ERROR: sqlite3 is required but not installed"
  exit 1
fi

# Consistent snapshot even when WAL is active.
sqlite3 "$DB_PATH" ".timeout 5000" ".backup '$RUN_DIR/algotrade.db'"

# Include useful state files if present.
for f in \
  "$ROOT_DIR/.env" \
  "$DATA_DIR/algotrade.db-wal" \
  "$DATA_DIR/algotrade.db-shm" \
  "$DATA_DIR/indodax_execution_log.json" \
  "$DATA_DIR/indodax_exit_log.json" \
  "$DATA_DIR/daily_performance.json"; do
  if [[ -f "$f" ]]; then
    cp -a "$f" "$RUN_DIR/"
  fi
done

tar -czf "$ARCHIVE_PATH" -C "$RUN_DIR" .
rm -rf "$RUN_DIR"

# Retention cleanup.
find "$BACKUP_DIR" -maxdepth 1 -type f -name 'algo_backup_*.tar.gz' -mtime "+$RETENTION_DAYS" -delete

# Keep tmp root clean if empty.
rmdir "$TMP_DIR" 2>/dev/null || true

echo "[backup] Created: $ARCHIVE_PATH"
