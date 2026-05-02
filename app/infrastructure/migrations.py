import sqlite3
import os
from utils.logger import log_message

# Daftar migrasi terpusat
# Format: (nama_unik, sql_query)
MIGRATIONS = [
    ("create_migrations_table", """
        CREATE TABLE IF NOT EXISTS migrations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE,
            applied_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """),
    ("add_updated_at_to_dynamic_weights", "ALTER TABLE dynamic_weights ADD COLUMN updated_at DATETIME DEFAULT CURRENT_TIMESTAMP"),
    ("add_target_pct_to_positions", "ALTER TABLE positions ADD COLUMN target_tp_pct REAL DEFAULT 0.04"),
    ("add_target_sl_pct_to_positions", "ALTER TABLE positions ADD COLUMN target_sl_pct REAL DEFAULT -0.02"),
    ("add_ai_confidence_to_trade_history", "ALTER TABLE trade_history ADD COLUMN ai_confidence REAL DEFAULT 0.0"),
    ("add_signal_to_ticker_history", "ALTER TABLE ticker_history ADD COLUMN signal TEXT DEFAULT 'HOLD'"),
    ("add_momentum_to_sector_history", "ALTER TABLE sector_history ADD COLUMN momentum_score REAL DEFAULT 0.0"),
    ("add_leader_to_sector_history", "ALTER TABLE sector_history ADD COLUMN leader_pair TEXT DEFAULT 'N/A'"),
    ("add_leader_ch_to_sector_history", "ALTER TABLE sector_history ADD COLUMN leader_change REAL DEFAULT 0.0"),
    ("add_exec_type_to_trade_history", "ALTER TABLE trade_history ADD COLUMN exec_type TEXT DEFAULT 'MAKER'"),
    ("add_reason_backup_to_positions", "ALTER TABLE positions ADD COLUMN reason_backup TEXT"),
    ("add_trade_id_to_trade_history", "ALTER TABLE trade_history ADD COLUMN trade_id TEXT UNIQUE"),
    ("add_is_manual_to_trade_history", "ALTER TABLE trade_history ADD COLUMN is_manual INTEGER DEFAULT 0"),
    ("add_exit_order_id_to_trade_history", "ALTER TABLE trade_history ADD COLUMN exit_order_id TEXT"),
    ("add_unique_idx_exit_order_id", "CREATE UNIQUE INDEX IF NOT EXISTS idx_trade_exit_order_id ON trade_history(exit_order_id) WHERE exit_order_id IS NOT NULL"),
]

def run_migrations(db_path):
    """
    Menjalankan sistem migrasi otomatis untuk memastikan skema database 
    selalu up-to-date tanpa merusak data yang sudah ada.
    """
    if not os.path.exists(db_path):
        return

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    try:
        # 1. Pastikan tabel migrations ada terlebih dahulu (untuk bootstrap)
        cursor.execute(MIGRATIONS[0][1])
        conn.commit()

        # 2. Jalankan sisa migrasi
        for name, sql in MIGRATIONS[1:]:
            # Cek apakah migrasi sudah pernah dijalankan
            cursor.execute("SELECT 1 FROM migrations WHERE name = ?", (name,))
            if not cursor.fetchone():
                try:
                    cursor.execute(sql)
                    cursor.execute("INSERT INTO migrations (name) VALUES (?)", (name,))
                    conn.commit()
                    log_message(f"✅ Database Migration Applied: {name}")
                except sqlite3.OperationalError as e:
                    # Tangani kasus jika kolom sudah ada secara manual
                    err_lower = str(e).lower()
                    if "duplicate column name" in err_lower or "already exists" in err_lower or "cannot add" in err_lower:
                        cursor.execute("INSERT INTO migrations (name) VALUES (?)", (name,))
                        conn.commit()
                    else:
                        log_message(f"⚠️ Migration {name} skipped/failed: {e}")
    except Exception as e:
        log_message(f"❌ Critical Error during migrations: {e}")
    finally:
        conn.close()
