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
    ("create_positions", """
        CREATE TABLE IF NOT EXISTS positions (
            pair TEXT PRIMARY KEY,
            entry_price REAL,
            amount_crypto REAL,
            amount_idr REAL,
            status TEXT,
            entry_order_id TEXT,
            exit_order_id TEXT,
            timestamp DATETIME,
            dry_run BOOLEAN,
            target_tp_pct REAL DEFAULT 0.05,
            target_sl_pct REAL DEFAULT -0.02,
            trailing_sl_price REAL DEFAULT 0.0,
            highest_price REAL DEFAULT 0.0,
            reason_backup TEXT,
            ai_confidence REAL DEFAULT 0.0,
            dca_tier INTEGER DEFAULT 0
        )
    """),
    ("create_market_depth", """
        CREATE TABLE IF NOT EXISTS market_depth (
            pair TEXT,
            price REAL,
            vol REAL,
            is_bid INTEGER
        )
    """),
    ("create_trade_history", """
        CREATE TABLE IF NOT EXISTS trade_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            pair TEXT,
            entry_price REAL,
            exit_price REAL,
            amount_crypto REAL,
            amount_idr REAL,
            pnl_idr REAL,
            pnl_pct REAL,
            reason TEXT,
            exec_type TEXT DEFAULT 'MAKER',
            duration_seconds INTEGER,
            dry_run BOOLEAN,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """),
    ("create_ticker_history", """
        CREATE TABLE IF NOT EXISTS ticker_history (
            pair TEXT PRIMARY KEY,
            history_json TEXT,
            prediction_prob REAL DEFAULT 0.0,
            trend TEXT DEFAULT 'N/A',
            signal TEXT DEFAULT 'HOLD',
            ch_5m REAL DEFAULT 0.0,
            ch_15m REAL DEFAULT 0.0,
            ch_1h REAL DEFAULT 0.0,
            volatility REAL DEFAULT 0.0,
            score REAL DEFAULT 0.0,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """),
    ("create_bot_state", """
        CREATE TABLE IF NOT EXISTS bot_state (
            key TEXT PRIMARY KEY,
            value TEXT,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """),
    ("create_trade_analytics", """
        CREATE TABLE IF NOT EXISTS trade_analytics (
            pair TEXT,
            entry_timestamp DATETIME,
            ai_prob REAL,
            whale_boost REAL,
            sector_sentiment REAL,
            imbalance REAL,
            volatility REAL,
            eta_minutes INTEGER DEFAULT 15,
            llm_verdict TEXT DEFAULT 'HOLD',
            llm_confidence REAL DEFAULT 0.0,
            llm_reason TEXT DEFAULT '',
            dry_run BOOLEAN DEFAULT 1,
            pnl_idr REAL,
            status TEXT DEFAULT 'OPEN'
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
    ("add_eta_to_analytics", "ALTER TABLE trade_analytics ADD COLUMN eta_minutes INTEGER DEFAULT 15"),
    ("add_verdict_to_analytics", "ALTER TABLE trade_analytics ADD COLUMN llm_verdict TEXT DEFAULT 'HOLD'"),
    ("add_confidence_to_analytics", "ALTER TABLE trade_analytics ADD COLUMN llm_confidence REAL DEFAULT 0.0"),
    ("add_reason_to_analytics", "ALTER TABLE trade_analytics ADD COLUMN llm_reason TEXT DEFAULT ''"),
    ("add_dry_run_to_analytics", "ALTER TABLE trade_analytics ADD COLUMN dry_run BOOLEAN DEFAULT 1"),
    ("create_order_logs", """
        CREATE TABLE IF NOT EXISTS order_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            pair TEXT,
            side TEXT,
            price REAL,
            amount REAL,
            status TEXT,
            response_json TEXT,
            error_msg TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """),
    ("create_system_events", """
        CREATE TABLE IF NOT EXISTS system_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            pair TEXT,
            type TEXT,
            message TEXT,
            level TEXT DEFAULT 'INFO',
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """),
    ("create_blacklist", """
        CREATE TABLE IF NOT EXISTS blacklist (
            pair TEXT PRIMARY KEY,
            reason TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """),
    ("create_users", """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE,
            password_hash TEXT,
            api_key TEXT,
            api_secret TEXT,
            role TEXT DEFAULT 'USER',
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """),
    ("create_sector_history", """
        CREATE TABLE IF NOT EXISTS sector_history (
            sector TEXT,
            sentiment_score REAL,
            momentum_score REAL DEFAULT 0.0,
            leader_pair TEXT DEFAULT 'N/A',
            leader_change REAL DEFAULT 0.0,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """),
    ("create_shadow_orders", """
        CREATE TABLE IF NOT EXISTS shadow_orders (
            pair TEXT PRIMARY KEY,
            order_id TEXT,
            price REAL,
            amount REAL,
            side TEXT DEFAULT 'BUY',
            status TEXT DEFAULT 'OPEN',
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """),
    ("create_dynamic_weights", """
        CREATE TABLE IF NOT EXISTS dynamic_weights (
            feature_name TEXT PRIMARY KEY,
            weight_value REAL DEFAULT 1.0,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """),
    ("create_account_balances", """
        CREATE TABLE IF NOT EXISTS account_balances (
            asset TEXT PRIMARY KEY,
            free REAL DEFAULT 0.0,
            used REAL DEFAULT 0.0,
            total REAL DEFAULT 0.0,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """),
    ("create_idx_events_pair", "CREATE INDEX IF NOT EXISTS idx_events_pair ON system_events(pair)"),
    ("create_idx_events_time", "CREATE INDEX IF NOT EXISTS idx_events_time ON system_events(created_at)"),
    ("create_idx_depth_pair", "CREATE INDEX IF NOT EXISTS idx_depth_pair ON market_depth(pair)"),
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
