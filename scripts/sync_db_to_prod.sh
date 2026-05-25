#!/usr/bin/env bash
# sync_db_to_prod.sh
# Sync local PostgreSQL/TimescaleDB ke production server (wiraworks-srv).
# Usage: ./scripts/sync_db_to_prod.sh [--dry-run]
#
# Langkah:
#   1. Dump 3 database dari local container
#   2. Copy dump ke production via scp
#   3. Stop bot services di production
#   4. Drop + recreate setiap DB di production
#   5. Restore dengan prosedur TimescaleDB (pre_restore → pg_restore → post_restore)
#   6. Restart bot services di production
#   7. Verifikasi row count

set -euo pipefail

# ─── Konfigurasi ──────────────────────────────────────────────────────────────
LOCAL_CONTAINER="algovibe-timescaledb"
PROD_SSH="wiraworks-srv"
PROD_CONTAINER="algovibe-timescaledb"
PROD_PROJECT="/root/algovibe"
DB_USER="algovibe"
DUMP_DIR="/tmp"
DATABASES=("algovibe" "algovibe_dryrun" "algovibe_live")
# Tabel yang diverifikasi per database (kosong = skip verify tabel tsb)
declare -A VERIFY_TABLE=(
    ["algovibe"]="ohlcv_1m"
    ["algovibe_dryrun"]="trade_history"
    ["algovibe_live"]="trade_history"
)
BOT_SERVICES="executive-core brain-inference market-pipe knn-worker exchange-reconcile"

DRY_RUN=false
[[ "${1:-}" == "--dry-run" ]] && DRY_RUN=true

# ─── Helpers ──────────────────────────────────────────────────────────────────
info()  { echo "[INFO]  $*"; }
warn()  { echo "[WARN]  $*"; }
error() { echo "[ERROR] $*" >&2; exit 1; }

run() {
    if $DRY_RUN; then
        echo "[DRY-RUN] $*"
    else
        eval "$@"
    fi
}

local_psql()  { docker exec "$LOCAL_CONTAINER" psql -U "$DB_USER" "$@"; }
prod_psql()   { ssh "$PROD_SSH" "docker exec $PROD_CONTAINER psql -U $DB_USER $*"; }
prod_exec()   { ssh "$PROD_SSH" "docker exec $PROD_CONTAINER $*"; }

# ─── Konfirmasi ───────────────────────────────────────────────────────────────
echo "=================================================="
echo "  SYNC LOCAL → PRODUCTION (${PROD_SSH})"
echo "  Database: ${DATABASES[*]}"
echo "  Mode: $( $DRY_RUN && echo DRY-RUN || echo LIVE )"
echo "=================================================="
if ! $DRY_RUN; then
    read -rp "Lanjutkan? Data production AKAN DITIMPA. [y/N] " confirm
    [[ "$confirm" =~ ^[Yy]$ ]] || { info "Dibatalkan."; exit 0; }
fi

# ─── Step 1: Dump local ───────────────────────────────────────────────────────
info "Step 1/7: Dump databases dari local container..."
for db in "${DATABASES[@]}"; do
    dump_file="${DUMP_DIR}/${db}.dump"
    info "  Dumping ${db} → ${dump_file}"
    run "docker exec $LOCAL_CONTAINER pg_dump -U $DB_USER -Fc $db > $dump_file"
    if ! $DRY_RUN; then
        size=$(du -sh "$dump_file" | cut -f1)
        info "  ${db}.dump → ${size}"
    fi
done

# ─── Step 2: Transfer ke production ──────────────────────────────────────────
info "Step 2/7: Transfer dump files ke ${PROD_SSH}:/tmp/ ..."
dump_files=()
for db in "${DATABASES[@]}"; do
    dump_files+=("${DUMP_DIR}/${db}.dump")
done
run "scp ${dump_files[*]} ${PROD_SSH}:${DUMP_DIR}/"

# ─── Step 3: Copy dump files ke dalam container ───────────────────────────────
info "Step 3/7: Copy dump files ke dalam container di production..."
for db in "${DATABASES[@]}"; do
    run "ssh $PROD_SSH 'docker cp ${DUMP_DIR}/${db}.dump ${PROD_CONTAINER}:${DUMP_DIR}/'"
done

# ─── Step 4: Stop bot services di production ──────────────────────────────────
info "Step 4/7: Stop bot services di production..."
run "ssh $PROD_SSH 'cd $PROD_PROJECT && docker compose stop $BOT_SERVICES'"

# ─── Step 5: Drop + Restore setiap database ───────────────────────────────────
info "Step 5/7: Drop, recreate, dan restore databases..."
for db in "${DATABASES[@]}"; do
    info "  → Processing: ${db}"
    dump_path="${DUMP_DIR}/${db}.dump"

    # Terminate koneksi aktif
    run "ssh $PROD_SSH \"docker exec $PROD_CONTAINER psql -U $DB_USER postgres -c \\\"SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname='${db}' AND pid <> pg_backend_pid();\\\"\""

    # Drop + recreate
    run "ssh $PROD_SSH 'docker exec $PROD_CONTAINER psql -U $DB_USER postgres -c \"DROP DATABASE IF EXISTS ${db}\"'"
    run "ssh $PROD_SSH 'docker exec $PROD_CONTAINER psql -U $DB_USER postgres -c \"CREATE DATABASE ${db} OWNER ${DB_USER}\"'"

    # Enable TimescaleDB
    run "ssh $PROD_SSH 'docker exec $PROD_CONTAINER psql -U $DB_USER ${db} -c \"CREATE EXTENSION IF NOT EXISTS timescaledb CASCADE;\"'"

    # pre_restore
    run "ssh $PROD_SSH 'docker exec $PROD_CONTAINER psql -U $DB_USER ${db} -c \"SELECT timescaledb_pre_restore();\"'"

    # Restore
    info "    pg_restore ${db} (ini mungkin butuh beberapa menit)..."
    run "ssh $PROD_SSH 'docker exec $PROD_CONTAINER pg_restore -U $DB_USER -d ${db} --no-owner --no-privileges ${dump_path} 2>&1 | grep -v \"already exists\" | grep -E \"error:|warning:|FATAL\" || true'"

    # post_restore
    run "ssh $PROD_SSH 'docker exec $PROD_CONTAINER psql -U $DB_USER ${db} -c \"SELECT timescaledb_post_restore();\"'"

    info "    ${db} restore selesai."
done

# ─── Step 6: Restart bot services di production ───────────────────────────────
info "Step 6/7: Restart bot services di production..."
run "ssh $PROD_SSH 'cd $PROD_PROJECT && docker compose start $BOT_SERVICES'"

# ─── Step 7: Verifikasi ───────────────────────────────────────────────────────
info "Step 7/7: Verifikasi row counts..."
if ! $DRY_RUN; then
    sleep 3
    for db in "${DATABASES[@]}"; do
        table="${VERIFY_TABLE[$db]:-}"
        if [[ -n "$table" ]]; then
            count=$(ssh "$PROD_SSH" "docker exec $PROD_CONTAINER psql -U $DB_USER $db -tAc 'SELECT COUNT(*) FROM $table;' 2>/dev/null || echo 'ERROR'")
            info "  ${db}.${table}: ${count} rows"
        fi
    done

    # Cek bot services running
    info ""
    info "Status production services:"
    ssh "$PROD_SSH" "docker ps --filter 'name=algovibe' --format 'table {{.Names}}\t{{.Status}}'"
fi

echo ""
echo "=================================================="
echo "  SYNC SELESAI"
echo "=================================================="
