#!/bin/bash
# ==============================================================================
# Algovibe TimescaleDB Backup & Restore Tool
# Author: Senior Software Engineer / DevOps
# Language: Indonesian / Malay (Standardized Backend Logs & Comments)
# ==============================================================================

set -euo pipefail

# Konfigurasi Default (Bisa di-override lewat ENV)
DB_USER="algovibe"
DB_PASS="secretpassword"
CONTAINER_NAME="algovibe-timescaledb"
BACKUP_DIR="/root/algovibe/backups"
KEEP_DAYS=7
DEFAULT_DBS=("algovibe_live" "algovibe_dryrun" "algovibe")
LOG_FILE="${BACKUP_DIR}/db_backup_restore.log"

# Log Standardization (No Emojis, Categorical Tags)
log_message() {
    local level="$1"
    local message="$2"
    local timestamp
    timestamp=$(date '+%Y-%m-%d %H:%M:%S')
    local log_line="${timestamp} [${level}] ${message}"
    
    # Cetak ke stdout/stderr
    if [ "${level}" = "ERROR" ] || [ "${level}" = "WARN" ]; then
        echo "${log_line}" >&2
    else
        echo "${log_line}"
    fi

    # Tulis ke file log log jika direktori backup sudah ada
    if [ -d "${BACKUP_DIR}" ]; then
        echo "${log_line}" >> "${LOG_FILE}"
    fi
}

log_info() { log_message "INFO" "$1"; }
log_ok() { log_message "OK" "$1"; }
log_warn() { log_message "WARN" "$1"; }
log_error() { log_message "ERROR" "$1"; }
log_db() { log_message "DATABASE" "$1"; }

# 1. Ekstraksi otomatis dari docker-compose.yml jika file ada
extract_credentials() {
    local compose_file="/root/algovibe/docker-compose.yml"
    if [ -f "${compose_file}" ]; then
        local parse_user
        local parse_pass
        
        parse_user=$(grep -E 'POSTGRES_USER=' "${compose_file}" | head -n 1 | cut -d= -f2 | tr -d ' ' | tr -d '"' | tr -d "'" || true)
        parse_pass=$(grep -E 'POSTGRES_PASSWORD=' "${compose_file}" | head -n 1 | cut -d= -f2 | tr -d ' ' | tr -d '"' | tr -d "'" || true)
        
        if [ -n "${parse_user}" ]; then
            DB_USER="${parse_user}"
            log_info "Berhasil mengekstrak user database dari docker-compose.yml: ${DB_USER}"
        fi
        if [ -n "${parse_pass}" ]; then
            DB_PASS="${parse_pass}"
            log_info "Berhasil mengekstrak password database dari docker-compose.yml"
        fi
    fi
}

# 2. Cek dependensi Docker
check_docker() {
    if ! command -v docker &> /dev/null; then
        log_error "Docker tidak terinstall di host system. Proses dihentikan."
        exit 1
    fi
}

# 3. Cek status dan kesehatan container database
check_container() {
    check_docker
    
    # Cek apakah container terdaftar
    if ! docker ps -a --format '{{.Names}}' | grep -Eq "^${CONTAINER_NAME}$"; then
        log_error "Container ${CONTAINER_NAME} tidak terdaftar di sistem. Pastikan stack sudah dideploy."
        exit 1
    fi

    # Cek apakah container sedang berjalan
    if ! docker ps --format '{{.Names}}' | grep -Eq "^${CONTAINER_NAME}$"; then
        log_warn "Container ${CONTAINER_NAME} tidak sedang berjalan."
        log_info "Mencoba menyalakan service database menggunakan docker compose..."
        
        # Masuk ke direktori kerja algovibe untuk compose
        if cd /root/algovibe && docker compose up -d timescaledb; then
            log_info "Menunggu database agar siap menerima koneksi..."
            sleep 3
        else
            log_error "Gagal menyalakan container database ${CONTAINER_NAME}."
            exit 1
        fi
    fi

    # Cek health check status
    local status
    status=$(docker inspect --format='{{.State.Health.Status}}' "${CONTAINER_NAME}" 2>/dev/null || echo "unknown")
    if [ "${status}" != "healthy" ]; then
        log_warn "Status container ${CONTAINER_NAME} saat ini: ${status}. Menunggu hingga healthy..."
        
        # Loop menunggu status healthy (max 60 detik)
        for i in {1..12}; do
            sleep 5
            status=$(docker inspect --format='{{.State.Health.Status}}' "${CONTAINER_NAME}" 2>/dev/null || echo "unknown")
            if [ "${status}" = "healthy" ]; then
                log_ok "Container ${CONTAINER_NAME} telah berstatus HEALTHY."
                return 0
            fi
            log_info "Menunggu container healthy... ($((i*5))s/60s)"
        done
        
        # Fallback manual ping pg_isready jika health status tidak diset dengan benar
        log_warn "Status container belum healthy secara formal. Mencoba verifikasi langsung menggunakan pg_isready..."
        if docker exec -t "${CONTAINER_NAME}" pg_isready -U "${DB_USER}" -d postgres >/dev/null 2>&1; then
            log_ok "Verifikasi langsung pg_isready sukses. Database siap digunakan."
        else
            log_error "Container database tidak merespon koneksi (pg_isready gagal)."
            exit 1
        fi
    else
        log_ok "Verifikasi container ${CONTAINER_NAME}: RUNNING & HEALTHY"
    fi
}

# 4. Fungsi menjalankan Backup
run_backup() {
    local target_db="$1"
    
    # Inisialisasi folder backup
    mkdir -p "${BACKUP_DIR}"
    
    extract_credentials
    check_container

    local dbs_to_backup=()
    if [ "${target_db}" = "all" ]; then
        dbs_to_backup=("${DEFAULT_DBS[@]}")
    else
        # Validasi nama DB target
        local valid=false
        for db in "${DEFAULT_DBS[@]}"; do
            if [ "${db}" = "${target_db}" ]; then
                valid=true
                break
            fi
        done
        if [ "${valid}" = "false" ]; then
            log_warn "Database '${target_db}' bukan database default (${DEFAULT_DBS[*]}). Melanjutkan backup kustom..."
        fi
        dbs_to_backup=("${target_db}")
    fi

    local timestamp
    timestamp=$(date '+%Y%m%d_%H%M%S')

    for db in "${dbs_to_backup[@]}"; do
        local backup_file="${BACKUP_DIR}/${db}_${timestamp}.dump"
        log_db "Memulai backup untuk database: ${db}"
        
        # Jalankan pg_dump dengan format Custom (-Fc)
        # Format custom ini dikompresi secara internal, menyertakan metadata skema lengkap,
        # dan sangat fleksibel saat direstore (bisa direstore sebagian / multi-threaded)
        if docker exec -i -e PGPASSWORD="${DB_PASS}" "${CONTAINER_NAME}" \
            pg_dump -U "${DB_USER}" -h localhost -F c -d "${db}" > "${backup_file}"; then
            
            local file_size
            file_size=$(du -sh "${backup_file}" | cut -f1)
            log_ok "Backup database ${db} berhasil diselesaikan."
            log_info "Detail File: ${backup_file} (${file_size})"
        else
            log_error "Gagal melakukan backup untuk database: ${db}"
            rm -f "${backup_file}"
            exit 1
        fi
    done

    # Rotasi file backup lama
    cleanup_old_backups
}

# 5. Fungsi Rotasi / Pembersihan Backup Lama
cleanup_old_backups() {
    log_info "Memulai evaluasi kebijakan rotasi backup (Pembersihan file > ${KEEP_DAYS} hari)..."
    local count=0
    
    # Temukan file backup .dump yang berumur lebih dari KEEP_DAYS hari
    while IFS= read -r file; do
        if [ -n "${file}" ] && [ -f "${file}" ]; then
            log_info "Menghapus file backup usang: $(basename "${file}")"
            rm -f "${file}"
            count=$((count + 1))
        fi
    done < <(find "${BACKUP_DIR}" -name "*.dump" -type f -mtime +"${KEEP_DAYS}" 2>/dev/null || true)

    if [ "${count}" -gt 0 ]; then
        log_ok "Rotasi backup selesai: Berhasil menghapus ${count} file backup usang."
    else
        log_info "Rotasi backup selesai: Tidak ada file backup yang melampaui retensi ${KEEP_DAYS} hari."
    fi
}

# 6. Menampilkan Daftar Backup yang Tersedia
list_backups() {
    log_info "Daftar file backup yang tersedia di ${BACKUP_DIR}:"
    if [ -d "${BACKUP_DIR}" ]; then
        local file_count
        file_count=$(find "${BACKUP_DIR}" -name "*.dump" -type f | wc -l)
        if [ "${file_count}" -eq 0 ]; then
            log_info "Tidak ditemukan file backup (.dump)."
        else
            ls -lh "${BACKUP_DIR}"/*.dump
            log_ok "Ditemukan ${file_count} file backup."
        fi
    else
        log_warn "Direktori backup tidak ditemukan di ${BACKUP_DIR}."
    fi
}

# 7. Fungsi menjalankan Restore Database
run_restore() {
    local backup_file="$1"
    local target_db="$2"
    local force_yes="${3:-false}"

    # Validasi keberadaan file
    if [ ! -f "${backup_file}" ]; then
        log_error "File backup tidak ditemukan: ${backup_file}"
        exit 1
    fi

    extract_credentials
    check_container

    # Konfirmasi keselamatan data sensitif (khususnya live database)
    if [ "${force_yes}" != "true" ]; then
        log_warn "─────────────────────────────────────────────────────────────────"
        log_warn " PERINGATAN KESELAMATAN DATA (RESTORE ACTION)"
        log_warn " target DB   : ${target_db}"
        log_warn " backup file : $(basename "${backup_file}")"
        log_warn " proses ini akan MENGHAPUS & MENULIS ULANG database '${target_db}'!"
        log_warn "─────────────────────────────────────────────────────────────────"
        
        # Jika database live, berikan penekanan ekstra
        if [ "${target_db}" = "algovibe_live" ]; then
            log_warn " [CRITICAL WARNING] DATABASE LIVE AKAN TERHAPUS!"
            read -p "Ketik '${target_db}' untuk melanjutkan proses restore: " -r confirmation
            if [ "${confirmation}" != "${target_db}" ]; then
                log_info "Konfirmasi tidak cocok. Proses restore dibatalkan."
                exit 0
            fi
        else
            read -p "Apakah Anda yakin ingin melanjutkan? (y/N): " -n 1 -r
            echo
            if [[ ! $REPLY =~ ^[Yy]$ ]]; then
                log_info "Proses restore dibatalkan oleh pengguna."
                exit 0
            fi
        fi
    fi

    log_db "Memulai prosedur pemulihan (restore) database '${target_db}'..."

    # Langkah 1: Putuskan semua koneksi aktif ke database target agar bisa di-drop
    log_info "Memutuskan sesi koneksi aktif pada database '${target_db}'..."
    docker exec -i -e PGPASSWORD="${DB_PASS}" "${CONTAINER_NAME}" psql -U "${DB_USER}" -d postgres -c \
        "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = '${target_db}' AND pid <> pg_backend_pid();" > /dev/null 2>&1 || true

    # Langkah 2: Drop database lama
    log_info "Menghapus (drop) database lama '${target_db}'..."
    docker exec -i -e PGPASSWORD="${DB_PASS}" "${CONTAINER_NAME}" psql -U "${DB_USER}" -d postgres -c \
        "DROP DATABASE IF EXISTS ${target_db};" || {
            log_error "Gagal menghapus database '${target_db}'. Pastikan tidak ada service yang memegang lock."
            exit 1
        }

    # Langkah 3: Buat kembali database baru
    log_info "Membuat ulang database baru '${target_db}'..."
    docker exec -i -e PGPASSWORD="${DB_PASS}" "${CONTAINER_NAME}" psql -U "${DB_USER}" -d postgres -c \
        "CREATE DATABASE ${target_db};" || {
            log_error "Gagal membuat database baru '${target_db}'."
            exit 1
        }

    # Langkah 4: Aktifkan ekstensi TimescaleDB (Wajib diaktifkan sebelum restore hypertable)
    log_info "Mengaktifkan ekstensi TimescaleDB di database '${target_db}'..."
    docker exec -i -e PGPASSWORD="${DB_PASS}" "${CONTAINER_NAME}" psql -U "${DB_USER}" -d "${target_db}" -c \
        "CREATE EXTENSION IF NOT EXISTS timescaledb CASCADE;" || {
            log_error "Gagal mengaktifkan TimescaleDB di database '${target_db}'."
            exit 1
        }

    # Langkah 5: Jalankan pg_restore dari file dump custom
    log_info "Menyalurkan data skema & record dari file dump menggunakan pg_restore..."
    # --no-owner: mengabaikan penetapan kepemilikan objek database asal agar tidak bentrok dengan hak akses lokal
    if docker exec -i -e PGPASSWORD="${DB_PASS}" "${CONTAINER_NAME}" \
        pg_restore -U "${DB_USER}" -d "${target_db}" --no-owner < "${backup_file}"; then
        log_ok "Restore database '${target_db}' berhasil diselesaikan tanpa kendala."
    else
        # pg_restore terkadang keluar dengan exit code bukan 0 untuk peringatan kecil (warnings).
        # Kita lakukan langkah validasi akhir untuk memastikan integritas database.
        log_warn "pg_restore melaporkan beberapa warning selama proses pemulihan. Memulai verifikasi database..."
        
        local check_conn
        check_conn=$(docker exec -i -e PGPASSWORD="${DB_PASS}" "${CONTAINER_NAME}" psql -U "${DB_USER}" -d "${target_db}" -t -A -c "SELECT 1;" 2>/dev/null || echo "failed")
        
        if [ "${check_conn}" = "1" ]; then
            # Ambil jumlah tabel yang berhasil terbuat
            local table_count
            table_count=$(docker exec -i -e PGPASSWORD="${DB_PASS}" "${CONTAINER_NAME}" psql -U "${DB_USER}" -d "${target_db}" -t -A -c \
                "SELECT count(*) FROM information_schema.tables WHERE table_schema = 'public';" 2>/dev/null || echo "0")
            
            log_ok "Verifikasi Sukses: Database '${target_db}' dapat diakses dan memuat ${table_count} tabel."
        else
            log_error "Verifikasi Gagal: Database '${target_db}' tidak dapat dihubungi pasca restore."
            exit 1
        fi
    fi
}

# 8. Menampilkan Bantuan Penggunaan
show_usage() {
    cat << EOF
================================================================================
Algovibe Database Backup & Restore Utility
================================================================================
Penggunaan: $0 <perintah> [argumen]

Perintah Utama:
  backup <db_name|all>        Membuat snapshot database. Pilihan db_name:
                              - algovibe_live     (Database Live Trading)
                              - algovibe_dryrun   (Database Sandbox/Dry-Run)
                              - algovibe          (Database Backtest)
                              - all               (Backup seluruh database di atas)

  restore <file_path> <db>    Memulihkan database target dari file dump (.dump).
                              Contoh target db: algovibe_live, algovibe_dryrun.
                              Tambahkan argumen ketiga '-y' atau '--yes' untuk
                              melewati prompt interaktif (otomatisasi).

  list                        Melihat daftar backup yang tersedia di folder backup.

  help                        Menampilkan pesan dokumentasi bantuan ini.

Contoh Praktis:
  $0 backup all
  $0 backup algovibe_live
  $0 restore ${BACKUP_DIR}/algovibe_dryrun_20260518_060000.dump algovibe_dryrun
  $0 restore ${BACKUP_DIR}/algovibe_live_20260518_060000.dump algovibe_live --yes
================================================================================
EOF
}

# 9. Main Router
main() {
    if [ $# -lt 1 ]; then
        show_usage
        exit 1
    fi

    local cmd="$1"
    shift

    case "${cmd}" in
        backup)
            if [ $# -lt 1 ]; then
                log_error "Parameter tidak lengkap! Masukkan nama database yang ingin dibackup (all / algovibe_live / algovibe_dryrun / algovibe)."
                exit 1
            fi
            run_backup "$1"
            ;;
        restore)
            if [ $# -lt 2 ]; then
                log_error "Parameter tidak lengkap! Format: $0 restore <lokasi_file_backup> <database_target> [--yes]"
                exit 1
            fi
            local file="$1"
            local db="$2"
            local force=false
            if [ $# -ge 3 ] && { [ "$3" = "-y" ] || [ "$3" = "--yes" ]; }; then
                force=true
            fi
            run_restore "${file}" "${db}" "${force}"
            ;;
        list)
            list_backups
            ;;
        help|--help|-h)
            show_usage
            ;;
        *)
            log_error "Perintah tidak dikenali: '${cmd}'"
            show_usage
            exit 1
            ;;
    esac
}

main "$@"
