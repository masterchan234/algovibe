#!/bin/bash
# migrate_history.sh
# Utilitas untuk Ekspor/Impor data OHLCV 90 hari Algovibe

DB_CONTAINER="algovibe-timescaledb"
DB_NAME="algovibe"
DB_USER="algovibe"
FILE_NAME="ohlcv_90d_dump.sql.gz"

show_help() {
    echo "Usage: ./migrate_history.sh [export|import]"
    echo "  export : Mengambil data dari database dan menyimpannya ke $FILE_NAME"
    echo "  import : Mengambil data dari $FILE_NAME dan memasukkannya ke database"
}

export_data() {
    echo "📦 Mengekspor data OHLCV (ini mungkin memakan waktu beberapa menit)..."
    docker exec $DB_CONTAINER pg_dump -U $DB_USER -d $DB_NAME -t ohlcv_1m | gzip > $FILE_NAME
    echo "✅ Ekspor selesai! File tersimpan di: $FILE_NAME"
    ls -lh $FILE_NAME
}

import_data() {
    if [ ! -f "$FILE_NAME" ]; then
        echo "❌ Error: File $FILE_NAME tidak ditemukan!"
        exit 1
    fi
    echo "📥 Mengimpor data OHLCV ke database baru..."
    gunzip -c $FILE_NAME | docker exec -i $DB_CONTAINER psql -U $DB_USER -d $DB_NAME
    echo "✅ Impor selesai! Data 90 hari Anda telah berpindah."
}

case "$1" in
    export) export_data ;;
    import) import_data ;;
    *) show_help ;;
esac
